"""取消信号：RunControl 本体，以及 P6 那条前提的持久钉——对象真能经 config.configurable 进节点。"""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from aitester.orchestration import build_agent_graph, run_agent
from aitester.orchestration.run_control import RUN_CONTROL_KEY, RunControl
from streaming_fakes import ChunkedStreamMixin, local_tools


class _ToolCallingProvider(ChunkedStreamMixin):
    """与 test_orchestration._ToolCallingProvider 同款计数剧本：第 1 轮工具调用，第 2 轮收口。

    brief 原样是无状态地永远重发同一条 tool_calls 消息：不注入取消位时 agent↔tools
    永远到不了 END（旧实现靠「同一消息实例同 id 被 add_messages 原位去重」侥幸终止，
    而 _stream_round 按 P8 每次显式重建 AIMessage、id 必新，侥幸不再成立），故补上收口。
    """

    name = "scripted"
    model_ref = "scripted/model"

    def __init__(self, message: AIMessage) -> None:
        self._message = message
        self._turn = 0

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_ToolCallingProvider":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        self._turn += 1
        if self._turn == 1:
            return self._message
        return AIMessage(content="done")


def _write_call(file_name: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "write", "args": {"file_path": file_name, "content": "v"},
         "id": "c1", "type": "tool_call"}])


def test_run_control_is_a_latch() -> None:
    control = RunControl()
    assert control.cancelled is False
    control.cancel()
    control.cancel()                      # 幂等：重复置位不炸
    assert control.cancelled is True


def test_control_reaches_the_node_and_drops_pending_tool_calls(tmp_path: Path) -> None:
    """P6 钉：取消对象经 config.configurable 读得到，且命中即丢弃待派发的 tool_calls。

    工具没派发 == write 没执行 == tmp_path 里没有文件：这条断言同时是「已停的一轮不再写盘」。
    """
    control = RunControl()
    control.cancel()
    graph = build_agent_graph(_ToolCallingProvider(_write_call("x.txt")), local_tools(tmp_path))
    out = graph.invoke({"messages": [HumanMessage(content="写文件")]},
                       config={"configurable": {RUN_CONTROL_KEY: control}})
    last = out["messages"][-1]
    assert last.content == ""
    assert last.tool_calls == []          # 丢弃后 should_continue 直接 END
    assert not (tmp_path / "x.txt").exists()


def test_no_control_in_config_runs_the_round_normally(tmp_path: Path) -> None:
    # 不注入取消对象（echo 路径与单测直调）必须照旧跑完：判据是 None 而非缺键即报错
    graph = build_agent_graph(_ToolCallingProvider(_write_call("y.txt")), local_tools(tmp_path))
    out = graph.invoke({"messages": [HumanMessage(content="写文件")]})
    assert out["messages"][-1].tool_calls == [] or (tmp_path / "y.txt").exists()
    assert (tmp_path / "y.txt").read_text(encoding="utf-8") == "v"
