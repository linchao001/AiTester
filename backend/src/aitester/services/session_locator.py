"""按项目 dir × 智能体打开 SessionStore。

同一进程内对同一 (resolved_dir, agent_id) 复用 store 实例：Windows 上 os.replace
写 index 时若另一份 SessionStore 正打开同文件会 PermissionError（abort 落盘竞态）。
"""

from __future__ import annotations

import threading
from pathlib import Path

from aitester.services.project_config import ProjectService
from aitester.services.session_store import SessionStore, open_session_store


class SessionLocator:
    def __init__(self, projects: ProjectService) -> None:
        self._projects = projects
        self._cache: dict[tuple[str, str], SessionStore] = {}
        self._lock = threading.Lock()

    def for_agent(self, project_id: str, agent_id: str) -> SessionStore:
        project = self._projects.get(project_id)
        root = Path(project["dir"]).expanduser().resolve()
        key = (str(root), agent_id)
        with self._lock:
            store = self._cache.get(key)
            if store is None:
                store = open_session_store(root, agent_id)
                self._cache[key] = store
            return store

    def count_by_project(self, project_id: str) -> int:
        project = self._projects.get(project_id)
        try:
            hist = Path(project["dir"]).expanduser().resolve() / "session_history"
        except (OSError, ValueError):
            # 与 dir_exists 同口径：畸形 dir（含 NUL）答 0，不把 GET /projects 打成 500
            return 0
        if not hist.is_dir():
            return 0
        total = 0
        for child in hist.iterdir():
            if not child.is_dir():
                continue
            if not (child / "index.json").is_file():
                continue
            # 计数走缓存口，避免与在途 append 抢文件
            store = self.for_agent(project_id, child.name)
            total += len(store.list(child.name, project_id))
        return total
