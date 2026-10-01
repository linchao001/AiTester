"""ReMe 进程内嵌管理器：专属事件循环线程 + (project, agent) 实例池。

裁定依据 spec 2026-10-01-knowledge-base-reme-design.md：
禁 HTTP 服务、每实例独占 workspace、KB 全局共享、embedding 按 Key。
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from aitester.services.kb.config import KbConfig, build_reme_config

DEFAULT_PROJECT = "default"
DEFAULT_CONSOLE_AGENT = "console"


class KbUnavailableError(RuntimeError):
    """知识库未启用或 ReMe 实例不可用。"""


class RemeKbManager:
    def __init__(self, settings: Any, data_dir: Path) -> None:
        self._settings = settings
        self._data_dir = Path(data_dir)
        self._apps: dict[tuple[str, str], Any] = {}
        self._start_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    def start(self) -> None:
        if not getattr(self._settings, "kb_enabled", False) or self._started:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="reme-kb-loop", daemon=True
        )
        self._thread.start()
        self._started = True

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro: Any) -> Future:
        if not self._started or self._loop is None:
            coro.close()
            raise KbUnavailableError("知识库未启用或未启动")
        assert self._thread is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _kb_config(self, project_id: str, agent_id: str) -> KbConfig:
        s = self._settings
        return KbConfig(
            workspace_dir=str(self._data_dir / "workspaces" / project_id / agent_id),
            kb_id=s.kb_id,
            kb_bases_dir=s.kb_bases_dir,
            create_missing=s.kb_create_missing,
            embedding_api_key=s.kb_embedding_api_key or getattr(s, "dashscope_api_key", ""),
            embedding_base_url=s.kb_embedding_base_url,
            embedding_model=s.kb_embedding_model,
            embedding_dimensions=s.kb_embedding_dimensions,
        )

    async def _start_app(self, key: tuple[str, str]):
        project_id, agent_id = key
        from reme import Application

        app = Application(**build_reme_config(self._kb_config(project_id, agent_id)))
        try:
            await app.start()
        except BaseException:
            # 启动失败必须摘除在途任务，否则该 key 被永久污染无法重试
            if self._start_tasks.get(key) is asyncio.current_task():
                self._start_tasks.pop(key, None)
            raise
        self._apps[key] = app
        if self._start_tasks.get(key) is asyncio.current_task():
            self._start_tasks.pop(key, None)
        return app

    async def _get_app(self, project_id: str, agent_id: str):
        """同 key 单飞：并发调用共享同一个创建任务，绝不会出现第二个实例。

        只在 manager 专属事件循环内被调用，故对 `_apps`/`_start_tasks` 的
        读改写之间不引入新的 await，天然原子。
        """
        key = (project_id, agent_id)
        app = self._apps.get(key)
        if app is not None:
            return app
        task = self._start_tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(self._start_app(key))
            self._start_tasks[key] = task
        # shield：某个调用方被取消时不能拖垮其他调用方正在等待的启动任务
        return await asyncio.shield(task)

    async def _run(self, project_id: str, agent_id: str, name: str, kwargs: dict):
        app = await self._get_app(project_id, agent_id)
        return await app.run_job(name, **kwargs)

    def run_job_sync(
        self,
        name: str,
        *,
        project_id: str = DEFAULT_PROJECT,
        agent_id: str = DEFAULT_CONSOLE_AGENT,
        timeout: float = 60.0,
        **kwargs: Any,
    ):
        return self._submit(self._run(project_id, agent_id, name, kwargs)).result(timeout)

    async def run_job(
        self,
        name: str,
        *,
        project_id: str = DEFAULT_PROJECT,
        agent_id: str = DEFAULT_CONSOLE_AGENT,
        **kwargs: Any,
    ):
        future = self._submit(self._run(project_id, agent_id, name, kwargs))
        return await asyncio.wrap_future(future)

    def close_all(self, timeout: float = 30.0) -> None:
        if not self._started:
            return

        async def _close() -> None:
            # 先等待在途启动落位，保证 close_all 关掉所有已启动/曾启动的实例
            pending = [t for t in self._start_tasks.values() if not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for app in list(self._apps.values()):
                await app.close()
            self._apps.clear()
            self._start_tasks.clear()

        try:
            self._submit(_close()).result(timeout)
        finally:
            assert self._loop is not None and self._thread is not None
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout)
            self._started = False
            self._loop = None
            self._thread = None
