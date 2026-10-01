"""provider 选择：进程环境变量 ``AITESTER_WEB_SEARCH_PROVIDER``，缺省 tavily。

对齐 QwenPaw factory 的语义：无配置回落 tavily，未知值直接报错而不是静默降级
（QwenPaw 从每智能体的 Console 工具配置读取，本工程暂以环境变量承载同一开关）。
"""

from __future__ import annotations

import os

from aitester.adapters.tools.web_tools.websearch.anysearch import AnySearchProvider
from aitester.adapters.tools.web_tools.websearch.base import SearchProvider
from aitester.adapters.tools.web_tools.websearch.tavily import TavilyProvider

PROVIDER_ENV = "AITESTER_WEB_SEARCH_PROVIDER"


def get_search_provider() -> SearchProvider:
    """返回当前配置的搜索后端。"""
    choice = (os.environ.get(PROVIDER_ENV) or "").strip().lower()
    if choice in {"", "tavily"}:
        return TavilyProvider()
    if choice == "anysearch":
        return AnySearchProvider()
    raise ValueError(
        f"Unknown web_search provider: {choice!r} (expected 'tavily' or 'anysearch')"
    )
