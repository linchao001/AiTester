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


def make_gate_node(lookup: Callable[[RunnableConfig | None], GateContext | None]):
    """返回 gate 节点体。spec「架构与拦截点」的三件事一气呵成，不拆函数：
    逐条判定 → 逐条 interrupt → 按决策改写清单 + 合成拒绝。
    """

    def gate_node(state: dict, config: RunnableConfig) -> dict[str, list]:
        ctx = lookup(config)
        if ctx is None:
            return {"messages": []}                       # R3：空 list，绝不返回 {}
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return {"messages": []}
        items = plan_items(list(last.tool_calls), ctx)
        if not items:
            return {"messages": []}
        rejected_ids: set[str] = set()
        for item in items:
            decision = decision_from(interrupt(item.payload))
            if decision["decision"] == APPROVE:
                if decision["remember"]:
                    ctx.remembered.add(item.plan.remember_key)   # 唯一允许的副作用（幂等）
            else:
                rejected_ids.add(item.call_id)
        if not rejected_ids:
            return {"messages": []}
        kept = [c for c in last.tool_calls if str(c.get("id")) not in rejected_ids]
        replaced = last.model_copy(update={"tool_calls": kept})   # R4：同 id 覆盖
        return {"messages": [replaced,
                             *[i.rejected_message() for i in items if i.call_id in rejected_ids]]}

    return gate_node
