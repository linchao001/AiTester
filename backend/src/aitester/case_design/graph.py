"""case_design_loop 拓扑：driver → (agent | END)；agent → tools? gate : driver；tools → agent。

与 react 的分工：react 的一轮 = agent 管到底；本拓扑把「阶段转场」交给 driver 节点——
driver 每进一次要么下发一条编排指令（route=agent）、要么发终帧（route=end），主智能体
只负责按指令产出制品。节点名必须叫 `tools`（stream_graph 按这个名字折叠 step 帧）。

导入环：本模块**零 orchestration import**——复用件全部在 build_case_design_graph 函数体
内延迟导入。否则 graph_registry 顶层 import 本模块后，「先 import aitester.case_design.graph」
的次序会撞上半初始化的 orchestration 包（两个入口次序都要安全）。
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from aitester.adapters.llm import LlmProvider
from aitester.adapters.tools.base import AiTooler
from aitester.case_design.driver import make_driver_node


class CaseDesignState(TypedDict):
    """图状态：messages 走 add_messages 归并追加；case 是驱动游标快照（每轮整体覆盖）。"""

    messages: Annotated[list[BaseMessage], add_messages]
    case: dict


def _task_tool_of(tools: list[AiTooler]) -> Any:
    """task 工具按「有 roster 字典」认领（与 _subagent_parallel 认 parallel 同款）；缺了返回 None。"""
    for tool in tools:
        if isinstance(getattr(tool, "roster", None), dict):
            return tool
    return None


def build_case_design_graph(provider: LlmProvider, tools: list[AiTooler]) -> CompiledStateGraph:
    """构建专属拓扑：复用 react 的流式轮/闸门/tools 折叠件，只换驱动与路由。"""
    from langgraph.prebuilt import ToolNode

    from aitester.orchestration.agent_graph import (
        _gate_context, _round_no, _run_control, _stream_round, _subagent_parallel,
        _tool_error_message, _unanswered, route_after_gate,
    )
    from aitester.orchestration.checkpoint import get_checkpointer
    from aitester.orchestration.gate import make_gate_node

    tool_node = ToolNode(tools, handle_tool_errors=_tool_error_message)
    bound = provider.bind_tools(tools) if tools else provider

    def agent_node(state: CaseDesignState, config: RunnableConfig) -> dict:
        return {"messages": [_stream_round(
            bound, state["messages"], _round_no(state), _run_control(config)
        )]}

    def should_continue(state: CaseDesignState) -> str:
        """agent 之后：有工具调用先去闸门；没有就回 driver 领下一段指令（react 此处直接 END）。"""
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "gate"
        return "driver"

    def tools_node(state: CaseDesignState, config: RunnableConfig) -> Any:
        """只把没答过的调用交给 ToolNode（与 react 逐行同款；折叠口径见 agent_graph）。"""
        messages = state["messages"]
        idx, calls = _unanswered(messages)
        if not calls:
            return {"messages": []}
        trimmed = messages[idx].model_copy(update={"tool_calls": calls})
        return tool_node.invoke(
            {"messages": [*messages[:idx], trimmed, *messages[idx + 1:]]}, config)

    graph = StateGraph(CaseDesignState)
    graph.add_node("driver", make_driver_node(_task_tool_of(tools)))
    graph.add_node("agent", agent_node)
    graph.add_node("gate", make_gate_node(_gate_context, _subagent_parallel(tools)))
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "driver")
    # route 字面量必须进映射表当键：route="end" 配 {END: END} 会 KeyError（P-route 实测），
    # 映射写成 {"agent": "agent", "end": END}——键是 T8 冻结的字面量，值才是 END 哨兵
    graph.add_conditional_edges("driver",
                                lambda s: s.get("case", {}).get("route", "end"),
                                {"agent": "agent", "end": END})
    graph.add_conditional_edges("agent", should_continue, {"gate": "gate", "driver": "driver"})
    graph.add_conditional_edges("gate", route_after_gate, {"tools": "tools", "agent": "agent"})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=get_checkpointer())
