"""接入层-外部工具适配：工具抽象、注册表与内置工具实现。"""

from aitester.adapters.tools.base import AiTooler
from aitester.adapters.tools.command_tools import COMMAND_TOOLS, BashTool, PwshTool
from aitester.adapters.tools.file_tools import (
    SEARCH_TOOLS,
    EditTool,
    FileObservationStore,
    ReadTool,
    WriteTool,
)
from aitester.adapters.tools.kb_tools import KbSaveTool, KbSearchTool
from aitester.adapters.tools.registry import ToolRegistry
from aitester.adapters.tools.web_tools import WebSearchTool


def build_default_registry(
    cwd: str = ".",
    session_id: str = "default",
    observed: FileObservationStore | None = None,
    kb=None,
    agent_id: str = "console",
) -> ToolRegistry:
    """构建预装全部内置工具的注册表。

    `observed` 为跨请求共享的会话级观察记录，用于「改前必读」守卫；
    不传则新建一个仅在本注册表生命周期内有效的记录。
    `kb` 为 RemeKbManager（或其同签名替身）；不传则不注册知识库工具；
    传了但 `kb_enabled` 关闭（`is_enabled` 为假）同样不注册，模型不可见。
    """
    store = observed if observed is not None else FileObservationStore()
    registry = ToolRegistry()
    for cls in (ReadTool, WriteTool, EditTool):
        registry.register(cls(cwd=cwd, session_id=session_id, observed=store))
    for cls in SEARCH_TOOLS:
        registry.register(cls(cwd=cwd))
    for cls in COMMAND_TOOLS:
        registry.register(cls(cwd=cwd))
    registry.register(WebSearchTool())
    if kb is not None and getattr(kb, "is_enabled", True):
        registry.register(KbSearchTool(kb=kb, agent_id=agent_id))
        registry.register(KbSaveTool(kb=kb, agent_id=agent_id))
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
    "PwshTool",
    "ReadTool",
    "ToolRegistry",
    "WebSearchTool",
    "WriteTool",
    "build_default_registry",
]
