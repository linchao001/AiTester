"""工具感知 Agent 图：agent → tools → agent → … → END；无工具时退化为单节点直答图。"""

from __future__ import annotations

import json
from typing import Annotated, Any, Callable, Iterator

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from aitester.adapters.llm import LlmProvider
from aitester.adapters.tools.base import AiTooler
from aitester.orchestration.run_control import RUN_CONTROL_KEY, RunControl


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


GraphBuilder = Callable[[LlmProvider, list[AiTooler]], CompiledStateGraph]

# 过程块参数摘要的截断上限（spec 接口块登记值，实现此前漂移成裸 80）
DETAIL_MAX = 80


def _run_control(config: RunnableConfig | None):
    """从注入的 RunnableConfig 取取消对象；没注入就是 None（echo、单测直调、无 stop 的路径）。"""
    if not config:
        return None
    return (config.get("configurable") or {}).get(RUN_CONTROL_KEY)


def _round_no(state: AgentState) -> int:
    """轮次 = 已落地的 ToolMessage 条数 + 1（P6 实测与 run_graph 现有 round_no 语义一致）。"""
    return 1 + sum(1 for m in state["messages"] if isinstance(m, ToolMessage))


def _stream_round(provider: Any, messages: list[BaseMessage], round_no: int, control) -> AIMessage:
    """逐 chunk 累加并实时下发 delta；取消即在下一个检查点停笔。

    停笔时丢弃待派发的 tool_calls——否则「用户已经按了停止」的一轮还会继续开工具、写文件。
    返回普通 AIMessage（P8：本版本 AIMessageChunk 没有 .message，必须显式重建），
    这样 run_graph 既有的 isinstance(msg, AIMessage) 判据不受子类型干扰。
    """
    writer = get_stream_writer()
    merged = AIMessageChunk(content="")
    text = ""
    stopped = False
    for chunk in provider.stream_messages(messages):
        if control is not None and control.cancelled:
            stopped = True
            break
        piece = str(chunk.content or "")
        if piece:
            text += piece
            writer({"type": "delta", "round": round_no, "text": piece})
        merged = merged + chunk
    calls = [] if stopped else list(merged.tool_calls)
    content = text if stopped else str(merged.content)
    writer({
        "type": "turn",
        "round": round_no,
        "text": content,
        "stopped": stopped,
        "tool_calls": [
            {"id": c.get("id"), "name": c["name"], "args": c["args"]} for c in calls
        ],
    })
    return AIMessage(
        content=content,
        tool_calls=calls,
        additional_kwargs=dict(merged.additional_kwargs),
    )


def _tool_error_message(error: Exception) -> str:
    """把工具异常转为模型可见的错误结果文本。

    ToolNode 默认只捕获 ToolInvocationError，其余异常会中断整个 agent loop；
    这里统一接住，让校验失败/守卫拒绝以错误结果返回给模型自纠。
    """
    return str(error)


def build_agent_graph(provider: LlmProvider, tools: list[AiTooler]) -> CompiledStateGraph:
    """构建 Agent 状态图：有工具走 agent↔tools 循环，无工具退化为单节点直答。"""
    if not tools:
        def answer_node(state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
            return {"messages": [_stream_round(
                provider, state["messages"], _round_no(state), _run_control(config)
            )]}

        plain = StateGraph(AgentState)
        plain.add_node("agent", answer_node)
        plain.add_edge(START, "agent")
        plain.add_edge("agent", END)
        return plain.compile()

    tool_node = ToolNode(tools, handle_tool_errors=_tool_error_message)
    bound = provider.bind_tools(tools)

    def agent_node(state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        return {"messages": [_stream_round(
            bound, state["messages"], _round_no(state), _run_control(config)
        )]}

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


def _detail_of(call: dict[str, Any]) -> str:
    """过程块的参数摘要：与迁移前逐字同口径（JSON 序列化后截 DETAIL_MAX）。"""
    try:
        return json.dumps(call.get("args") or {}, ensure_ascii=False)[:DETAIL_MAX]
    except (TypeError, ValueError):
        return str(call.get("args"))[:DETAIL_MAX]  # 非常规 args（非 JSON 可序列化）不退化成报错，UI 只截一行


def stream_graph(
    build: GraphBuilder,
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
    control: RunControl | None = None,
) -> Iterator[dict[str, Any]]:
    """按指定拓扑执行一轮，把执行过程实时折成事件流。

    两路合成：节点内 get_stream_writer() 写的 custom（delta/turn）给正文与工具发起，
    graph.stream 的 updates 分片给 ToolMessage（step/draft）。二者按写入顺序到达（P6 实测）。
    末条恒为 finish：{reply, tool_traces, drafts, stopped}。
    """
    graph = build(provider, tools)
    # 只有真带 control 时才注入 config：空 config 等于「没有取消对象」，与 invoke 直调同形
    config = {"configurable": {RUN_CONTROL_KEY: control}} if control is not None else {}
    stream = graph.stream(
        {"messages": messages}, config=config, stream_mode=["custom", "updates"]
    )

    reply = ""
    round_no = 0
    stopped = False
    tool_traces: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    calls_by_id: dict[str, dict[str, Any]] = {}

    for mode, payload in stream:
        if mode == "custom":
            kind = payload.get("type")
            if kind == "delta":
                yield {"type": "delta", "round": payload["round"], "text": payload["text"]}
            elif kind == "turn":
                round_no = max(round_no, int(payload["round"]))
                stopped = stopped or bool(payload["stopped"])
                calls = payload.get("tool_calls") or []
                if calls:
                    for call in calls:
                        calls_by_id[str(call.get("id"))] = call
                        yield {
                            "type": "call",
                            "tool": call["name"],
                            "round": payload["round"],
                            "detail": _detail_of(call),
                        }
                elif payload.get("text"):
                    # 与迁移前同口径：后写的非工具轮 content 覆盖前面的（中间轮文本因此不落盘）
                    reply = str(payload["text"])
            continue

        produced = payload.get("tools") or {}
        for msg in produced.get("messages") or []:
            if not isinstance(msg, ToolMessage):
                continue
            detail = _detail_of(calls_by_id.get(str(msg.tool_call_id), {}))
            trace = {
                "tool": msg.name or "",
                "result": str(msg.content),
                "ok": str(getattr(msg, "status", "success")) != "error",
                "round": round_no,
                "detail": detail,
            }
            tool_traces.append(trace)
            # step 事件是 UI 过程块的契约字段，严格取键、不外泄 result（services 层一直不吃 result）
            yield {
                "type": "step",
                "tool": trace["tool"],
                "ok": trace["ok"],
                "round": trace["round"],
                "detail": trace["detail"],
            }
            # prepare_kb_write 的草案走 artifact 通道（模型不可见），只发给 UI 确认
            if msg.name == "prepare_kb_write" and getattr(msg, "artifact", None):
                drafts.append(msg.artifact)
                yield {"type": "draft", "draft": msg.artifact}

    yield {
        "type": "finish",
        "reply": reply,
        "tool_traces": tool_traces,
        "drafts": drafts,
        "stopped": stopped,
    }


def run_graph(
    build: GraphBuilder,
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
) -> dict[str, Any]:
    """一次性折返壳：把事件流折回 {reply, tool_traces, drafts}。

    形状与迁移前逐字相同（tool_traces 每项 {tool, result, ok, round, detail}，
    services/chat.py 按严格取键消费，缺字段即 KeyError，不兜默认防假绿）。
    留着它只为让既有测试继续锁住流式核心——一份实现，两种消费。
    """
    out: dict[str, Any] = {"reply": "", "tool_traces": [], "drafts": []}
    for event in stream_graph(build, provider, tools, messages):
        if event["type"] == "finish":
            out = {
                "reply": event["reply"],
                "tool_traces": event["tool_traces"],
                "drafts": event["drafts"],
            }
    return out


def run_agent(
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
) -> dict[str, Any]:
    """默认 react 循环的便捷入口（等价于 run_graph(build_agent_graph, …)）。"""
    return run_graph(build_agent_graph, provider, tools, messages)
