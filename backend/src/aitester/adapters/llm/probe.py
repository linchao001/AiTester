"""连接探测：发一次 max_tokens=1 的极小补全，只验证模型能否应答。"""
from __future__ import annotations

import time

from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError

PROBE_MESSAGE = "ping"
PROBE_TIMEOUT_SECONDS = 10


class ProbeError(Exception):
    """探测失败；detail 是面向用户的中文归因，不含 API Key。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _classify(exc: Exception) -> str:
    if isinstance(exc, APITimeoutError):
        return "连接超时，请检查网络或 Base URL"
    if isinstance(exc, APIConnectionError):
        return "地址不可达，请检查 Base URL 与网络"
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        if status in (401, 403):
            return "API Key 无效或无权限"
        if status == 404:
            return "Base URL 路径不对（模型或接口不存在）"
        if status == 429:
            return "请求被限流，请稍后重试"
        return f"上游返回 HTTP {status}"
    return f"调用失败：{type(exc).__name__}"


def probe_model(
    model: str,
    api_key: str,
    base_url: str,
    timeout: int = PROBE_TIMEOUT_SECONDS,
) -> int:
    """成功返回本次调用耗时（毫秒），失败抛 ProbeError（归因文案可照做）。"""
    client = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
        max_tokens=1,
    )
    started = time.perf_counter()
    try:
        client.invoke([{"role": "user", "content": PROBE_MESSAGE}])
    except Exception as exc:
        raise ProbeError(_classify(exc)) from exc
    return round((time.perf_counter() - started) * 1000)
