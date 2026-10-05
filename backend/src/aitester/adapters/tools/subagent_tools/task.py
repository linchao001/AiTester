"""task 工具：委派式（agents-as-tools）子智能体的唯一入口。

文案边界（R11）：description / 入参说明 / 错误文本全是模型可见面，一律英文；UI 里的
中文说明来自 capability_config 的目录条目（R12），与本文件无关。
编排逻辑（子图驱动、帧重发、挂起重建）不在这里——`drive` 由装配层注入（R13），
本模块对 orchestration 零 import，断掉 adapters ↔ orchestration 的环。
"""

from __future__ import annotations

from typing import Annotated, Any, Callable

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, ToolException
from pydantic import BaseModel, Field

from aitester.adapters.tools.base import AiTooler

TASK_TOOL_ID = "task"
DEFAULT_SUBAGENT_TYPE = "general-purpose"   # 入参缺省值：gate 塑形也按它查 parallel 表（R14）

TASK_TOOL_DESC = (
    "Delegate a self-contained, read-only investigation to a subagent and get one "
    "summary back. Use it when the work would take many steps (searching, reading "
    "files, web search) and only the conclusion matters — the subagent's intermediate "
    "steps never enter this conversation, so your own context stays small. The "
    "subagent cannot see this conversation: put every detail it needs (goal, paths, "
    "relevant names) into `description`, and state exactly what to return. "
    "Several independent investigations may run at once: issue one `task` call per "
    "question in the same round and they run in parallel, as long as every subagent "
    "you name has a read-only tool face. Any other tool you ask for in such a round "
    "is skipped — re-issue it next round once the summaries are back."
)


class TaskInput(BaseModel):
    """入参形状（模型可见，说明英文）。"""

    description: str = Field(
        description="Self-contained brief for the subagent: the goal, every piece of "
                    "context it cannot discover itself, and the exact result to return.")
    subagent_type: str = Field(
        default=DEFAULT_SUBAGENT_TYPE,
        description="Which subagent to run. Pick from the list in this tool's "
                    "description; defaults to general-purpose.")
    title: str = Field(
        default="",
        description="Short Chinese label shown in the UI while the subagent runs "
                    "(e.g. '调查失败用例'). Optional; defaults to the subagent's name.")


def render_description(roster: dict[str, dict[str, str]]) -> str:
    """工具 description = 固定文案 + 在册清单（模型只能派清单里的子智能体）。"""
    lines = [f"- {meta['name']} ({agent_id}): {meta['desc']}"
             for agent_id, meta in roster.items()]
    return TASK_TOOL_DESC + "\n\nAvailable subagents:\n" + "\n".join(lines)


class TaskTool(AiTooler):
    """委派一件调查任务给子智能体独立完成，只回收一份摘要字符串。

    四个缝全部由装配层注入（R13 断环）：
    - ``roster``：``{agent_id: {"name": …, "desc": …}}``——模型可见清单与未知类型报错依据；
    - ``build_child``：``(agent_id) -> ChildRuntime``；未知 id 或装配失败直接抛异常；
    - ``drive``：``(child, brief, *, call_id, name, title, config, isolated) -> str`` 编排层驱动函数；
    - ``parallel``：``{agent_id: 能不能并行派发}``——同一份表既给 gate 塑形用，也决定本次
      驱动是否走派生 ns（R14），所以判定与执行不可能分家。
    """

    name: str = TASK_TOOL_ID
    description: str = TASK_TOOL_DESC
    args_schema: type[BaseModel] = TaskInput
    roster: dict[str, dict[str, str]] = {}
    build_child: Any = None
    drive: Any = None
    parallel: dict[str, bool] = {}

    def _run(self, description: str, subagent_type: str = DEFAULT_SUBAGENT_TYPE,
             title: str = "", tool_call_id: Annotated[str, InjectedToolCallId] = "",
             config: RunnableConfig = None) -> str:      # 裸标注才注入：写成 | None 时 langchain 不传 config（T1 实测）
        meta = self.roster.get(subagent_type)
        if meta is None:
            known = ", ".join(sorted(self.roster)) or "(none)"
            raise ToolException(
                f"Unknown subagent_type '{subagent_type}'. Available subagents: {known}.")
        try:
            child = self.build_child(subagent_type)
        except Exception as exc:                      # 装配失败也要以模型可见错误收场
            raise ToolException(
                f"Subagent '{subagent_type}' could not be started: {exc}") from exc
        return self.drive(child, description, call_id=str(tool_call_id),
                          name=meta["name"], title=title or meta["name"], config=config,
                          isolated=self.parallel.get(subagent_type, False))


def build_task_tool(roster: dict[str, dict[str, str]], build_child: Callable[[str], Any],
                    drive: Callable[..., str],
                    parallel: dict[str, bool] | None = None) -> TaskTool:
    """按在册清单产出一把 task 工具（description 里带清单一节；parallel 缺省全按串行）。"""
    return TaskTool(roster=dict(roster), build_child=build_child, drive=drive,
                    parallel=dict(parallel or {}), description=render_description(roster))
