from aitester.adapters.llm import MockProvider
from aitester.orchestration import run_echo


def test_run_echo_returns_provider_reply() -> None:
    messages = [
        {"role": "system", "content": "你是测试智能体"},
        {"role": "user", "content": "hello"},
    ]
    assert run_echo(MockProvider(), messages) == "[mock] hello"


from langchain_core.messages import AIMessage, ToolMessage

from aitester.orchestration import run_graph
from aitester.orchestration.agent_graph import build_agent_graph


class _ToolCallingProvider:
    """固定脚本：第 1 轮调两个工具（一个成功一个失败），第 2 轮出最终回复。"""

    name = "spy"
    model_ref = "spy/model"

    def __init__(self) -> None:
        self._turn = 0

    def complete(self, messages):
        return "[spy]"

    def bind_tools(self, tools):
        return self

    def invoke_messages(self, messages):
        self._turn += 1
        if self._turn == 1:
            return AIMessage(content="", tool_calls=[
                {"name": "read", "args": {"path": "a.md"}, "id": "c1", "type": "tool_call"},
                {"name": "grep_search", "args": {"pattern": "x"}, "id": "c2", "type": "tool_call"},
            ])
        return AIMessage(content="完成")


def _graph_with(messages):
    class _Fixed:
        def invoke(self, state):
            return {"messages": messages}

    return lambda provider, tools: _Fixed()


def test_steps_carry_ok_round_and_detail() -> None:
    messages = [
        AIMessage(content="", tool_calls=[
            {"name": "read", "args": {"path": "a.md"}, "id": "c1", "type": "tool_call"},
            {"name": "grep_search", "args": {"pattern": "x" * 200}, "id": "c2", "type": "tool_call"},
        ]),
        ToolMessage(content="内容", tool_call_id="c1", name="read", status="success"),
        ToolMessage(content="工具执行失败", tool_call_id="c2", name="grep_search", status="error"),
        AIMessage(content="完成"),
    ]
    result = run_graph(_graph_with(messages), _ToolCallingProvider(), [], [])
    assert [t["ok"] for t in result["tool_traces"]] == [True, False]
    assert [t["round"] for t in result["tool_traces"]] == [1, 1]
    assert result["tool_traces"][0]["detail"] == '{"path": "a.md"}'
    assert len(result["tool_traces"][1]["detail"]) == 80  # 超长参数截断
    assert [t["tool"] for t in result["tool_traces"]] == ["read", "grep_search"]
    assert result["reply"] == "完成"


def test_round_increments_per_agent_turn() -> None:
    messages = [
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="ok", tool_call_id="c1", name="read"),
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "c2", "type": "tool_call"}]),
        ToolMessage(content="ok", tool_call_id="c2", name="read"),
        AIMessage(content="结束"),
    ]
    result = run_graph(_graph_with(messages), _ToolCallingProvider(), [], [])
    assert [t["round"] for t in result["tool_traces"]] == [1, 2]


def test_detail_is_empty_json_when_args_missing() -> None:
    messages = [
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="ok", tool_call_id="c1", name="read"),
        AIMessage(content="结束"),
    ]
    result = run_graph(_graph_with(messages), _ToolCallingProvider(), [], [])
    assert result["tool_traces"][0]["detail"] == "{}"


def test_real_graph_still_loops_and_returns_reply() -> None:
    provider = _ToolCallingProvider()
    result = run_graph(build_agent_graph, provider, [], [])
    assert result["tool_traces"] == []  # 无工具 → 单节点直答图，过程块为空
