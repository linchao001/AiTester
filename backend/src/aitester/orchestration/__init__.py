"""编排 loop 层：LangGraph 状态图。"""

from aitester.orchestration.agent_graph import AgentState, build_agent_graph, run_agent, run_graph, stream_graph
from aitester.orchestration.auth_rules import (
    face_can_suspend,
    needs_approval,
    plan_target,
    validate_perm_mode,
)
from aitester.orchestration.checkpoint import drop_thread, get_checkpointer, new_thread_id
from aitester.orchestration.gate import (
    GATE_KEY,
    GateAuthError,
    GateContext,
    build_gate_context,
    decision_from,
    make_gate_node,
    plan_items,
)
from aitester.orchestration.graph import EchoState, build_echo_graph, run_echo
from aitester.orchestration.graph_registry import GRAPH_BUILDERS, GraphBuilder, get_graph_builder
from aitester.orchestration.run_control import RUN_CONTROL_KEY, RunControl
from aitester.orchestration.subagent import (
    DETAIL_MAX,
    SUBAGENT_KEY,
    ChildRuntime,
    detail_of,
    drive_child,
    shape_task_batch,
)

__all__ = [
    "DETAIL_MAX",
    "GATE_KEY",
    "GRAPH_BUILDERS",
    "RUN_CONTROL_KEY",
    "SUBAGENT_KEY",
    "AgentState",
    "ChildRuntime",
    "EchoState",
    "GateAuthError",
    "GateContext",
    "GraphBuilder",
    "RunControl",
    "build_agent_graph",
    "build_echo_graph",
    "build_gate_context",
    "decision_from",
    "detail_of",
    "drive_child",
    "drop_thread",
    "face_can_suspend",
    "get_checkpointer",
    "get_graph_builder",
    "make_gate_node",
    "needs_approval",
    "new_thread_id",
    "plan_items",
    "plan_target",
    "run_agent",
    "run_echo",
    "run_graph",
    "shape_task_batch",
    "stream_graph",
    "validate_perm_mode",
]
