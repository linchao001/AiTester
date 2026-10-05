"""子智能体工具组：委派式的 task 工具（编排逻辑由调用方注入，见 task.py 文档）。"""

from aitester.adapters.tools.subagent_tools.task import (
    DEFAULT_SUBAGENT_TYPE,
    TASK_TOOL_DESC,
    TASK_TOOL_ID,
    TaskInput,
    TaskTool,
    build_task_tool,
    render_description,
)

__all__ = [
    "DEFAULT_SUBAGENT_TYPE",
    "TASK_TOOL_DESC",
    "TASK_TOOL_ID",
    "TaskInput",
    "TaskTool",
    "build_task_tool",
    "render_description",
]
