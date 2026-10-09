"""按项目 dir × 智能体打开 SessionStore；不做跨请求业务缓存。"""

from __future__ import annotations

from pathlib import Path

from aitester.services.project_config import ProjectService
from aitester.services.session_store import SessionStore, open_session_store


class SessionLocator:
    def __init__(self, projects: ProjectService) -> None:
        self._projects = projects

    def for_agent(self, project_id: str, agent_id: str) -> SessionStore:
        project = self._projects.get(project_id)
        return open_session_store(project["dir"], agent_id)

    def count_by_project(self, project_id: str) -> int:
        project = self._projects.get(project_id)
        hist = Path(project["dir"]).expanduser().resolve() / "session_history"
        if not hist.is_dir():
            return 0
        total = 0
        for child in hist.iterdir():
            if not child.is_dir():
                continue
            if not (child / "index.json").is_file():
                continue
            store = SessionStore(child, agent_id=child.name)
            # list 需要 project_id 过滤；该目录只服务本项目，但仍用真 project_id
            total += len(store.list(child.name, project_id))
        return total
