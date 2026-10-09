"""单条工具产物的 token 闸——本片唯一会改变「送给模型的内容」的动作（裁定 CM-2）。

既有字节地板（命令输出 1MB×2、read 50KB、search 5 万字符）一概不动：它们拦的是
「别把磁盘读爆」，这里拦的是「别把上下文吃爆」。截断必须留痕且带三个数，
因为人只看得到终帧那一行，看不到就等于没说（R-C3）。
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.messages import BaseMessage

from aitester.context import meter
from aitester.context.usage import ContextUsage

TOOL_OUTPUT_TOKEN_CAP = 12_000          # 默认窗口 131072 的一成
CAP_ENV = "AITESTER_TOOL_OUTPUT_TOKEN_CAP"
HEAD_RATIO = 0.6
_SHRINK = 0.8
_SHRINK_ROUNDS = 12
_ELLIPSIS = "\n…\n"

TRUNCATION_MARK = (
    "[工具输出已截断：原约 {original} tokens，保留 {kept}，省略中段 {dropped}。"
    "以下内容由编排层截断，非工具自身报错。]"
)


def cap_for() -> int:
    raw = os.environ.get(CAP_ENV)
    if not raw:
        return TOOL_OUTPUT_TOKEN_CAP
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return TOOL_OUTPUT_TOKEN_CAP
    return value if value > 0 else TOOL_OUTPUT_TOKEN_CAP


def _assemble(text: str, head_n: int, tail_n: int) -> str:
    if tail_n <= 0:
        return text[:head_n].rstrip() + "\n…"
    return text[:head_n].rstrip() + _ELLIPSIS + text[-tail_n:].lstrip()


def _render_mark(original: int, kept: int, dropped: int) -> str:
    return TRUNCATION_MARK.format(original=original, kept=kept, dropped=dropped)


def _truncate(text: str, cap: int, original: int) -> tuple[str, int]:
    """收窄到「标记 + 正文」整段真的不超线，返回 (最终字符串, 正文 token 数)。

    预算算在**最终字符串**上（不是只算正文）：标记里那三个数每轮都在变，只有整段
    实测才可能守住 `estimate(out) <= cap`。cap 小于一行标记自己时标记优先、正文让到
    1+1 字符——宁可整段略超这个极端 cap，也不静默多塞内容。
    """
    scale = min(1.0, cap / max(original, 1))
    keep_chars = max(2, int(len(text) * scale))
    head_n = max(1, int(keep_chars * HEAD_RATIO))
    tail_n = max(1, keep_chars - head_n)
    body = _assemble(text, head_n, tail_n)
    kept = meter.estimate_text(body)
    out = _render_mark(original, kept, max(0, original - kept)) + "\n" + body
    for _ in range(_SHRINK_ROUNDS):
        if meter.estimate_text(out) <= cap or (head_n <= 1 and tail_n <= 1):
            return out, min(kept, original)
        head_n = max(1, int(head_n * _SHRINK))
        tail_n = max(1, int(tail_n * _SHRINK))
        body = _assemble(text, head_n, tail_n)
        kept = meter.estimate_text(body)
        out = _render_mark(original, kept, max(0, original - kept)) + "\n" + body
    return out, min(kept, original)


def cap_content(text: str, *, tool: str, usage: ContextUsage | None) -> str:
    if not text:
        return text
    cap = cap_for()
    try:
        original = meter.estimate_text(text)
        if original <= cap:
            return text
        out, kept = _truncate(text, cap, original)
        dropped = max(0, original - kept)
        if usage is not None:
            usage.note_truncation(tool=tool, original=original,
                                  kept=kept, dropped=dropped)
        return out
    except Exception as exc:                      # 闸坏了也不许拖垮回合：放行 + 记因
        if usage is not None:
            usage.error = f"cap 失败：{type(exc).__name__}"
        return text


def cap_result(result: Any, *, tool: str, usage: ContextUsage | None) -> Any:
    """只截 content。文件工具是 content_and_artifact，artifact 一个字节都不动；
    生产路径（ToolNode→invoke）拿到的是 ToolMessage，同样只换 content、其余原样。"""
    if isinstance(result, str):
        return cap_content(result, tool=tool, usage=usage)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], str):
        return (cap_content(result[0], tool=tool, usage=usage), result[1])
    if isinstance(result, BaseMessage) and isinstance(result.content, str):
        capped = cap_content(result.content, tool=tool, usage=usage)
        if capped is result.content:
            return result                    # 没砍就不新建对象：身份也是读数的一部分
        return result.model_copy(update={"content": capped})
    return result
