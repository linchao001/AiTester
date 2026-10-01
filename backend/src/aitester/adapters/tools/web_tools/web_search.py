"""web_search 工具：按关键词搜索公网，委托可插拔 provider（默认 Tavily keyless）。

对齐 QwenPaw ``agents/tools/web_search.py``：空词拒绝、max_results=5、
``[n] 标题 / URL / 摘要`` 文本格式、失败时附 curl 兜底提示。
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from aitester.adapters.tools.base import AiTooler
from aitester.adapters.tools.web_tools.errors import (
    raise_blank_search_term,
    raise_search_failed,
)
from aitester.adapters.tools.web_tools.websearch import (
    format_search_results,
    get_search_provider,
)

MAX_RESULTS = 5


class WebSearchInput(BaseModel):
    search_term: str = Field(
        description=(
            "The search term to look up on the web. Be specific and include relevant "
            "keywords for better results. For technical queries, include version numbers "
            "or dates if relevant."
        )
    )


class WebSearchTool(AiTooler):
    name: str = "web_search"
    description: str = (
        "Search the web for real-time information about any topic. Returns summarized "
        "information from search results and relevant URLs.\n\n"
        "Use this tool when you need up-to-date information that might not be available "
        "or correct in your training data, or when you need to verify current facts. "
        "This includes queries about:\n"
        "- Libraries, frameworks, and tools whose APIs, best practices, or usage "
        "instructions are frequently updated.\n"
        "- Current events or technology news.\n"
        "- Informational queries similar to what you might search on the web.\n\n"
        "FALLBACK - This tool uses a free API with rate limits. If it returns an error "
        "due to network issues or quota limits, fall back to the pwsh or bash tool with curl."
    )
    args_schema: type[BaseModel] = WebSearchInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def _run(self, search_term: str) -> tuple[str, dict[str, Any]]:
        query = (search_term or "").strip()
        if not query:
            raise_blank_search_term()

        started = time.monotonic()
        try:
            provider = get_search_provider()
            results = provider.search(query, max_results=MAX_RESULTS)
        except Exception as exc:
            raise_search_failed(exc)

        text = format_search_results(results)
        if not text:
            text = "No content searched."
        artifact = {
            "query": query,
            "provider": provider.name,
            "results": results,
            "durationMs": int((time.monotonic() - started) * 1000),
        }
        return text, artifact
