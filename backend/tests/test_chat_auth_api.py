"""授权侧的端点契约：码与 detail 逐字对齐 spec 错误表，帧序对齐 spec 数据流。

TestClient 会缓冲整段响应（第 4 片实测），所以这里只断顺序与内容，不断节奏。
"""

import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from test_chat_pending import _Scripted, _call_round  # 复用剧本 provider，不再抄第三份
from test_chat_stream_api import _NoopKbManager, _pid, _stream
from streaming_fakes import sse_frames

from aitester.config import Settings
from aitester.main import create_app
from aitester.services import ChatService


def _default_app(tmp_path: Path):
    return create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        projects_path=tmp_path / "p.json",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        memory_manager=_NoopKbManager(),
    )


def _app(tmp_path: Path, provider=None):
    application = _default_app(tmp_path)
    # 待批表必须用 app.state 那一份：端点读的是它，服务写的也必须是它（同一条内存表两个口）
    application.state.chat_service = ChatService(
        provider=provider if provider is not None else _Scripted([_call_round("../escape.md"),
                                                                  AIMessage(content="写好了")]),
        agent_runtime=application.state.agent_runtime,
        sessions=application.state.sessions,
        projects=application.state.project_config,
        pending=application.state.pending_registry,
    )
    return application


def _hold(tmp_path: Path) -> tuple[TestClient, str, str, str]:
    """发到挂起：回 client、项目 id、run_id、session_id——pending 按项目取数（R14），
    而读会话正文要 session_id，四个都得给，调用方才不必为了一条断言再建一次 app。"""
    application = _app(tmp_path)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    frames = _stream(client, {"message": "写界外", "agent_id": "case_design",
                              "project_id": pid, "perm_mode": "boundary"})
    return client, pid, frames[0][1]["run_id"], frames[0][1]["session_id"]


def _pending(client: TestClient, pid: str) -> list[dict]:
    """待批表按「智能体 × 项目」取数（R14）：走查用的智能体恒为 case_design。"""
    return client.get("/api/chat/pending",
                      params={"agent_id": "case_design", "project_id": pid}).json()["runs"]


def _stream_resume(client: TestClient, run_id: str) -> list[tuple[str, dict]]:
    with client.stream("POST", "/api/chat/resume/stream", json={"run_id": run_id}) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8")
        return sse_frames(resp)


def test_free_mode_emits_no_wait_frame(tmp_path: Path) -> None:
    """默认档零行为（红线）：不发 perm_mode 就等于 free，一帧 wait 都不该有。"""
    application = _app(tmp_path)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    frames = _stream(client, {"message": "生成用例", "agent_id": "case_design", "project_id": pid})
    kinds = [e for e, _ in frames]
    assert "wait" not in kinds and kinds[-1] == "done"


def test_invalid_perm_mode_is_400_with_zero_frames(tmp_path: Path) -> None:
    application = _app(tmp_path)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    r = client.post("/api/chat/send/stream", json={"message": "hi", "agent_id": "case_design",
                                                   "project_id": pid, "perm_mode": "yolo"})
    assert r.status_code == 400
    assert r.json()["detail"] == "无效的权限模式，请选择自由权限、只批界外或严格权限"


def test_wait_frame_then_approve_resume_to_single_done(tmp_path: Path) -> None:
    """spec 测试 8 的端到端：wait 帧带 run_id，批准后续跑收尾，磁盘只有一行 assistant。"""
    client, pid, run_id, session_id = _hold(tmp_path)
    pending = _pending(client, pid)
    assert [p["run_id"] for p in pending] == [run_id]
    assert pending[0]["waiting"][0]["call_id"] == "c1"
    assert pending[0]["waiting"][0]["tool"] == "write"
    assert pending[0]["decided"] == [] and pending[0]["perm_mode"] == "boundary"
    assert pending[0]["session_id"] == session_id              # R14：停止后要知道去重拉哪条会话
    r = client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "c1",
                                               "decision": "approve", "remember": False})
    assert r.status_code == 204 and r.content == b""
    events = _stream_resume(client, run_id)
    kinds = [e for e, _ in events]
    assert kinds[-1] == "done" and kinds.count("done") == 1
    assert events[0][0] == "start" and events[0][1]["run_id"] == run_id   # 续跑沿用同一个 run_id
    rows = client.get(f"/api/chat/sessions/{session_id}/messages", params={"agent_id": "case_design", "project_id": pid}).json()["messages"]
    assert [m["role"] for m in rows] == ["user", "assistant"]
    assert _pending(client, pid) == []


def test_approve_unknown_run_is_404(tmp_path: Path) -> None:
    client, _, _run_id, _sid = _hold(tmp_path)
    r = client.post("/api/chat/approve", json={"run_id": "nope", "call_id": "c1",
                                               "decision": "approve", "remember": False})
    assert r.status_code == 404
    assert r.json()["detail"] == "这条回答已经结束，无法再批准"


def test_approve_twice_is_409(tmp_path: Path) -> None:
    client, _, run_id, _sid = _hold(tmp_path)
    client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "c1",
                                           "decision": "approve", "remember": False})
    r = client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "c1",
                                                "decision": "approve", "remember": False})
    assert r.status_code == 409
    assert r.json()["detail"] == "这条授权请求已经处理过了"


def test_approve_with_unknown_decision_is_422_and_not_registered(tmp_path: Path) -> None:
    """决策只认 approve/reject：坏值必须在 pydantic 就被挡下——表里的 pending 队列会把任何
    字符串当合法决策收下，坏值要等喂进 gate 的 interrupt 才炸，那时这条已经 consumed。"""
    client, pid, run_id, _sid = _hold(tmp_path)
    r = client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "c1",
                                               "decision": "maybe", "remember": False})
    assert r.status_code == 422
    assert _pending(client, pid)[0]["decided"] == []      # 一条决策都没进表
    r2 = client.post("/api/chat/resume/stream", json={"run_id": run_id})
    assert r2.status_code == 400                          # 也没被误当成「已批准可续跑」


def test_resume_without_decision_is_400(tmp_path: Path) -> None:
    """R6：只批准不续跑是正常态，但没有任何决策就要求续跑必须被拦下。"""
    client, _, run_id, _sid = _hold(tmp_path)
    r = client.post("/api/chat/resume/stream", json={"run_id": run_id})
    assert r.status_code == 400
    assert r.json()["detail"] == "这条回答还在等你批准"


def test_resume_after_dir_deleted_keeps_one_detail_and_creates_nothing(tmp_path: Path) -> None:
    """spec 测试 8 末条：与 send 逐字同文案，且 write 的 mkdir 一次都没跑。"""
    application = _app(tmp_path)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    frames = _stream(client, {"message": "写界外", "agent_id": "case_design",
                              "project_id": pid, "perm_mode": "boundary"})
    run_id = frames[0][1]["run_id"]
    root = tmp_path / "reqs"
    client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "c1",
                                           "decision": "approve", "remember": False})
    shutil.rmtree(root)
    r = client.post("/api/chat/resume/stream", json={"run_id": run_id})
    assert r.status_code == 400
    assert r.json()["detail"] == (
        f"项目「订单系统」的目录 {root} 不存在或不可访问，请到项目页确认路径")
    assert not root.exists()


def test_stop_during_pending_then_resume_is_404(tmp_path: Path) -> None:
    """spec 测试 9：待批期间停止 → 截断行落盘、卡消失、再续跑 404。"""
    application = _app(tmp_path, provider=_Scripted([
        _call_round("../escape.md", text="我先想想"), AIMessage(content="好的")]))
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    frames = _stream(client, {"message": "写界外", "agent_id": "case_design",
                              "project_id": pid, "perm_mode": "boundary"})
    run_id, sid = frames[0][1]["run_id"], frames[0][1]["session_id"]
    r = client.post("/api/chat/stop", json={"run_id": run_id})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert _pending(client, pid) == []
    rows = client.get(f"/api/chat/sessions/{sid}/messages", params={"agent_id": "case_design", "project_id": pid}).json()["messages"]
    assert rows[-1]["content"] == "我先想想" and rows[-1]["stopped"] is True
    r2 = client.post("/api/chat/resume/stream", json={"run_id": run_id})
    assert r2.status_code == 404


def test_pending_is_empty_for_a_fresh_process(tmp_path: Path) -> None:
    """裁定 2：内存挂起、重启即丢——新 app 的 pending 表必空（走查项 14 的单测锁）。"""
    client, pid, _run_id, _sid = _hold(tmp_path)
    assert _pending(client, pid) != []
    fresh = TestClient(_app(tmp_path))
    assert fresh.get("/api/chat/pending",
                   params={"agent_id": "case_design", "project_id": pid}).json()["runs"] == []


def test_delete_session_cascades_pending(tmp_path: Path) -> None:
    """spec 测试 10：删会话后 pending 表不再含其条目。

    挂起期会话行还不存在（裁定 8），所以先跑完一轮把行落出来，再发第二条并挂住，才谈得上删会话。
    """
    application = _app(tmp_path, provider=_Scripted([
        _call_round("../a.md"), AIMessage(content="第一个写了"),
        _call_round("../b.md"), AIMessage(content="第二个写了")]))
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    first = _stream(client, {"message": "写界外", "agent_id": "case_design",
                             "project_id": pid, "perm_mode": "boundary"})
    run_id, sid = first[0][1]["run_id"], first[0][1]["session_id"]
    client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "c1",
                                           "decision": "approve", "remember": False})
    _stream_resume(client, run_id)                 # 落全行：会话此刻才存在
    second = _stream(client, {"message": "再写一个", "agent_id": "case_design",
                              "project_id": pid, "session_id": sid, "perm_mode": "boundary"})
    assert _pending(client, pid) != []
    assert client.delete(f"/api/chat/sessions/{sid}", params={"agent_id": "case_design", "project_id": pid}).status_code == 204
    assert _pending(client, pid) == []


def test_default_assembly_injects_the_state_registry(tmp_path: Path) -> None:
    """装配级联的唯一锁：create_app 装进服务的待批表必须就是 app.state 那一份对象。

    各测试自建服务覆盖 chat_service，走不到这条默认装配；若 main.py 改成服务自持实例，
    /chat/pending 读的是空表、挂起却写在另一张表上，而全套测试照样绿。
    """
    application = _default_app(tmp_path)
    assert application.state.chat_service.pending is application.state.pending_registry
