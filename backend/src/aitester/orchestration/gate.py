"""授权 gate：纯判定 + 逐条 interrupt，工具内部零挂起（P5 的教训）。

节点体会被 langgraph 在每次 resume 时从头重跑，已答过的 interrupt 直接返回缓存值，
所以除了往 remembered 集合加键之外，这里不得有任何副作用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from aitester.orchestration.auth_rules import AuthTarget, needs_approval, plan_target
from aitester.orchestration.subagent import SUBAGENT_KEY, shape_task_batch, shape_task_batch

# 进 config.configurable 的键：与 RUN_CONTROL_KEY 同一条注入通道（GraphBuilder 签名不许多带参数）
GATE_KEY = "aitester_gate"

APPROVE = "approve"
REJECT = "reject"
REJECT_PREFIX = "用户拒绝了此操作："


class GateAuthError(ValueError):
    """决策值非法 = 装配 bug：不外泄原文，路由按固定中文处理。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass
class GateContext:
    perm_mode: str
    project_dir: str          # 已 expanduser 再 resolve 的字符串（与工具 cwd 认同同一个展开）
    session_key: str          # f"{agent_id}:{session_id}"：记住表的分组键（R5）
    remembered: set[str] = field(default_factory=set)


@dataclass
class AuthItem:
    call: dict[str, Any]
    plan: AuthTarget
    call_id: str

    @property
    def tool_id(self) -> str:
        return str(self.call.get("name") or "")

    @property
    def payload(self) -> dict[str, Any]:
        """`wait` 事件载荷 = 卡片所需全部（R11：命令全文，不适用 DETAIL_MAX）。"""
        return {"type": "wait", "call_id": self.call_id, "tool": self.tool_id,
                "action": self.plan.action, "target": self.plan.target,
                "command": self.plan.command, "cwd": self.plan.cwd}

    def rejected_message(self) -> ToolMessage:
        shown = self.plan.target or self.plan.command
        return ToolMessage(
            content=f"{REJECT_PREFIX}{self.plan.action} {shown}".strip(),
            tool_call_id=self.call_id, name=self.tool_id, status="error")


def build_gate_context(perm_mode: str, project_dir: str, session_key: str,
                       remembered: set[str]) -> GateContext | None:
    """free 档与平台智能体（无项目落点）都是 None：节点整段直通，零行为变化。"""
    if perm_mode == "free" or not project_dir:
        return None
    return GateContext(perm_mode=perm_mode, project_dir=project_dir,
                       session_key=session_key, remembered=remembered)


def decision_from(value: Any) -> dict[str, Any]:
    """`interrupt()` 的返回值 → 决策。只认两个字面值，其余一律响亮失败。"""
    if not isinstance(value, dict):
        raise GateAuthError("授权决策格式不正确")
    decision = str(value.get("decision") or "")
    if decision not in (APPROVE, REJECT):
        raise GateAuthError("授权决策格式不正确")
    return {"decision": decision, "remember": bool(value.get("remember"))}


def plan_items(calls: list[dict[str, Any]], ctx: GateContext) -> list[AuthItem]:
    """纯判定：这一批调用里哪些要过闸门（保持数组顺序 = interrupt 的逐个挂起顺序）。"""
    out: list[AuthItem] = []
    for call in calls:
        tool_id = str(call.get("name") or "")
        if plan_target(tool_id, call.get("args") or {}, ctx.project_dir) is None:
            continue
        if not needs_approval(tool_id, call.get("args") or {}, ctx.perm_mode,
                              ctx.project_dir, ctx.remembered):
            continue
        out.append(AuthItem(call=call,
                            plan=plan_target(tool_id, call.get("args") or {}, ctx.project_dir),
                            call_id=str(call.get("id") or "")))
    return out


def make_gate_node(lookup: Callable[[RunnableConfig | None], GateContext | None],
                   parallel: dict[str, bool] | None = None):
    """返回 gate 节点体。spec「架构与拦截点」的三件事一气呵成，不拆函数：
    塑形 → 逐条判定 → 逐条 interrupt → 给被拒与被延后的调用合成结果。

    `parallel` 是 task 工具上那份并行表（R14 同一份对象，见 agent_graph 接线）：缺省 None
    按全串行办——不传它的老调用点（无 task 的图）行为逐字不变。
    """
    flags = parallel or {}

    def gate_node(state: dict, config: RunnableConfig) -> dict[str, list]:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return {"messages": []}
        # R4/R8：委派轮的批形先定——整批只读子则全部并行，否则只留第一个 task；同批兄弟转
        # 错误结果等下一轮重发。先于 ctx 判定，free 档（ctx=None）同防（重放与档位无关）。
        keep, deferred = shape_task_batch(list(last.tool_calls), flags)
        ctx = lookup(config)
        if ctx is None:
            return {"messages": deferred}                 # R3：空 list，绝不返回 {}
        items = plan_items(keep, ctx)
        if not items:
            return {"messages": deferred}
        # R3：wait 载荷恒带来源标注——父层 None；子层是 drive 写进 configurable 的 dict
        sub = (config.get("configurable") or {}).get(SUBAGENT_KEY)
        rejected_ids: set[str] = set()
        remembered_keys: list[str] = []
        for item in items:
            decision = decision_from(interrupt({**item.payload, "subagent": sub}))
            if decision["decision"] == APPROVE:
                if decision["remember"]:
                    remembered_keys.append(item.plan.remember_key)
            else:
                rejected_ids.add(item.call_id)
        # 唯一允许的副作用（同键同值覆盖幂等），但必须等整条循环跑完才落表：
        # langgraph 的续跑值按「第几次挂起」位置匹配（实测 scratchpad.resume[idx]），
        # 中途改 remembered 会让下一次重跑的 plan_items 变短，后面的调用继承前一条的决策。
        ctx.remembered.update(remembered_keys)
        # 原样保留那条 AIMessage 的 tool_calls，只补被拒调用的结果：provider 的硬约束是
        # 「声明了的每个 tool_call_id 都要有对应 tool 消息」，把被拒的剔出清单反而失衡
        # （真机 400，见 test_chat_auth.check_tool_protocol）。不执行由 tools 节点负责。
        return {"messages": [*deferred, *[i.rejected_message()
                                          for i in items if i.call_id in rejected_ids]]}

    return gate_node
