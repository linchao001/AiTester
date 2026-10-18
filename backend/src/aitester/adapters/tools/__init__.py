"""接入层-外部工具适配：工具抽象、注册表与内置工具实现。"""

from pathlib import Path

from aitester.adapters.tools.base import AiTooler
from aitester.adapters.tools.command_tools import COMMAND_TOOLS, BashTool, PwshTool
from aitester.adapters.tools.file_tools import (
    SEARCH_TOOLS,
    EditTool,
    FileObservationStore,
    ReadTool,
    WriteTool,
)
from aitester.adapters.tools.kb_tools import (
    PrepareKbDeleteTool,
    PrepareKbEditTool,
    PrepareKbFrontmatterTool,
    KbSaveTool,
    KbSearchTool,
    PrepareKbWriteTool,
)
from aitester.adapters.tools.registry import ToolRegistry
from aitester.adapters.tools.web_tools import WebSearchTool


def build_default_registry(
    cwd: str = ".",
    session_id: str = "default",
    observed: FileObservationStore | None = None,
    kb=None,
    agent_id: str = "console",
    project_id: str = "",
    task=None,
    usage=None,
) -> ToolRegistry:
    """构建预装全部内置工具的注册表。

    `observed` 为跨请求共享的会话级观察记录，用于「改前必读」守卫；
    不传则新建一个仅在本注册表生命周期内有效的记录。
    `kb` 为 RemeMemoryManager（或其同签名替身）；不传则不注册知识库工具；
    传了但 `kb_enabled` 关闭（`is_enabled` 为假）同样不注册，模型不可见。
    `task` 为装配层注入的 task 工具实例（子智能体唯一入口）；不传就不注册——
    子智能体自己的注册表装配时不传它，深度 1 由此成为结构锁（R7）。
    `usage` 为本回合的上下文累加件（ContextUsage 或 None）：它必须进**每一处**构造，
    漏一处就等于给「一回合只一本账」发明第二个实现——截断留痕会记到另一本（或没有）账上。
    """
    store = observed if observed is not None else FileObservationStore()
    registry = ToolRegistry()
    for cls in (ReadTool, WriteTool, EditTool):
        registry.register(cls(cwd=cwd, session_id=session_id, observed=store, usage=usage))
    for cls in SEARCH_TOOLS:
        registry.register(cls(cwd=cwd, usage=usage))
    for cls in COMMAND_TOOLS:
        registry.register(cls(cwd=cwd, usage=usage))
    registry.register(WebSearchTool(usage=usage))
    if task is not None:
        registry.register(task)          # task 由装配层带 usage 构造（见 agent_runtime._task_tool）
    if kb is not None and getattr(kb, "is_enabled", True):
        pid = (project_id or "").strip()
        registry.register(
            KbSearchTool(kb=kb, agent_id=agent_id, project_id=pid, usage=usage)
        )
        registry.register(
            KbSaveTool(kb=kb, agent_id=agent_id, project_id=pid, usage=usage)
        )
        registry.register(PrepareKbWriteTool(kb_root=Path(kb.kb_root_dir), usage=usage))
        # KB 文件维护：硬删/正文替换/删元数据字段——均为零写盘草案工具，
        # 落盘只由用户确认后、前端调 /api/kb/{delete,edit,frontmatter_delete} 完成（模型不持执行工具）。
        registry.register(PrepareKbDeleteTool(usage=usage))
        registry.register(PrepareKbEditTool(usage=usage))
        registry.register(PrepareKbFrontmatterTool(usage=usage))
    return registry


__all__ = [
    "COMMAND_TOOLS",
    "AiTooler",
    "BashTool",
    "EditTool",
    "FileObservationStore",
    "GlobSearchTool",
    "GrepSearchTool",
    "KbSaveTool",
    "KbSearchTool",
    "PrepareKbDeleteTool",
    "PrepareKbEditTool",
    "PrepareKbFrontmatterTool",
    "PwshTool",
    "PrepareKbWriteTool",
    "ReadTool",
    "ToolRegistry",
    "WebSearchTool",
    "WriteTool",
    "build_default_registry",
]
