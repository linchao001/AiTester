"""个人记忆门面：Reme search / auto_memory；失败软化，不抛给聊天回合。"""

from __future__ import annotations

import logging
import threading
from typing import Any

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

    def _enabled(self) -> bool:
        return bool(getattr(self._settings, "personal_memory_enabled", False))

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
        return str(getattr(resp, "answer", "") or "").strip()

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
        if not self._enabled():
            return
        if not getattr(self._settings, "auto_memory_enabled", False):
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
            session_id=to_reme_session_id(session_id),
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
        if not self._enabled() or not getattr(
            self._settings, "auto_memory_enabled", False,
        ):
            return

        def _run() -> None:
            try:
                self.auto_memory(
                    project_id=project_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    messages=messages,
                )
            except Exception:
                logger.exception("scheduled auto_memory failed; soft-skip")

        threading.Thread(
            target=_run, name="reme-auto-memory", daemon=True,
        ).start()
