"""会话级 auto_memory 回合累计（对齐 QwenPaw MemoryMiddleware pending 语义）。

按用户 turn marker 计数，不是墙钟、也不是模型调用次数。消息快照随 pending
一起持有，便于删会话时仍能 flush（SessionStore 可能已删）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

_SEEN_CAP = 1000


@dataclass
class FlushBatch:
    project_id: str
    agent_id: str
    session_id: str
    messages: list[dict[str, Any]]


@dataclass
class _PendingTurn:
    marker: str
    messages: list[dict[str, Any]]


@dataclass
class _SessionState:
    project_id: str
    agent_id: str
    pending: list[_PendingTurn] = field(default_factory=list)
    seen: dict[str, None] = field(default_factory=dict)


class AutoMemoryTurnTracker:
    def __init__(self) -> None:
        self._sessions: dict[str, _SessionState] = {}
        self._lock = threading.Lock()

    def note_turn(
        self,
        *,
        session_id: str,
        project_id: str,
        agent_id: str,
        turn_marker: str,
        messages: list[dict[str, Any]],
        interval: int,
    ) -> FlushBatch | None:
        """记录一个用户回合；达到 interval 时返回应 flush 的批次。

        interval <= 0：清空 pending，不调度周期 flush。
        """
        sid = (session_id or "").strip()
        marker = (turn_marker or "").strip()
        if not sid or not marker:
            return None
        with self._lock:
            state = self._sessions.get(sid)
            if state is None:
                state = _SessionState(project_id=project_id, agent_id=agent_id)
                self._sessions[sid] = state
            else:
                state.project_id = project_id or state.project_id
                state.agent_id = agent_id or state.agent_id

            if marker in state.seen:
                return None
            state.seen[marker] = None
            while len(state.seen) > _SEEN_CAP:
                state.seen.pop(next(iter(state.seen)))

            if interval <= 0:
                state.pending.clear()
                return None

            state.pending.append(
                _PendingTurn(marker=marker, messages=list(messages)),
            )
            if len(state.pending) < interval:
                return None
            return self._pop_batch_locked(sid, state, count=interval)

    def take_all(self, session_id: str) -> FlushBatch | None:
        """取出并清空该会话全部 pending（删会话 / 未来压缩提前 flush）。"""
        sid = (session_id or "").strip()
        if not sid:
            return None
        with self._lock:
            state = self._sessions.pop(sid, None)
            if state is None or not state.pending:
                return None
            messages: list[dict[str, Any]] = []
            for turn in state.pending:
                messages.extend(turn.messages)
            return FlushBatch(
                project_id=state.project_id,
                agent_id=state.agent_id,
                session_id=sid,
                messages=messages,
            )

    def reset(self, session_id: str) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        with self._lock:
            self._sessions.pop(sid, None)

    def pending_count(self, session_id: str) -> int:
        sid = (session_id or "").strip()
        with self._lock:
            state = self._sessions.get(sid)
            return len(state.pending) if state else 0

    def _pop_batch_locked(
        self,
        session_id: str,
        state: _SessionState,
        *,
        count: int,
    ) -> FlushBatch:
        taken = state.pending[:count]
        del state.pending[:count]
        messages: list[dict[str, Any]] = []
        for turn in taken:
            messages.extend(turn.messages)
        if not state.pending and not state.seen:
            self._sessions.pop(session_id, None)
        return FlushBatch(
            project_id=state.project_id,
            agent_id=state.agent_id,
            session_id=session_id,
            messages=messages,
        )
