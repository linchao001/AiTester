"""待批注册表：run_id → 挂起中的回合。进程内内存态，后端重启即丢（spec 裁定 2）。

与 RunRegistry 同层同风格：只挂 app.state，不落盘、不鉴权、不设 TTL（裁定 10）。
`run_id` 与 `thread_id` 全程同值（P4），续跑沿用同一个才有中断状态可接。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:                       # 只在类型层：services.chat 反向 import 本模块
    from aitester.services.chat import PreparedRun

# 三条 detail 逐字取自 spec「错误处理」表；第四条是 R6 补的口子（spec 表未覆盖 UI 发不出的请求）
PENDING_GONE_DETAIL = "这条回答已经结束，无法再批准"
CALL_DECIDED_DETAIL = "这条授权请求已经处理过了"
RESUME_NOT_READY_DETAIL = "这条回答还在等你批准"


class PendingGoneError(ValueError):
    """条目不在表中（未知、已被停止摘除、或后端已重启）：路由按 404 落 detail。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class CallDecidedError(ValueError):
    """这条 call_id 已答过（或不认识）：路由按 409 落 detail。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ResumeNotReadyError(ValueError):
    """还没有可喂给中断点的决策就要求续跑：路由按 400 落 detail。"""

    def __init__(self, detail: str = RESUME_NOT_READY_DETAIL) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass
class PendingEntry:
    run_id: str
    thread_id: str
    prepared: "PreparedRun"
    perm_mode: str                 # 创建时那一档（锁档）：续跑按它判定，不读请求当下的字段值
    project_id: str
    project_dir: str
    session_key: str
    session_id: str
    agent_id: str
    prefix_text: str               # 已投递正文前缀：只存内存，供刷新恢复渲染（裁定 8）
    steps: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    queue: list[dict[str, Any]] = field(default_factory=list)     # wait 事件载荷，顺序 = gate 逐条顺序
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    consumed: list[str] = field(default_factory=list)             # 已喂给 Command(resume=…) 的 call_id

    @property
    def decided(self) -> list[dict[str, Any]]:
        """已答项按队列顺序回显，六键跟着一起给（R14）：刷新后的痕迹要还能读出「批准了哪一次写入」。"""
        return [{**c, "decision": self.answers[c["call_id"]]["decision"]}
                for c in self.queue if c["call_id"] in self.answers]

    def waiting(self) -> list[dict[str, Any]]:
        return [c for c in self.queue if c["call_id"] not in self.answers]


class PendingRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, PendingEntry] = {}
        self._remembered: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def open(self, entry: PendingEntry) -> None:
        with self._lock:
            self._runs[entry.run_id] = entry

    def peek(self, run_id: str) -> PendingEntry | None:
        with self._lock:
            return self._runs.get(run_id)

    def take(self, run_id: str) -> PendingEntry | None:
        """原子摘除：停止与批准同时到达时「先摘除者胜」（spec 生命周期节）。"""
        with self._lock:
            return self._runs.pop(run_id, None)

    def update_hold(self, entry: PendingEntry, *, prefix: str,
                    steps: list[dict[str, Any]], waiting: list[dict[str, Any]]) -> None:
        """续跑又撞下一条中断：原地更新前缀、过程行与队列（按 call_id 去重）。"""
        with self._lock:
            if self._runs.get(entry.run_id) is not entry:
                return                                   # 已被摘除：本次挂起作废，绝不复活
            known = {c["call_id"] for c in entry.queue}
            entry.prefix_text = prefix
            entry.steps = list(steps)
            entry.queue.extend(c for c in waiting if c["call_id"] not in known)

    def answer(self, run_id: str, call_id: str, decision: str, remember: bool) -> None:
        with self._lock:
            entry = self._runs.get(run_id)
            if entry is None:
                raise PendingGoneError(PENDING_GONE_DETAIL)
            known = {c["call_id"] for c in entry.queue}
            if call_id not in known or call_id in entry.answers:
                raise CallDecidedError(CALL_DECIDED_DETAIL)
            entry.answers[call_id] = {"decision": decision, "remember": remember}

    def _next_resumable(self, run_id: str) -> tuple[PendingEntry, str]:
        entry = self._runs.get(run_id)
        if entry is None:
            raise PendingGoneError(PENDING_GONE_DETAIL)
        for call in entry.queue:
            cid = call["call_id"]
            if cid in entry.answers and cid not in entry.consumed:
                return entry, cid
        raise ResumeNotReadyError()

    def peek_resume(self, run_id: str) -> dict[str, Any]:
        """只回答「现在能不能续」，不烧决策：路由据此在返回迭代器之前就能回 400。

        真正的消费必须等到续跑真的开跑（Task 6 在生成器体里调 take_resume）——提前落 consumed
        会让「取到决策却没喂出去」的那次请求把下一条决策喂进上一条的中断位（位置匹配，实测）。
        """
        with self._lock:
            entry, cid = self._next_resumable(run_id)
            return {"call_id": cid, **entry.answers[cid]}

    def take_resume(self, run_id: str) -> dict[str, Any]:
        """按队列顺序取下一条「已答未喂」的决策并标记已喂，喂给 Command(resume=…)。"""
        with self._lock:
            entry, cid = self._next_resumable(run_id)
            entry.consumed.append(cid)
            return {"call_id": cid, **entry.answers[cid]}

    def view(self, session_id: str) -> list[PendingEntry]:
        with self._lock:
            return sorted((e for e in self._runs.values() if e.session_id == session_id),
                          key=lambda e: e.created_at)

    def view_for(self, agent_id: str, project_id: str) -> list[PendingEntry]:
        """UI 的取数口：按「智能体 × 项目」取，不按会话（R14）。

        挂起时那条会话什么都没落盘（裁定 8），刷新后前端既不知道它的 id、项目列表里也查不到它；
        按会话查等于让「新会话第一条就界外写」的走查项 11 必然失败。
        """
        with self._lock:
            return sorted((e for e in self._runs.values()
                           if e.agent_id == agent_id and e.project_id == project_id),
                          key=lambda e: e.created_at)

    def remembered_for(self, session_key: str) -> set[str]:
        """返回活对象：gate 拿到决策后往里加，判定函数读同一个集合。"""
        with self._lock:
            return self._remembered.setdefault(session_key, set())

    def drop_session(self, session_id: str) -> list[str]:
        """删会话级联（裁定 10 第三条）：返回要清的检查点 thread_id，由调用方交给 drop_thread。"""
        with self._lock:
            gone = [rid for rid, e in self._runs.items() if e.session_id == session_id]
            for rid in gone:
                del self._runs[rid]
            for key in [k for k in self._remembered if k.split(":", 1)[1] == session_id]:
                del self._remembered[key]
            return gone
