"""可插拔网页搜索后端的公共层：HTTP 传输、结果格式化与 provider 抽象。

对齐 QwenPaw ``agents/tools/websearch/base.py``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

_TIMEOUT = 30


def _post(url: str, headers: dict, payload: dict) -> dict:
    """JSON POST；证书校验始终开启。"""
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()


def format_search_results(results: list[dict]) -> str:
    """把结果列表渲染为 ``[n] 标题 / URL / 摘要`` 文本（对齐 QwenPaw 原文）。"""
    if not results:
        return "No results found."
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "")
        lines.append(f"[{i}] {title}")
        lines.append(f"    URL: {url}")
        if content:
            lines.append(f"    {content}")
        lines.append("")
    return "\n".join(lines).rstrip()


class SearchProvider(ABC):
    """网页搜索后端抽象。"""

    name = ""

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[dict]:
        """返回 ``{title, url, snippet, content}`` 形态的结果列表。"""
        raise NotImplementedError
