"""评审驱动：把一份简报交给评审子智能体跑一圈，回收并校验其围栏 JSON 判决。

判决必须机读（落点对照表、矩阵组装都吃它），所以对输出形状零容忍：解析失败
**重试一次**——换 call_id 的新一轮驱动（drive_child 的「已完结 → 复用摘要」守卫按
call_id 认身份，同 id 重驱动会把上一轮的坏摘要原样递回）；子图自身失败（ToolException）
不重试，那是子面问题不是格式问题，直接冒泡由驱动收敛 halted。
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from aitester.case_design.schema import parse_json_fence

ModelT = TypeVar("ModelT", bound=BaseModel)

MAX_ATTEMPTS = 2

_RETRY_HINT = (
    "\n\n【重试】上一轮输出无法按约定解析（需要恰好一个 ```json 代码块、块外无其它内容）。"
    "本轮只输出恰好一个 ```json 代码块；字段名与枚举值必须与简报给定的形状逐字一致，"
    "不得自造字段或自造枚举值。"
)


class ReviewerError(RuntimeError):
    """评审子两次输出都无法按围栏解析（驱动层据此落 halted）。"""


def run_reviewer(task_tool: Any, agent_id: str, brief: str, *, model_cls: type[ModelT],
                 call_id: str, title: str, config: Any = None,
                 name: str = "", archive: Any = None) -> tuple[ModelT, str]:
    """驱动评审子并解析判决；返回（判决模型, 原始文本）。

    `task_tool` 吃 TaskTool 的注入面（build_child/drive/parallel/roster）；`config` 直接透传
    父 run 的 RunnableConfig——评审子要落父 run 的 thread、经父流发帧。name 未给时优先回落
    roster 里的中文名（与 task 工具的 sub 卡片同一显示源），再退 agent_id。isolated 与
    `TaskTool._run` 同一表达式（读 parallel 表的同一份源，R14）：只读面 → 派生 ns（多个
    评审子在父任务里串行驱动、按 call_id 各自成家，互不串守卫）；用户把面配宽（含可挂起
    工具）→ 自动退回父 ns 的挂起续跑通路。

    `archive`（B-F7）：双败时以 (call_id, 逐次原文+解析错误) 落盘证据链的回调——
    面向人的 ReviewerError 消息只留一行中文摘要，pydantic 原文嵌进消息等于让人在
    唯一人审门外读一大段英文 ValidationError。
    """
    last_error: Exception | None = None
    attempts: list[str] = []
    meta = (getattr(task_tool, "roster", {}) or {}).get(agent_id) or {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        child = task_tool.build_child(agent_id)
        text = task_tool.drive(
            child,
            brief if attempt == 1 else brief + _RETRY_HINT,
            call_id=call_id if attempt == 1 else f"{call_id}-r2",
            name=name or str(meta.get("name") or agent_id),
            title=title,
            config=config,
            isolated=bool(getattr(task_tool, "parallel", {}).get(agent_id, False)),
        )
        try:
            return model_cls.model_validate(parse_json_fence(text)), text
        except Exception as exc:                  # 只重试「解析不了」，不重试「跑不成」
            last_error = exc
            attempts.append(f"第 {attempt} 次输出（{len(text or '')} 字符）：\n{text}\n"
                            f"解析错误：{exc!r}")
    if archive is not None:
        archive(call_id, "\n\n".join(attempts) + f"\n\n最终解析错误：{last_error!r}")
    raise ReviewerError(f"评审子 {agent_id} 两次输出都无法按围栏 JSON 约定解析（原文已归档）")
