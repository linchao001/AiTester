"""个人记忆门面：Reme search / auto_memory；失败软化，不抛给聊天回合。"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from typing import Any

from aitester.memory.reme.auto_memory_turns import AutoMemoryTurnTracker
from aitester.memory.reme.llm_bridge import resolve_chat_provider
from aitester.memory.reme.messages import to_reme_messages, to_reme_session_id

logger = logging.getLogger(__name__)


class PersonalMemory:
    def __init__(
        self,
        manager: Any,
        settings: Any,
        model_config: Any,
        *,
        capability: Any | None = None,
    ) -> None:
        self._kb = manager
        self._settings = settings
        self._model_config = model_config
        self._capability = capability
        self._turns = AutoMemoryTurnTracker()
        self._task_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()

    def _enabled(self) -> bool:
        return bool(getattr(self._settings, "personal_memory_enabled", False))

    def _auto_memory_on(self) -> bool:
        return self._enabled() and bool(
            getattr(self._settings, "auto_memory_enabled", False),
        )

    def _interval(self) -> int:
        raw = getattr(self._settings, "auto_memory_interval", 5)
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    def search(
        self,
        *,
        project_id: str,
        agent_id: str,
        query: str,
        limit: int | None = None,
    ) -> str:
        if not self._enabled() or not query.strip():
            return ""
        lim = limit if limit is not None else int(
            getattr(self._settings, "auto_memory_search_limit", 5) or 5
        )
        resp = self._kb.run_job_sync(
            "search",
            project_id=project_id,
            agent_id=agent_id,
            query=query,
            limit=max(1, lim),
            timeout=60.0,
        )
        if not getattr(resp, "success", False):
            return ""
        from aitester.project_runtime import reme_paths_for_project_cwd

        return reme_paths_for_project_cwd(
            str(getattr(resp, "answer", "") or "").strip()
        )

    def auto_search_for_turn(
        self,
        *,
        project_id: str,
        agent_id: str,
        query: str,
    ) -> str:
        if not self._enabled():
            return ""
        if not getattr(self._settings, "auto_memory_search_enabled", False):
            return ""
        try:
            return self.search(
                project_id=project_id, agent_id=agent_id, query=query,
            )
        except Exception:
            logger.exception("personal memory search failed; soft-skip")
            return ""

    def auto_memory(
        self,
        *,
        project_id: str,
        agent_id: str,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        if not self._auto_memory_on():
            return
        rows = to_reme_messages(messages)
        if not rows or not session_id:
            return
        provider = resolve_chat_provider(
            self._model_config,
            agent_id=agent_id,
            capability=self._capability,
        )
        self._kb.inject_llm(
            provider, project_id=project_id, agent_id=agent_id, timeout=60.0,
        )
        resp = self._kb.run_job_sync(
            "auto_memory",
            project_id=project_id,
            agent_id=agent_id,
            messages=rows,
            session_id=to_reme_session_id(session_id, agent_id=agent_id),
            timeout=120.0,
        )
        if not getattr(resp, "success", False):
            logger.warning(
                "auto_memory failed: %s", getattr(resp, "answer", ""),
            )

    def schedule_auto_memory(
        self,
        *,
        project_id: str,
        agent_id: str,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """入队后台串行执行（FIFO 单 worker，对齐 QwenPaw add_summarize_task）。"""
        if not self._auto_memory_on():
            return
        if not (session_id or "").strip() or not messages:
            return
        self._ensure_worker()
        self._task_queue.put({
            "project_id": project_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "messages": list(messages),
        })

    def note_user_turn(
        self,
        *,
        project_id: str,
        agent_id: str,
        session_id: str,
        messages: list[dict[str, Any]],
        turn_marker: str | None = None,
    ) -> None:
        """回复落盘后记一个用户回合；达到 interval 时入队 flush。"""
        if not self._auto_memory_on():
            return
        marker = (turn_marker or "").strip() or f"turn_{uuid.uuid4().hex}"
        try:
            batch = self._turns.note_turn(
                session_id=session_id,
                project_id=project_id,
                agent_id=agent_id,
                turn_marker=marker,
                messages=messages,
                interval=self._interval(),
            )
        except Exception:
            logger.exception("auto_memory note_turn failed; soft-skip")
            return
        if batch is None or not batch.messages:
            return
        self.schedule_auto_memory(
            project_id=batch.project_id,
            agent_id=batch.agent_id,
            session_id=batch.session_id,
            messages=batch.messages,
        )

    def flush_session(self, session_id: str) -> None:
        """删会话前把未处理 pending 立刻入队（对齐 QwenPaw /new 摘要语义）。"""
        if not self._auto_memory_on():
            self._turns.reset(session_id)
            return
        try:
            batch = self._turns.take_all(session_id)
        except Exception:
            logger.exception("auto_memory flush_session failed; soft-skip")
            self._turns.reset(session_id)
            return
        if batch is None or not batch.messages:
            return
        self.schedule_auto_memory(
            project_id=batch.project_id,
            agent_id=batch.agent_id,
            session_id=batch.session_id,
            messages=batch.messages,
        )

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="reme-auto-memory",
                daemon=True,
            )
            self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            item = self._task_queue.get()
            try:
                if item is None:
                    return
                try:
                    self.auto_memory(**item)
                except Exception:
                    logger.exception("scheduled auto_memory failed; soft-skip")
            finally:
                self._task_queue.task_done()
