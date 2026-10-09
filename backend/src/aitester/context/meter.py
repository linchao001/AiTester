"""上下文计量：全仓唯一的一把尺。

估算走 tiktoken 词表（**只有预热成功后才用**，冷取词表实测要 141 秒，绝不能出现在
请求路径上）；词表不可用时退字符兜底并记因——兜底口径刻意偏多算，尺宁可紧不可松。
真值只从 provider 回传里取，取不到就返
None——「没有真值」是一种必须被如实呈递的状态，不是 0。
"""

from __future__ import annotations

import json
import math
import os
import threading
from typing import Any

SAFETY_MARGIN = 1.15
ENCODING_NAME = "o200k_base"
FALLBACK_CHARS_PER_TOKEN = 3
FALLBACK_TOKENS_PER_NONASCII_CHAR = 0.8   # 实测纯中文 o200k ≈ 0.71 token/字，取 0.8 留余量
WARM_ENV = "AITESTER_TOKEN_WARM"          # 置 "0" 关闭预热（受限网络/离线环境）

_lock = threading.Lock()
_enc: Any = None
_ready = threading.Event()
_error: str | None = None


def reset_for_tests() -> None:
    global _enc, _error
    with _lock:
        _enc = None
        _error = None
    _ready.clear()


def _set_error(msg: str) -> None:
    global _error
    with _lock:
        _error = msg[:200]


def warm() -> bool:
    """取词表并置就绪位：单飞、幂等、失败只记因。只在启动期的后台线程里调。"""
    global _enc, _error                  # Python 要求 global 先于任何使用——必须提到函数首行
    if _ready.is_set():
        return True
    with _lock:
        if _ready.is_set():
            return True
        if os.environ.get(WARM_ENV) == "0":
            _error = f"{WARM_ENV}=0，已按配置关闭词表预热"
            return False
        try:
            import tiktoken
            encoding = tiktoken.get_encoding(ENCODING_NAME)
        except Exception as exc:
            _error = f"词表不可用：{type(exc).__name__}"
            return False
        _enc = encoding
        _ready.set()
    return True


def is_warm() -> bool:
    return _ready.is_set()


def warm_error() -> str | None:
    return _error


def _pad(raw_tokens: float) -> int:
    return max(1, math.ceil(raw_tokens * SAFETY_MARGIN))


def _fallback_tokens(text: str) -> float:
    """兜底口径：ASCII 字符按 1/3 token，非 ASCII（中日韩等）按 0.8 token。

    写成加权式而不是 len/3，是因为实测「中文一律按 1/3 token」会少算近 2×——
    尺少算 = 闸门自以为砍够了却没砍够，方向必须是多算。
    """
    nonascii = sum(1 for ch in text if ord(ch) > 127)
    return (len(text) - nonascii) / FALLBACK_CHARS_PER_TOKEN + nonascii * FALLBACK_TOKENS_PER_NONASCII_CHAR


def estimate_text(text: str) -> int:
    if _ready.is_set():
        try:
            return _pad(len(_enc.encode(text, disallowed_special=())))
        except Exception as exc:
            _set_error(f"编码失败，退字符兜底：{type(exc).__name__}")
    return _pad(_fallback_tokens(text))


def _message_blob(message: Any) -> str:
    if isinstance(message, dict):
        parts = [str(message.get("role", "")), str(message.get("content", ""))]
        calls = list(message.get("tool_calls") or [])
        name = message.get("name")
    else:
        parts = [str(getattr(message, "type", "") or type(message).__name__),
                 str(getattr(message, "content", "") or "")]
        calls = list(getattr(message, "tool_calls", None) or [])
        name = getattr(message, "name", None)
    if name:
        parts.append(str(name))
    for call in calls:
        if not isinstance(call, dict):
            continue
        parts.append(str(call.get("name", "")))
        try:
            parts.append(json.dumps(call.get("args", {}), ensure_ascii=False, default=str))
        except Exception:
            parts.append(str(call.get("args")))
    return "\n".join(p for p in parts if p)


def estimate_messages(messages: list[Any]) -> int:
    return sum(estimate_text(_message_blob(m)) for m in messages) if messages else 0


_IN_KEYS = ("input_tokens", "prompt_tokens")
_OUT_KEYS = ("output_tokens", "completion_tokens")


def _pick(source: Any, keys: tuple[str, ...]) -> int | None:
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = source.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue          # inf/nan 不是数：当成「没有真值」，别把 int() 的异常扔给回合
        return int(value)
    return None


def _tokens_of(response: Any, keys: tuple[str, ...]) -> int | None:
    real = _pick(getattr(response, "usage_metadata", None), keys)
    if real is not None:
        return real
    meta = getattr(response, "response_metadata", None)
    if isinstance(meta, dict):
        return _pick(meta.get("token_usage") or meta.get("usage"), keys)
    return None


def input_tokens_of(response: Any) -> int | None:
    return _tokens_of(response, _IN_KEYS)


def output_tokens_of(response: Any) -> int | None:
    return _tokens_of(response, _OUT_KEYS)
