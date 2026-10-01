"""模型侧稳定诊断：网页搜索工具的入参校验与失败文案。

模型可见文案一律英文（对齐 QwenPaw 与业界主流工具定义），面向用户的 UI 文案不在此列。
"""

from typing import NoReturn

from langchain_core.tools import ToolException

# 对齐 QwenPaw ``agents/tools/web_search.py`` 的降级指引；shell 工具名换成本工程的 pwsh / bash。
_SEARCH_FALLBACK_HINT = (
    "This tool uses a free API with rate limits. "
    "Try again later, or fall back to the pwsh or bash tool with curl as a last resort."
)


def raise_blank_search_term() -> NoReturn:
    raise ToolException("search_term must be a non-empty string")


def raise_search_failed(reason: object) -> NoReturn:
    raise ToolException(f"web_search failed: {reason}\n\n{_SEARCH_FALLBACK_HINT}")
