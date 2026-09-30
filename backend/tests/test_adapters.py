import pytest

from aitester.adapters.llm import (
    LlmProvider,
    MockProvider,
    OpenAICompatProvider,
    ProviderError,
)
from aitester.adapters.llm import openai_compat


def test_mock_provider_echoes_last_user_message() -> None:
    provider: LlmProvider = MockProvider()
    messages = [
        {"role": "system", "content": "你是测试智能体"},
        {"role": "user", "content": "生成用例"},
        {"role": "assistant", "content": "上一轮回复"},
        {"role": "user", "content": "再来一条"},
    ]
    assert provider.name == "mock"
    assert provider.complete(messages) == "[mock] 再来一条"


def test_mock_provider_without_user_message() -> None:
    assert MockProvider().complete([{"role": "system", "content": "hi"}]) == "[mock]"


def test_mock_provider_model_ref() -> None:
    assert MockProvider().model_ref == "mock/mock"


def test_openai_compat_complete_passes_messages_and_returns_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            self.received: list[dict[str, str]] | None = None

        def invoke(self, messages: list[dict[str, str]]) -> object:
            self.received = messages
            return type("R", (), {"content": "真实回复"})()

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    provider = OpenAICompatProvider("deepseek", "sk-x", "https://api.deepseek.com", "deepseek-flash")
    messages = [{"role": "user", "content": "hi"}]
    assert provider.complete(messages) == "真实回复"
    assert provider._client.received == messages  # type: ignore[union-attr]


def test_openai_compat_wraps_upstream_error_and_redacts_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            pass

        def invoke(self, messages: list[dict[str, str]]) -> object:
            raise RuntimeError(f"invalid api_key sk-SECRET123 in request to {messages[0]['content']}")

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    provider = OpenAICompatProvider("dashscope", "sk-SECRET123", "https://example.com/v1", "qwen3.7-max")
    with pytest.raises(ProviderError) as exc_info:
        provider.complete([{"role": "user", "content": "hi"}])
    detail = exc_info.value.detail
    assert "dashscope/qwen3.7-max" in detail
    assert "sk-SECRET123" not in detail
