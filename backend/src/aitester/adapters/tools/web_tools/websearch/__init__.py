"""可插拔网页搜索后端：默认 Tavily keyless，可用环境变量切到 AnySearch。"""

from aitester.adapters.tools.web_tools.websearch.anysearch import AnySearchProvider
from aitester.adapters.tools.web_tools.websearch.base import (
    SearchProvider,
    format_search_results,
)
from aitester.adapters.tools.web_tools.websearch.factory import get_search_provider
from aitester.adapters.tools.web_tools.websearch.tavily import TavilyProvider

__all__ = [
    "AnySearchProvider",
    "SearchProvider",
    "TavilyProvider",
    "format_search_results",
    "get_search_provider",
]
