"""工具感知 Agent 图：agent → tools → agent → … → END。"""

from __future__ import annotations

from typing import Annotated, Any, Callable

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from aitester.adapters.llm import LlmProvider
from aitester.adapters.tools.base import AiTooler


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


GraphBuilder = Callable[[LlmProvider, list[AiTooler]], CompiledStateGraph]


def _tool_error_message(error: Exception) -> str:
    """把工具异常转为模型可见的错误结果文本。

    ToolNode 默认只捕获 ToolInvocationError，其余异常会中断整个 agent loop；
    这里统一接住，让校验失败/守卫拒绝以错误结果返回给模型自纠。
    """
    return str(error)


def build_agent_graph(provider: LlmProvider, tools: list[AiTooler]) -> CompiledStateGraph:
    """构建 Agent 状态图：有工具走 agent↔tools 循环，无工具退化为单节点直答。"""
    if not tools:
        def answer_node(state: AgentState) -> dict[str, list[BaseMessage]]:
            return {"messages": [provider.invoke_messages(state["messages"])]}

        plain = StateGraph(AgentState)
        plain.add_node("agent", answer_node)
        plain.add_edge(START, "agent")
        plain.add_edge("agent", END)
        return plain.compile()

    tool_node = ToolNode(tools, handle_tool_errors=_tool_error_message)
    bound = provider.bind_tools(tools)

    def agent_node(state: AgentState) -> dict[str, list[BaseMessage]]:
        response = bound.invoke_messages(state["messages"])
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


def run_graph(
    build: GraphBuilder,
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
) -> dict[str, Any]:
    """按指定拓扑执行一轮，返回 {reply, tool_traces}。"""
    graph = build(provider, tools)
    result = graph.invoke({"messages": messages})

    reply = ""
    tool_traces: list[dict[str, Any]] = []
    for msg in result["messages"]:
        if isinstance(msg, AIMessage) and not msg.tool_calls and msg.content:
            reply = str(msg.content)
        if isinstance(msg, ToolMessage):
            tool_traces.append({"tool": msg.name or "", "result": str(msg.content)})

    return {"reply": reply, "tool_traces": tool_traces}


def run_agent(
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
) -> dict[str, Any]:
    """默认 react 循环的便捷入口（等价于 run_graph(build_agent_graph, …)）。"""
    return run_graph(build_agent_graph, provider, tools, messages)
