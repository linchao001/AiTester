"""知识库门面：既有 Reme KB job 的薄封装（API / 工具 / KbClient 共用）。"""

from __future__ import annotations

from typing import Any

from aitester.memory.reme.manager import DEFAULT_CONSOLE_AGENT, DEFAULT_PROJECT


class KnowledgeMemory:
    def __init__(self, manager: Any) -> None:
        self._kb = manager

    @property
    def kb_root_dir(self):
        return self._kb.kb_root_dir

    @property
    def is_enabled(self) -> bool:
        return bool(getattr(self._kb, "is_enabled", False))

    def run_job_sync(self, name: str, **kwargs: Any):
        return self._kb.run_job_sync(name, **kwargs)

    async def run_job(self, name: str, **kwargs: Any):
        return await self._kb.run_job(name, **kwargs)

    def status(
        self,
        *,
        project_id: str = DEFAULT_PROJECT,
        agent_id: str = DEFAULT_CONSOLE_AGENT,
    ):
        return self.run_job_sync("status", project_id=project_id, agent_id=agent_id)

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        bucket: str = "",
        project_id: str = DEFAULT_PROJECT,
        agent_id: str = DEFAULT_CONSOLE_AGENT,
    ):
        return self.run_job_sync(
            "knowledge_search",
            project_id=project_id,
            agent_id=agent_id,
            query=query,
            limit=limit,
            bucket=bucket or None,
        )

    def save(
        self,
        title: str,
        content: str,
        bucket: str,
        *,
        project_id: str = DEFAULT_PROJECT,
        agent_id: str = DEFAULT_CONSOLE_AGENT,
    ):
        return self.run_job_sync(
            "save_to_knowledge",
            project_id=project_id,
            agent_id=agent_id,
            title=title,
            content=content,
            bucket=bucket,
        )
