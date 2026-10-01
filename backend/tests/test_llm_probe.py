import httpx
import pytest
from openai import APIConnectionError, APITimeoutError

from aitester.adapters.llm import probe
from aitester.adapters.llm.probe import ProbeError, probe_model

_REQ = httpx.Request("POST", "https://api.deepseek.com/chat/completions")


def _status_error(status_code: int) -> Exception:
    from openai import APIStatusError

    return APIStatusError(
        "upstream", response=httpx.Response(status_code, request=_REQ), body=None
    )


def _patch_chat(monkeypatch, *, error: Exception | None = None) -> tuple[dict, list]:
    recorded: dict[str, object] = {}
    calls: list[list] = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

        def invoke(self, messages: list) -> object:
            calls.append(messages)
            if error is not None:
                raise error
            return type("Resp", (), {"content": "pong"})()

    monkeypatch.setattr(probe, "ChatOpenAI", FakeChatOpenAI)
    return recorded, calls


def test_probe_sends_single_token_completion(monkeypatch) -> None:
    recorded, calls = _patch_chat(monkeypatch)
    latency = probe_model(
        model="deepseek-flash", api_key="sk-x123456789", base_url="https://api.deepseek.com"
    )
    assert isinstance(latency, int) and latency >= 0
    assert recorded == {
        "model": "deepseek-flash",
        "api_key": "sk-x123456789",
        "base_url": "https://api.deepseek.com",
        "timeout": 10,
        "max_retries": 0,
        "max_tokens": 1,
    }
    assert calls == [[{"role": "user", "content": "ping"}]]


def test_probe_honours_custom_timeout(monkeypatch) -> None:
    recorded, _ = _patch_chat(monkeypatch)
    probe_model(model="m", api_key="k", base_url="https://x", timeout=3)
    assert recorded["timeout"] == 3


@pytest.mark.parametrize(
    "error, expected",
    [
        (APITimeoutError(request=_REQ), "连接超时，请检查网络或 Base URL"),
        (APIConnectionError(request=_REQ), "地址不可达，请检查 Base URL 与网络"),
        (_status_error(401), "API Key 无效或无权限"),
        (_status_error(403), "API Key 无效或无权限"),
        (_status_error(404), "Base URL 路径不对（模型或接口不存在）"),
        (_status_error(429), "请求被限流，请稍后重试"),
        (_status_error(503), "上游返回 HTTP 503"),
        (ValueError("boom"), "调用失败：ValueError"),
    ],
)
def test_probe_maps_failure_to_actionable_copy(monkeypatch, error, expected) -> None:
    _patch_chat(monkeypatch, error=error)
    with pytest.raises(ProbeError) as exc_info:
        probe_model(model="m", api_key="sk-secret-123456", base_url="https://x")
    assert exc_info.value.detail == expected
    assert "sk-secret-123456" not in str(exc_info.value)
