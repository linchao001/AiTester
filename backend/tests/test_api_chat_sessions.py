from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aitester.adapters.llm import MockProvider
from aitester.config import Settings
from aitester.main import create_app
from aitester.services import ChatService
from aitester.services.session_locator import SessionLocator
from streaming_fakes import sse_frames


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
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        memory_manager=_NoopKbManager(),
    )


def _send_body(client, payload: dict) -> dict:
    """POST /api/chat/send/stream 折成 done 载荷 dict（与 test_api._stream_reply 同思路）：
    一次性端点删除后，既有 body["session_id"]/["title"]/["steps"] 断言原样吃终态。"""
    with client.stream("POST", "/api/chat/send/stream", json=payload) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8")
        frames = sse_frames(resp)
        last = frames[-1] if frames else ("", {})
        return dict(last[1]) if last[0] == "done" else {}


def _seed_project(application, tmp_path: Path, name: str = "订单系统") -> str:
    """可见智能体的 send 必须有真实可达的项目。"""
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    return application.state.project_config.create(
        name=name, desc="", dir_=str(root), agents=["case_design"]
    )["id"]


def _seed(tmp_path: Path, agent_id: str = "case_design") -> tuple[TestClient, str, str, Path]:
    application = _app(tmp_path)
    pid = _seed_project(application, tmp_path)
    root = Path(application.state.project_config.get(pid)["dir"])
    store = application.state.sessions.for_agent(pid, agent_id)
    sid = store.new_id()
    store.create(sid, agent_id, pid, "订单退款用例设计")
    store.append(sid, "user", "订单退款用例设计")
    store.append(sid, "assistant", "好的",
                 steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}", "result": "FILE"}])
    return TestClient(application), sid, pid, root


def _wired_app(tmp_path: Path):
    """create_app 缝装配真实 ChatService（注入 MockProvider，零网络），返回 app 以便先种会话。"""
    application = _app(tmp_path)
    application.state.chat_service = ChatService(
        provider=MockProvider(),
        agent_runtime=application.state.agent_runtime,
        sessions=application.state.sessions,
        projects=application.state.project_config,
    )
    return application


def _wired_client(tmp_path: Path) -> TestClient:
    return TestClient(_wired_app(tmp_path))


def _msg_params(agent_id: str, project_id: str) -> dict[str, str]:
    return {"agent_id": agent_id, "project_id": project_id}


def test_list_requires_project_id(tmp_path) -> None:
    client = TestClient(_app(tmp_path))
    r = client.get("/api/chat/sessions", params={"agent_id": "case_design"})
    assert r.status_code == 422


def test_list_filters_by_project(tmp_path) -> None:
    application = _app(tmp_path)
    pid_a = _seed_project(application, tmp_path, "项目甲")
    pid_b = _seed_project(application, tmp_path, "项目乙")
    loc: SessionLocator = application.state.sessions
    a = loc.for_agent(pid_a, "case_design")
    b = loc.for_agent(pid_b, "case_design")
    sid_a = a.new_id()
    sid_b = b.new_id()
    a.create(sid_a, "case_design", pid_a, "本项目")
    b.create(sid_b, "case_design", pid_b, "别的项目")
    client = TestClient(application)
    rows = client.get(
        "/api/chat/sessions",
        params={"agent_id": "case_design", "project_id": pid_a},
    ).json()["sessions"]
    assert [r["id"] for r in rows] == [sid_a]
    assert rows[0]["project_id"] == pid_a


def test_list_sessions_returns_rows_for_agent(tmp_path: Path) -> None:
    client, sid, pid, _ = _seed(tmp_path)
    body = client.get("/api/chat/sessions",
                      params={"agent_id": "case_design", "project_id": pid}).json()
    assert len(body["sessions"]) == 1
    row = body["sessions"][0]
    assert row["id"] == sid and row["title"] == "订单退款用例设计"
    assert row["project_id"] == pid
    assert row["message_count"] == 2


def test_list_sessions_is_empty_for_unknown_or_platform_agent(tmp_path: Path) -> None:
    client, _, pid, _ = _seed(tmp_path)
    for agent in ("ghost", "kb_assistant"):
        r = client.get("/api/chat/sessions",
                       params={"agent_id": agent, "project_id": pid})
        assert r.status_code == 200
        assert r.json() == {"sessions": []}


def test_list_sessions_requires_agent_id(tmp_path: Path) -> None:
    client, _, _, _ = _seed(tmp_path)
    assert client.get("/api/chat/sessions").status_code == 422


def test_messages_require_project_and_agent_query(tmp_path: Path) -> None:
    client, sid, _pid, _ = _seed(tmp_path)
    assert client.get(f"/api/chat/sessions/{sid}/messages").status_code == 422
    assert client.delete(f"/api/chat/sessions/{sid}").status_code == 422


def test_messages_endpoint_returns_steps_without_result(tmp_path: Path) -> None:
    client, sid, pid, root = _seed(tmp_path)
    body = client.get(
        f"/api/chat/sessions/{sid}/messages",
        params=_msg_params("case_design", pid),
    ).json()
    assert body["session_id"] == sid
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["steps"] is None
    step = body["messages"][1]["steps"][0]
    assert step == {"tool": "read", "ok": True, "round": 1, "detail": "{}"}
    assert "result" not in step
    # 磁盘仍有全量 result
    disk = (
        root / ".AiTester" / "session_history" / "case_design" / f"{sid}.jsonl"
    ).read_text(encoding="utf-8")
    assert "FILE" in disk


def test_messages_unknown_or_temporary_id_returns_404(tmp_path: Path) -> None:
    client, _, pid, _ = _seed(tmp_path)
    for bad in ("sess_deadbeef", "kb-console"):
        r = client.get(
            f"/api/chat/sessions/{bad}/messages",
            params=_msg_params("case_design", pid),
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "会话不存在或已被删除"


def test_delete_returns_204_and_removes_everything(tmp_path: Path) -> None:
    client, sid, pid, root = _seed(tmp_path)
    params = _msg_params("case_design", pid)
    assert client.delete(f"/api/chat/sessions/{sid}", params=params).status_code == 204
    assert client.delete(f"/api/chat/sessions/{sid}", params=params).json()["detail"] == \
        "会话不存在或已被删除"
    assert client.get("/api/chat/sessions",
                      params={"agent_id": "case_design", "project_id": pid}).json() == {"sessions": []}
    assert not (
        root / ".AiTester" / "session_history" / "case_design" / f"{sid}.jsonl"
    ).exists()


def test_send_defaults_to_empty_session_id(tmp_path: Path) -> None:
    client = _wired_client(tmp_path)
    pid = _seed_project(client.app, tmp_path)
    body = _send_body(client, {"message": "生成用例", "agent_id": "case_design", "project_id": pid})
    assert body["session_id"].startswith("sess_")
    assert body["title"] == "生成用例"
    assert body["steps"] == []
    listed = client.get("/api/chat/sessions",
                        params={"agent_id": "case_design", "project_id": pid}).json()["sessions"]
    assert [s["id"] for s in listed] == [body["session_id"]]


def test_send_with_temporary_key_does_not_appear_in_list(tmp_path: Path) -> None:
    client = _wired_client(tmp_path)
    pid = _seed_project(client.app, tmp_path)
    body = _send_body(client, {"session_id": "kb-console", "message": "hi",
                               "agent_id": "case_design", "project_id": pid})
    assert body["session_id"] == "kb-console"
    assert client.get("/api/chat/sessions",
                      params={"agent_id": "case_design", "project_id": pid}).json() == {"sessions": []}


def test_send_unknown_session_id_returns_404(tmp_path: Path) -> None:
    client = _wired_client(tmp_path)
    pid = _seed_project(client.app, tmp_path)
    r = client.post("/api/chat/send/stream",
                    json={"session_id": "sess_deadbeef", "message": "hi",
                          "agent_id": "case_design", "project_id": pid})
    assert r.status_code == 404
    assert r.json()["detail"] == "会话不存在或已被删除"


def test_messages_drops_malformed_persisted_steps(tmp_path: Path) -> None:
    client, sid, pid, _ = _seed(tmp_path)
    client.app.state.sessions.for_agent(pid, "case_design").append(
        sid, "assistant", "混合痕迹",
        steps=["x", {"tool": "read"}, None,
               {"tool": "grep", "ok": False, "round": 2, "detail": "x"}])
    r = client.get(
        f"/api/chat/sessions/{sid}/messages",
        params=_msg_params("case_design", pid),
    )
    assert r.status_code == 200
    assert r.json()["messages"][-1]["steps"] == [
        {"tool": "grep", "ok": False, "round": 2, "detail": "x"}]


def test_send_to_foreign_agent_session_returns_404(tmp_path: Path) -> None:
    """sess_* 建在 case_design 目录下，用另一 agent 目录续写 → 查无（404）。"""
    application = _wired_app(tmp_path)
    pid = _seed_project(application, tmp_path)
    store = application.state.sessions.for_agent(pid, "case_design")
    sid = store.new_id()
    store.create(sid, "case_design", pid, "订单退款")
    client = TestClient(application)
    # ghost 若在 runtime 未知会更早 404；跨 agent 子目录查无同属「不存在」
    r = client.post(
        "/api/chat/send/stream",
        json={"session_id": sid, "message": "hi", "agent_id": "ghost", "project_id": pid},
    )
    assert r.status_code == 404
    assert _send_body(client, {
        "session_id": sid, "message": "再加一条", "agent_id": "case_design",
        "project_id": pid,
    })["session_id"] == sid


def test_send_returns_nonempty_steps_at_http_level(tmp_path: Path) -> None:
    from langchain_core.messages import ToolMessage

    from aitester.services.agent_runtime import AgentInstance

    class _FakeGraph:
        def stream(self, state, *, config=None, stream_mode=None):
            yield ("custom", {"type": "turn", "round": 1, "text": "", "stopped": False,
                              "tool_calls": [{"id": "c1", "name": "read",
                                              "args": {"path": "a.md"}}]})
            yield ("updates", {"tools": {"messages": [
                ToolMessage(content="内容", tool_call_id="c1", name="read",
                            status="success")]}})
            yield ("custom", {"type": "turn", "round": 2, "text": "完成",
                              "stopped": False, "tool_calls": []})

    application = _app(tmp_path)
    pid = _seed_project(application, tmp_path)
    application.state.chat_service = ChatService(
        provider=MockProvider(),
        agent_runtime=SimpleNamespace(
            build=lambda agent_id, session_id, provider_override=None, cwd=".", **_: AgentInstance(
                agent_id=agent_id, system_prompt="p", provider=MockProvider(),
                tools=[], build_graph=lambda provider, tools: _FakeGraph())),
        sessions=application.state.sessions,
        projects=application.state.project_config,
    )
    client = TestClient(application)
    body = _send_body(client,
                      {"message": "读文件", "agent_id": "case_design", "project_id": pid})
    assert body["steps"] == [
        {"tool": "read", "ok": True, "round": 1, "detail": '{"path": "a.md"}'}]
    assert "result" not in body["steps"][0]
    msgs = client.get(
        f"/api/chat/sessions/{body['session_id']}/messages",
        params=_msg_params("case_design", pid),
    ).json()["messages"]
    assert msgs[-1]["steps"] == body["steps"]


def test_messages_endpoint_exposes_stopped(tmp_path) -> None:
    client, sid, pid, _ = _seed(tmp_path)
    client.app.state.sessions.for_agent(pid, "case_design").append(
        sid, "assistant", "半截回答", stopped=True)
    rows = client.get(
        f"/api/chat/sessions/{sid}/messages",
        params=_msg_params("case_design", pid),
    ).json()["messages"]
    assert rows[-1]["stopped"] is True
    assert rows[0]["stopped"] is False
