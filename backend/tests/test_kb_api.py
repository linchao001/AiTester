from types import SimpleNamespace

from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app
from aitester.services.kb.manager import KbUnavailableError


class _RecordingKbManager:
    def __init__(self, exc=None):
        self.calls: list[tuple[str, dict]] = []
        self.exc = exc

    def start(self):
        pass

    def close_all(self, timeout: float = 30.0):
        pass

    async def run_job(self, name, *, project_id="default", agent_id="console", **kwargs):
        if self.exc is not None:
            raise self.exc
        self.calls.append((name, kwargs))
        return SimpleNamespace(success=True, answer="ok", metadata={"echo": name})


def _client(tmp_path, kb):
    app = create_app(
        model_config_path=tmp_path / "models.json",
        capability_config_path=tmp_path / "caps.json",
        settings=Settings(_env_file=None, kb_embedding_api_key=""),
        kb_manager=kb,
    )
    return TestClient(app)


def test_kb_search_dispatches_job(tmp_path):
    kb = _RecordingKbManager()
    with _client(tmp_path, kb) as client:
        r = client.post("/api/kb/search", json={"query": "热词", "limit": 3, "bucket": "business"})
    assert r.status_code == 200
    assert r.json() == {"success": True, "answer": "ok", "metadata": {"echo": "knowledge_search"}}
    assert kb.calls == [("knowledge_search", {"query": "热词", "limit": 3, "bucket": "business"})]


def test_kb_save_and_inbox_routes(tmp_path):
    kb = _RecordingKbManager()
    with _client(tmp_path, kb) as client:
        assert client.post("/api/kb/save", json={"title": "t", "content": "c"}).status_code == 200
        assert client.get("/api/kb/inbox").status_code == 200
        assert client.post("/api/kb/inbox/promote", json={"stem": "s"}).status_code == 200
    # 终审裁定：save 端点只派发 save job，不再同步补跑 reindex（后台环收敛）
    assert kb.calls[0] == ("save_to_knowledge", {"title": "t", "content": "c", "bucket": "business/wiki"})
    assert kb.calls[1] == ("list_knowledge_inbox", {})
    assert kb.calls[2] == ("promote_knowledge_inbox", {"stem": "s"})


def test_kb_inbox_merge_and_reject(tmp_path):
    kb = _RecordingKbManager()
    with _client(tmp_path, kb) as client:
        assert client.post("/api/kb/inbox/merge", json={"stem": "s"}).status_code == 200
        assert client.post(
            "/api/kb/inbox/reject", json={"stem": "s2"}
        ).status_code == 200
    assert kb.calls[0] == (
        "merge_knowledge_inbox",
        {"stem": "s", "target_path": "", "mode": "REFINE"},
    )
    assert kb.calls[1] == ("reject_knowledge_inbox", {"stem": "s2"})


def test_kb_status_unavailable_maps_503(tmp_path):
    kb = _RecordingKbManager(exc=KbUnavailableError("知识库未启用或未启动"))
    with _client(tmp_path, kb) as client:
        r = client.get("/api/kb/status")
    assert r.status_code == 503


def test_kb_get_bases(tmp_path):
    kb = _RecordingKbManager()
    with _client(tmp_path, kb) as client:
        assert client.get("/api/kb/bases").status_code == 200
    assert kb.calls == [("list_knowledge_bases", {})]


def test_chat_send_returns_drafts(tmp_path):
    app = create_app(
        model_config_path=tmp_path / "m.json", capability_config_path=tmp_path / "c.json",
        settings=Settings(_env_file=None), kb_manager=_RecordingKbManager())
    app.state.chat_service = SimpleNamespace(send=lambda sid, msg, aid: {
        "reply": "r", "trace": ["services"], "model": "m",
        "drafts": [{"op": "create", "path": "a.md", "abs_display": "P",
                     "summary": "s", "content": "c", "base": None, "mtime": 0}]})
    with TestClient(app) as c:
        j = c.post("/api/chat/send", json={"message": "写点什么"}).json()
    assert j["drafts"][0]["path"] == "a.md"


def test_chat_send_drafts_defaults_empty(tmp_path):
    app = create_app(
        model_config_path=tmp_path / "m.json", capability_config_path=tmp_path / "c.json",
        settings=Settings(_env_file=None), kb_manager=_RecordingKbManager())
    app.state.chat_service = SimpleNamespace(send=lambda sid, msg, aid: {
        "reply": "r", "trace": ["services"], "model": "m"})  # 旧形态返回：无 drafts 键
    with TestClient(app) as c:
        j = c.post("/api/chat/send", json={"message": "echo 我"}).json()
    assert j["drafts"] == []  # 向后兼容：SendResponse 默认空列表


def test_chat_send_skips_malformed_drafts(tmp_path):
    # 终审项 5：缺必填键/非 dict 的畸形草案逐条跳过，不整响应 500，回复不丢
    app = create_app(
        model_config_path=tmp_path / "m.json", capability_config_path=tmp_path / "c.json",
        settings=Settings(_env_file=None), kb_manager=_RecordingKbManager())
    valid = {"op": "create", "path": "a.md", "abs_display": "P",
             "summary": "s", "content": "c", "base": None, "mtime": 0}
    app.state.chat_service = SimpleNamespace(send=lambda sid, msg, aid: {
        "reply": "回复还在", "trace": ["services"], "model": "m",
        "drafts": [valid, {"op": "create"}, "garbage", None]})
    with TestClient(app) as c:
        r = c.post("/api/chat/send", json={"message": "写点什么"})
    assert r.status_code == 200
    j = r.json()
    assert j["reply"] == "回复还在"
    assert [d["path"] for d in j["drafts"]] == ["a.md"]  # 仅合法草案存活
