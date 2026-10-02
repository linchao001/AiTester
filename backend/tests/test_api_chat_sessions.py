from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from aitester.adapters.llm import MockProvider
from aitester.config import Settings
from aitester.main import create_app
from aitester.services import ChatService
from aitester.services.session_store import SessionStore


# 与 test_api_projects.py:12 同款假 manager：会话端点不碰 reme，免起真实实例
class _NoopKbManager:
    # send 装配工具注册表时 PrepareKbWriteTool 要读该属性（零写盘，给个假根即可）
    kb_root_dir = "kb-root"

    def start(self):
        pass

    def close_all(self, timeout: float = 30.0):
        pass

    async def run_job(self, name, *, project_id="default", agent_id="console", **kwargs):
        return SimpleNamespace(success=True, answer="ok", metadata={})

    def run_job_sync(self, name, *, project_id="default", agent_id="console", **kwargs):
        return SimpleNamespace(success=True, answer="ok", metadata={})


def _app(tmp_path: Path):
    return create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        projects_path=tmp_path / "p.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        kb_manager=_NoopKbManager(),
    )


def _client(tmp_path: Path) -> TestClient:
    return TestClient(_app(tmp_path))


def _seed(tmp_path: Path, agent_id: str = "case_design") -> tuple[TestClient, str]:
    application = _app(tmp_path)
    store: SessionStore = application.state.sessions
    sid = store.new_id()
    store.create(sid, agent_id, "订单退款用例设计")
    store.append(sid, "user", "订单退款用例设计")
    store.append(sid, "assistant", "好的",
                 steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}"}])
    return TestClient(application), sid


def _wired_client(tmp_path: Path) -> TestClient:
    """用真实 ChatService（注入 MockProvider，零网络）验 send 的会话契约。"""
    application = _app(tmp_path)
    application.state.chat_service = ChatService(
        provider=MockProvider(),
        agent_runtime=application.state.agent_runtime,
        sessions=application.state.sessions,
    )
    return TestClient(application)


def test_list_sessions_returns_rows_for_agent(tmp_path: Path) -> None:
    client, sid = _seed(tmp_path)
    body = client.get("/api/chat/sessions", params={"agent_id": "case_design"}).json()
    assert len(body["sessions"]) == 1
    row = body["sessions"][0]
    assert row["id"] == sid and row["title"] == "订单退款用例设计"
    assert row["message_count"] == 2
    assert isinstance(row["created_at"], int) and isinstance(row["updated_at"], int)


def test_list_sessions_is_empty_for_unknown_or_platform_agent(tmp_path: Path) -> None:
    client, _ = _seed(tmp_path)
    for agent in ("ghost", "kb_assistant"):
        r = client.get("/api/chat/sessions", params={"agent_id": agent})
        assert r.status_code == 200
        assert r.json() == {"sessions": []}  # 不泄露、不报错（spec 契约）


def test_list_sessions_requires_agent_id(tmp_path: Path) -> None:
    client, _ = _seed(tmp_path)
    assert client.get("/api/chat/sessions").status_code == 422


def test_messages_endpoint_returns_steps(tmp_path: Path) -> None:
    client, sid = _seed(tmp_path)
    body = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert body["session_id"] == sid
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["steps"] is None
    assert body["messages"][1]["steps"] == [
        {"tool": "read", "ok": True, "round": 1, "detail": "{}"}]


def test_messages_unknown_or_temporary_id_returns_404(tmp_path: Path) -> None:
    client, _ = _seed(tmp_path)
    for bad in ("sess_deadbeef", "kb-console"):
        r = client.get(f"/api/chat/sessions/{bad}/messages")
        assert r.status_code == 404
        assert r.json()["detail"] == "会话不存在或已被删除"


def test_delete_returns_204_and_removes_everything(tmp_path: Path) -> None:
    client, sid = _seed(tmp_path)
    assert client.delete(f"/api/chat/sessions/{sid}").status_code == 204
    assert client.delete(f"/api/chat/sessions/{sid}").json()["detail"] == "会话不存在或已被删除"
    assert client.get("/api/chat/sessions", params={"agent_id": "case_design"}).json() == {"sessions": []}
    assert not (tmp_path / "sessions" / f"{sid}.jsonl").exists()


def test_send_defaults_to_empty_session_id(tmp_path: Path) -> None:
    client = _wired_client(tmp_path)
    r = client.post("/api/chat/send", json={"message": "生成用例", "agent_id": "case_design"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"].startswith("sess_")   # 请求体不带 session_id 也能建会话
    assert body["title"] == "生成用例"
    assert body["steps"] == []                       # MockProvider 不调工具
    listed = client.get("/api/chat/sessions", params={"agent_id": "case_design"}).json()["sessions"]
    assert [s["id"] for s in listed] == [body["session_id"]]


def test_send_with_temporary_key_does_not_appear_in_list(tmp_path: Path) -> None:
    client = _wired_client(tmp_path)
    r = client.post("/api/chat/send",
                    json={"session_id": "kb-console", "message": "hi", "agent_id": "case_design"})
    assert r.status_code == 200
    assert r.json()["session_id"] == "kb-console"   # 临时键原样回显
    assert client.get("/api/chat/sessions", params={"agent_id": "case_design"}).json() == {"sessions": []}


def test_send_unknown_session_id_returns_404(tmp_path: Path) -> None:
    client = _wired_client(tmp_path)
    r = client.post("/api/chat/send",
                    json={"session_id": "sess_deadbeef", "message": "hi", "agent_id": "case_design"})
    assert r.status_code == 404
    assert r.json()["detail"] == "会话不存在或已被删除"


def test_messages_drops_malformed_persisted_steps(tmp_path: Path) -> None:
    # 裁定 3：手工编辑/旧格式的落盘 steps 行只丢痕迹，不整响应 500（容错仅在读路径）
    # 覆盖两类畸形：非 dict 元素（"x"/null，落盘即 JSON 字符串/字面量 null）与缺必填键的 dict
    client, sid = _seed(tmp_path)
    client.app.state.sessions.append(
        sid, "assistant", "混合痕迹",
        steps=["x", {"tool": "read"}, None,
               {"tool": "grep", "ok": False, "round": 2, "detail": "x"}])
    r = client.get(f"/api/chat/sessions/{sid}/messages")
    assert r.status_code == 200
    assert r.json()["messages"][-1]["steps"] == [
        {"tool": "grep", "ok": False, "round": 2, "detail": "x"}]
