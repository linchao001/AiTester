"""ReMe 进程内嵌管理器：专属事件循环线程 + 按 workspace 路径的实例池。

记忆层生命周期中枢（个人记忆 + 知识库）。裁定依据：
spec 2026-10-10-reme-workspace-project-dir-design.md（覆盖旧 (project, agent) 池）；
项目 workspace 落点见 project_runtime（``{dir}/.AiTester``）。
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

from aitester.case_design.constants import NODE_BUCKETS
from aitester.memory.reme.config import KbConfig, build_reme_config
from aitester.memory.reme.paths import resolve_kb_root
from aitester.project_runtime import project_runtime_root

DEFAULT_CONSOLE_AGENT = "console"

# project_id → 项目绑定目录（原始或绝对路径均可）；解析失败应抛错
ProjectDirResolver = Callable[[str], str | Path]


class KbUnavailableError(RuntimeError):
    """知识库 / Reme 记忆实例未启用或不可用。"""


def _ensure_node_buckets(cfg: KbConfig) -> None:
    """三层节点桶物理落地：workspace/knowledge 是整根 junction，实体侧建目录即挂载侧可见。

    调用点保持在 Application 构造前，但这是 reme watch 形态的**漂移保险**、不是既成保证：
    实测（task-5-report 负向探针）reme 的 watch 是 knowledge 整根递归轮询，
    后建的桶下一轮也必然被捕获——勿从「必须先建桶」推出任何运行期保证（评审 Minor 6）。
    KB 根缺失且允许自建时先走 ensure_kb（补 KB.md 骨架——mount 只在根不存在时建骨架，
    根已存在则直接挂载）。根缺失且不允许自建时无声返回，后续 mount 照旧响亮失败。
    """
    from reme.knowledge.store import ensure_kb, kb_root  # 与 _start_app 同：延迟导入

    root = kb_root(cfg.kb_id, knowledge_bases_dir=cfg.kb_bases_dir or None)
    if not root.is_dir():
        if not cfg.create_missing:
            return
        ensure_kb(cfg.kb_id, knowledge_bases_dir=cfg.kb_bases_dir or None)
    for bucket in NODE_BUCKETS:
        (root / bucket).mkdir(parents=True, exist_ok=True)


class RemeMemoryManager:
    def __init__(
        self,
        settings: Any,
        data_dir: Path | None = None,
        *,
        project_dir_resolver: ProjectDirResolver | None = None,
    ) -> None:
        self._settings = settings
        # data_dir 曾用于 _platform fallback；现忽略，保留形参兼容旧调用方
        del data_dir
        self._project_dir_resolver = project_dir_resolver
        # 池键 = 解析后的 workspace 绝对路径（同路径共享一实例；agent_id 不参与）
        self._apps: dict[str, Any] = {}
        self._start_tasks: dict[str, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def is_enabled(self) -> bool:
        """settings.kb_enabled 的真实开关；与 is_started 不同，不受启停生命周期影响。"""
        return bool(getattr(self._settings, "kb_enabled", False))

    @property
    def kb_root_dir(self) -> Path:
        """共享 KB 实体目录（browse 接口与草案工具共用的唯一真相根）。"""
        return resolve_kb_root(self._settings)

    def workspace_dir(self, project_id: str = "", agent_id: str = "") -> Path:
        """解析 Reme workspace 根。agent_id 保留形参兼容调用方，不参与路径。

        必须绑定真实项目：``{project.dir}/.AiTester``（产物收口；工具 cwd 仍为项目根）。
        无项目 / 空 project_id 一律拒绝（不再提供平台 fallback workspace）。
        """
        del agent_id  # 2A：实例与路径按项目（路径）粒度，与 agent 无关
        pid = (project_id or "").strip()
        if not pid:
            raise KbUnavailableError("知识库操作必须绑定项目")
        if self._project_dir_resolver is None:
            raise KbUnavailableError(
                f"无法解析项目「{pid}」的工作目录：未配置 project_dir_resolver"
            )
        try:
            raw = self._project_dir_resolver(pid)
        except Exception as exc:
            raise KbUnavailableError(
                f"无法解析项目「{pid}」的工作目录: {exc}"
            ) from exc
        if raw is None or str(raw).strip() == "":
            raise KbUnavailableError(f"项目「{pid}」未配置本地目录")
        return project_runtime_root(raw)

    def pool_key(self, project_id: str = "", agent_id: str = "") -> str:
        return str(self.workspace_dir(project_id, agent_id))

    def start(self) -> None:
        if not getattr(self._settings, "kb_enabled", False) or self._started:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="reme-memory-loop", daemon=True
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

    def _kb_config(self, workspace: Path) -> KbConfig:
        s = self._settings
        return KbConfig(
            workspace_dir=str(workspace),
            kb_id=s.kb_id,
            kb_bases_dir=s.kb_bases_dir,
            create_missing=s.kb_create_missing,
            embedding_api_key=s.kb_embedding_api_key or getattr(s, "dashscope_api_key", ""),
            embedding_base_url=s.kb_embedding_base_url,
            embedding_model=s.kb_embedding_model,
            embedding_dimensions=s.kb_embedding_dimensions,
        )

    async def _start_app(self, key: str):
        try:
            # 构造期同样可能抛错（reme Application.__init__ 的挂载检查/mkdir/组件
            # 装配均会真抛），守卫必须覆盖构造+start 全程，否则失败任务滞留
            # _start_tasks，该 key 之后每次 _get_app 都重放旧异常而无法重试
            from reme import Application

            workspace = Path(key)
            workspace.mkdir(parents=True, exist_ok=True)
            cfg = self._kb_config(workspace)
            # 先建桶再构造 Application：reme watch 形态的漂移保险，非既成保证
            # （当前根递归轮询下后建桶同样可被索引，详见 _ensure_node_buckets docstring）
            _ensure_node_buckets(cfg)
            app = Application(**build_reme_config(cfg))
            await app.start()
            # 背景 index_update_loop 需先完成首轮 init_changes 定基线；
            # 否则紧随其后的写 job（如 case_node_upsert）会与 watch 竞态，
            # 增量索引长期看不到新文件（reme 0.4.1.13 + CF2 实测）。
            try:
                await app.run_job("status")
            except Exception:
                pass
            await asyncio.sleep(0.5)
        except BaseException as exc:
            # 启动失败必须摘除在途任务，否则该 key 被永久污染无法重试
            if self._start_tasks.get(key) is asyncio.current_task():
                self._start_tasks.pop(key, None)
            # 终审裁定：实例启动失败统一收敛为 KbUnavailableError，
            # 路由层据此映射 503，而非裸异常穿透成 500
            raise KbUnavailableError(f"知识库实例启动失败: {exc}") from exc
        self._apps[key] = app
        if self._start_tasks.get(key) is asyncio.current_task():
            self._start_tasks.pop(key, None)
        return app

    async def _get_app(self, project_id: str, agent_id: str = ""):
        """同路径单飞：并发调用共享同一个创建任务，绝不会出现第二个实例。

        只在 manager 专属事件循环内被调用，故对 `_apps`/`_start_tasks` 的
        读改写之间不引入新的 await，天然原子。agent_id 不参与池键（2A）。
        """
        key = self.pool_key(project_id, agent_id)
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

    async def _inject_llm(self, project_id: str, agent_id: str, model: Any) -> None:
        """把 AiTester 聊天对象注入 as_llm（Reme LangChainChatModel 适配）。"""
        app = await self._get_app(project_id, agent_id)
        await app.update_component("as_llm", "default", model=model)

    def inject_llm(
        self,
        model: Any,
        *,
        project_id: str = "",
        agent_id: str = DEFAULT_CONSOLE_AGENT,
        timeout: float = 60.0,
    ) -> None:
        self._submit(self._inject_llm(project_id, agent_id, model)).result(timeout)

    def run_job_sync(
        self,
        name: str,
        *,
        project_id: str = "",
        agent_id: str = DEFAULT_CONSOLE_AGENT,
        timeout: float = 60.0,
        **kwargs: Any,
    ):
        return self._submit(self._run(project_id, agent_id, name, kwargs)).result(timeout)

    async def run_job(
        self,
        name: str,
        *,
        project_id: str = "",
        agent_id: str = DEFAULT_CONSOLE_AGENT,
        **kwargs: Any,
    ):
        future = self._submit(self._run(project_id, agent_id, name, kwargs))
        return await asyncio.wrap_future(future)

    def drop_workspace(self, workspace: str | Path, timeout: float = 30.0) -> None:
        """关闭并摘掉指定 workspace 路径上的实例（项目删除或目录失效时）。"""
        if not self._started:
            return
        key = str(Path(workspace).expanduser().resolve())

        async def _drop() -> None:
            task = self._start_tasks.get(key)
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)
            self._start_tasks.pop(key, None)
            app = self._apps.pop(key, None)
            if app is not None:
                await app.close()

        self._submit(_drop()).result(timeout)

    def drop_project(self, project_id: str, timeout: float = 30.0) -> None:
        """按 project_id 解析路径后 drop（解析失败则静默，配置已删时常见）。"""
        try:
            ws = self.workspace_dir(project_id)
        except KbUnavailableError:
            return
        self.drop_workspace(ws, timeout=timeout)

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
