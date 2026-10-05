"""工具感知 Agent 图：agent → gate → tools → agent → … → END；无工具时退化为单节点直答图。"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Iterator

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command
from typing_extensions import TypedDict

from aitester.adapters.llm import LlmProvider
from aitester.adapters.tools.base import AiTooler
from aitester.orchestration.checkpoint import get_checkpointer, new_thread_id
from aitester.orchestration.gate import GATE_KEY, GateContext, make_gate_node
from aitester.orchestration.run_control import RUN_CONTROL_KEY, RunControl
from aitester.orchestration.subagent import detail_of


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


GraphBuilder = Callable[[LlmProvider, list[AiTooler]], CompiledStateGraph]


def _run_control(config: RunnableConfig | None):
    """从注入的 RunnableConfig 取取消对象；没注入就是 None（echo、单测直调、无 stop 的路径）。"""
    if not config:
        return None
    return (config.get("configurable") or {}).get(RUN_CONTROL_KEY)


def _gate_context(config: RunnableConfig | None) -> GateContext | None:
    """从注入的 RunnableConfig 取执法上下文；没注入 = 不执法（echo、单测直调、free 档）。"""
    if not config:
        return None
    return (config.get("configurable") or {}).get(GATE_KEY)


def _unanswered(messages: list[BaseMessage]) -> tuple[int, list[Any]]:
    """(最后一条带 tool_calls 的 AIMessage 下标, 它里面还没有结果可配对的调用)。

    口径与 ToolNode 自己取消息一致（实测它反向扫到第一条 AIMessage 就开跑），
    判据却是「有没有对应的 ToolMessage」而不是「在不在 tool_calls 里」——被拒的调用
    由 gate 合成了 error 结果，所以它不再执行，但必须留在清单里给 provider 看。
    """
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, AIMessage):
            return idx, [c for c in msg.tool_calls if str(c.get("id")) not in answered]
    return -1, []


def route_after_gate(state: AgentState) -> str:
    """gate 之后：还有批准的就执行，一条不剩的就回模型。

    显式边走全拒（P6 场景 3 实测 ToolNode 空跑不抛，但那是未承诺行为，不能当语义用）。
    """
    return "tools" if _unanswered(state["messages"])[1] else "agent"


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
        return plain.compile(checkpointer=get_checkpointer())

    tool_node = ToolNode(tools, handle_tool_errors=_tool_error_message)
    bound = provider.bind_tools(tools)

    def agent_node(state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        return {"messages": [_stream_round(
            bound, state["messages"], _round_no(state), _run_control(config)
        )]}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "gate"
        return END

    def tools_node(state: AgentState, config: RunnableConfig) -> Any:
        """只把没答过的调用交给 ToolNode——它自己会跑清单里的每一条（实测不过滤）。

        清单完整（被拒的也留在里面）是给 provider 看的形状，执行则必须逐条排除已有结果，
        否则一条「已拒绝」的写文件会在下一轮真的落盘。
        """
        messages = state["messages"]
        idx, calls = _unanswered(messages)
        if not calls:
            return {"messages": []}
        trimmed = messages[idx].model_copy(update={"tool_calls": calls})
        return tool_node.invoke(
            {"messages": [*messages[:idx], trimmed, *messages[idx + 1:]]}, config)

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("gate", make_gate_node(_gate_context))
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"gate": "gate", END: END})
    graph.add_conditional_edges("gate", route_after_gate, {"tools": "tools", "agent": "agent"})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=get_checkpointer())


def stream_graph(
    build: GraphBuilder,
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
    control: RunControl | None = None,
    thread_id: str = "",
    resume: Any = None,
    gate: GateContext | None = None,
) -> Iterator[dict[str, Any]]:
    """按指定拓扑执行一轮，把执行过程实时折成事件流。

    带 checkpointer 后 stream() 必须给 thread_id（缺键直接 ValueError，实测），所以这里一律
    给值：SSE 路由给 run_id（P4：pending→resume 复用同一个），run_graph 这类历史入口给空串自造。
    resume 非 None 表示「从 gate 的中断处续跑」，此时不再投新输入——投了就变成新回合语义。
    挂起时 finish.pending=True，且它前面一定有至少一条 wait 事件。
    """
    graph = build(provider, tools)
    configurable: dict[str, Any] = {"thread_id": thread_id or new_thread_id()}
    if control is not None:
        configurable[RUN_CONTROL_KEY] = control
    if gate is not None:
        configurable[GATE_KEY] = gate
    stream = graph.stream(
        Command(resume=resume) if resume is not None else {"messages": messages},
        config={"configurable": configurable},
        stream_mode=["custom", "updates"],
    )

    reply = ""
    round_no = 0
    stopped = False
    pending = False
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
                            "detail": detail_of(call),
                        }
                elif payload.get("text"):
                    # 与迁移前同口径：后写的非工具轮 content 覆盖前面的（中间轮文本因此不落盘）
                    reply = str(payload["text"])
            elif kind == "sub":
                yield dict(payload)          # 子阶段帧（start/done/fail）：drive 已按契约备齐
            elif kind in ("call", "step") and payload.get("subagent") is not None:
                yield dict(payload)          # 子过程帧：带来源标注；父层折叠不碰它（R3/R10）
            continue

        hits = payload.get("__interrupt__")
        if hits:
            # P1：挂起以 updates 分片多一个 __interrupt__ 键出现，流干净结束、不抛异常。
            # 载荷由 gate 备齐，这里严格取键：缺字段即 KeyError，正是 spec 测试 7 要的响亮失败。
            for hit in hits:
                value = hit.value
                yield {"type": "wait", "call_id": value["call_id"], "tool": value["tool"],
                       "action": value["action"], "target": value["target"],
                       "command": value["command"], "cwd": value["cwd"],
                       "subagent": value["subagent"]}
            pending = True
            continue

        produced = payload.get("tools") or {}
        for msg in produced.get("messages") or []:
            if not isinstance(msg, ToolMessage):
                continue
            detail = detail_of(calls_by_id.get(str(msg.tool_call_id), {}))
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
        "pending": pending,
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
