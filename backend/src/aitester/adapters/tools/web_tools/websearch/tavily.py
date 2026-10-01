"""Tavily keyless 搜索后端（对齐 QwenPaw ``websearch/tavily.py``）。"""

from __future__ import annotations

from aitester.adapters.tools.web_tools.websearch.base import SearchProvider, _post


class TavilyProvider(SearchProvider):
    """Tavily 免 Key 后端：请求头 ``X-Tavily-Access-Mode: keyless``。"""

    name = "tavily"

    _SEARCH_URL = "https://api.tavily.com/search"

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        payload = {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }
        data = _post(
            self._SEARCH_URL,
            headers={
                "Content-Type": "application/json",
                "X-Tavily-Access-Mode": "keyless",
            },
            payload=payload,
        )
        return list(data.get("results") or [])
