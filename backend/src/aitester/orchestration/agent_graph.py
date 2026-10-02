"""工具感知 Agent 图：agent → tools → agent → … → END；无工具时退化为单节点直答图。"""

from __future__ import annotations

import json
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
    """按指定拓扑执行一轮，返回 {reply, tool_traces, drafts}。

    tool_traces 每项为 {tool, result, ok, round, detail}：ok/round/detail 是 UI 过程块
    的契约字段，services/chat.py 按严格取键消费，缺字段即 KeyError（不兜默认防假绿）。
    """
    graph = build(provider, tools)
    result = graph.invoke({"messages": messages})

    reply = ""
    tool_traces: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    calls_by_id: dict[str, dict[str, Any]] = {}
    round_no = 0
    for msg in result["messages"]:
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                round_no += 1  # 一轮 agent↔tools = 一条带 tool_calls 的 AIMessage
                for call in msg.tool_calls:
                    calls_by_id[str(call.get("id"))] = call
            elif msg.content:
                reply = str(msg.content)
            continue
        if isinstance(msg, ToolMessage):
            call = calls_by_id.get(str(msg.tool_call_id), {})
            try:
                detail = json.dumps(call.get("args") or {}, ensure_ascii=False)[:80]
            except (TypeError, ValueError):
                detail = str(call.get("args"))[:80]  # 非常规 args（非 JSON 可序列化）不退化成报错，UI 只截一行
            tool_traces.append({
                "tool": msg.name or "",
                "result": str(msg.content),
                "ok": str(getattr(msg, "status", "success")) != "error",
                "round": round_no,
                "detail": detail,
            })
            # prepare_kb_write 的草案走 artifact 通道（模型不可见），只发给 UI 确认
            if msg.name == "prepare_kb_write" and getattr(msg, "artifact", None):
                drafts.append(msg.artifact)

    return {"reply": reply, "tool_traces": tool_traces, "drafts": drafts}


def run_agent(
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
) -> dict[str, Any]:
    """默认 react 循环的便捷入口（等价于 run_graph(build_agent_graph, …)）。"""
    return run_graph(build_agent_graph, provider, tools, messages)
