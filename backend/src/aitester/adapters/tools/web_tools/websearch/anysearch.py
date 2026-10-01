"""AnySearch REST 搜索后端（https://api.anysearch.com），对齐 QwenPaw 同名实现。

与 QwenPaw 的差异：本工程暂无凭据存储，402 自动注册拿到的 Key 只缓存在进程内存里
（QwenPaw 持久化到工作区 credentials.yaml）；匿名免费额度耗尽后的重试语义保持一致。
"""

from __future__ import annotations

import logging
import re
import time

import httpx

from aitester.adapters.tools.web_tools.websearch.base import SearchProvider, _post

logger = logging.getLogger(__name__)

# 进程内缓存的 AnySearch Key（匿名调用触发 402 自动注册后写回）。
_api_key = ""


def current_anysearch_key() -> str:
    """当前进程缓存的 AnySearch Key；没有则返回空串（走匿名免费额度）。"""
    return _api_key


def _remember_anysearch_key(key: str) -> None:
    global _api_key
    _api_key = key


_CRED_LINE_RE = re.compile(
    r"^(username|password|api_key)=(.+?)\.?$",
    re.MULTILINE,
)


def _parse_auto_registered_credentials(message: str) -> dict[str, str]:
    """解析 402 自动注册消息体里的凭据行（``api_key`` 行末尾句点需剥掉）。"""
    return {m.group(1): m.group(2) for m in _CRED_LINE_RE.finditer(message)}


class AnySearchProvider(SearchProvider):
    """AnySearch 后端：匿名免费额度；402 时区分「自动注册」与「额度耗尽」两条分支。"""

    name = "anysearch"

    _SEARCH_URL = "https://api.anysearch.com/v1/search"

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        headers = {"Content-Type": "application/json"}
        api_key = current_anysearch_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {"query": query, "max_results": max_results}
        try:
            data = _post(self._SEARCH_URL, headers=headers, payload=payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 402:
                data = self._handle_quota_exceeded(exc.response, headers, payload)
            else:
                raise
        return list((data.get("data") or {}).get("results") or [])

    def _handle_quota_exceeded(
        self, response: httpx.Response, headers: dict, payload: dict
    ) -> dict:
        body = response.json()
        message = str(body.get("message") or "")

        if "automatically generated" in message:
            creds = _parse_auto_registered_credentials(message)
            new_key = creds.get("api_key", "")
            if not new_key:
                raise ValueError(f"AnySearch quota response missing api_key: {message!r}")
            _remember_anysearch_key(new_key)
            logger.info("AnySearch auto-registered an API key (in-memory for this process)")
            retry_headers = dict(headers)
            retry_headers["Authorization"] = f"Bearer {new_key}"
            return _post(self._SEARCH_URL, headers=retry_headers, payload=payload)

        if "anonymous free quota" in message:
            time.sleep(1)
            try:
                return _post(self._SEARCH_URL, headers=headers, payload=payload)
            except httpx.HTTPStatusError as retry_exc:
                retry_message = str(retry_exc.response.json().get("message") or "")
                raise ValueError(
                    f"AnySearch quota error ({retry_exc.response.status_code}): {retry_message}"
                ) from retry_exc

        raise ValueError(f"AnySearch quota error ({response.status_code}): {message}")
