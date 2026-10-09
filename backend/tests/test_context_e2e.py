"""一次回合的三处读数必须同源：终帧、落盘行、以及被闸管住的那条工具产物。

挂具照 test_chat_stream / test_case_design_graph 的既有形态复用，不另起一套：
真 AgentRuntime + 真注册表 + 真图 + 真文件工具，只有 provider 是替身。

四条钉各管一段：
1. 闸与尺同回合 —— done 帧读数 = 磁盘行读数，且 truncated 那一条真是 read 砍出来的；
2. 两张图共用一把尺 —— 主会话图与 case_design 图轮次接在同一本累加件上（CM-5 的证）；
3. SSE 那一跳的正向钉 —— 帧载荷里的 data["context"] 非空且与磁盘行等值（白名单不是摆设）；
4. 断开（GeneratorExit）那一跳 —— 没有 done 帧，读数只能活在截断行里。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from streaming_fakes import ScriptedProvider, local_tools, sse_frames
from test_chat_stream import _Scripted, _runtime, _service, project  # noqa: F401  复用服务层夹具
from test_chat_stream_api import _app, _pid  # noqa: F401  复用 SSE 传输层挂具

from aitester.adapters.llm.metered import MeteredProvider
from aitester.case_design.graph import build_case_design_graph
from aitester.context import meter
from aitester.context.budget import CAP_ENV
from aitester.context.usage import ContextUsage
from aitester.orchestration import build_agent_graph, stream_graph
from aitester.services import ChatService
from aitester.services.session_locator import SessionLocator
from aitester.services.session_store import SessionStore, open_session_store

SNAP_KEYS = {"window", "rounds", "peak_occupancy", "occupancy_source",
             "spent_input", "spent_output", "truncated", "error"}


def setup_function(_):
    meter.reset_for_tests()          # 词表可能被别处预热过：本条按字符兜底才是确定读数


def test_gate_and_meter_work_in_one_turn(tmp_path, project, monkeypatch):
    monkeypatch.setenv(CAP_ENV, "120")
    svc_proj, pid, root = project
    (root / "big.txt").write_text(
        "\n".join(f"第{i:03d}行数据内容示例" for i in range(400)), encoding="utf-8")
    store = open_session_store(root, "case_design")
    svc = ChatService(
        provider=_Scripted([
            AIMessage(content="", tool_calls=[
                {"id": "c1", "name": "read", "args": {"file_path": "big.txt"},
                 "type": "tool_call"}]),
            AIMessage(content="看完了"),
        ]),
        agent_runtime=_runtime(tmp_path), sessions=SessionLocator(svc_proj), projects=svc_proj)
    prepared = svc.prepare("", "读大文件", "case_design", pid)
    done = list(svc.stream_turn(prepared))[-1]

    ctx = done["context"]
    assert set(ctx) == SNAP_KEYS
    assert ctx["rounds"] == 2 and ctx["peak_occupancy"] > 0
    assert ctx["occupancy_source"] == "estimated"          # Mock 面不许冒领真值
    assert [t["tool"] for t in ctx["truncated"]] == ["read"]
    t = ctx["truncated"][0]
    assert t["dropped"] > 0 and t["kept"] + t["dropped"] == t["original"]
    assert store.messages(prepared.session_id)[-1].context == ctx   # 三处同源


def test_case_design_graph_and_main_loop_share_the_ruler(tmp_path):
    """把缝挪回任一图里，这条就红（CM-5 的证）。"""
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    usage = ContextUsage(window=1000)

    def run(build):
        inner = ScriptedProvider([
            AIMessage(content="", tool_calls=[
                {"id": "c1", "name": "read", "args": {"file_path": "a.txt"},
                 "type": "tool_call"}]),
            AIMessage(content="读到 hello"),
        ])
        return list(stream_graph(build, MeteredProvider(inner, usage),
                                 local_tools(root), [HumanMessage(content="读文件")]))

    run(build_agent_graph)
    after_react = usage.rounds
    assert after_react == 2                        # 工具轮 + 收尾轮都过同一把尺
    run(build_case_design_graph)
    assert usage.rounds == after_react + 2         # 第二张图接着记，不许另开一本


def test_sse_done_frame_carries_the_snapshot(tmp_path):
    """白名单那行不是装饰：帧载荷里的 context 必须非空，且与磁盘那一行等值。

    只断等值不断非空是假绿（两边都是 None 也等）；两条都钉。
    session_id 从末帧自己取，不许猜。
    """
    application = _app(tmp_path, provider=_Scripted([
        AIMessage(content="", tool_calls=[
            {"id": "c1", "name": "write",
             "args": {"file_path": "t.txt", "content": "v"},
             "type": "tool_call"}]),
        AIMessage(content="已写入"),
    ]))
    pid = _pid(application, tmp_path)
    root = Path(application.state.project_config.get(pid)["dir"])

    with TestClient(application).stream(
        "POST", "/api/chat/send/stream",
        json={"message": "写文件", "agent_id": "case_design", "project_id": pid}
    ) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8")
        frames = sse_frames(resp)

    assert frames[-1][0] == "done"
    data = frames[-1][1]
    assert data["context"] is not None             # 非空先钉：两边都缺也算「等值」
    assert set(data["context"]) == SNAP_KEYS
    rows = open_session_store(root, "case_design").messages(data["session_id"])
    assert data["context"] == rows[-1].context     # 帧上那句与盘上那行同一份快照


def test_disconnect_row_carries_the_snapshot(tmp_path, project):
    """断开收尾只落盘不发 done：用户重开页面时，读数只能在会话行里。"""
    svc_proj, pid, root = project
    store = open_session_store(root, "case_design")
    svc = _service(tmp_path, svc_proj)                # MockProvider：第一帧 delta
    prepared = svc.prepare("", "生成用例", "case_design", pid)
    stream = svc.stream_turn(prepared)
    seen: list[str] = []
    event = next(stream)
    seen.append(event["type"])
    assert event["type"] == "delta"                   # 只消费第一帧就断开
    stream.close()

    assert "done" not in seen                         # 这条路径没有 done 帧可带
    row = store.messages(prepared.session_id)[-1]
    assert row.stopped is True
    assert row.context is not None
    assert set(row.context) == SNAP_KEYS
