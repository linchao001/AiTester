import pytest

from aitester.adapters.llm import (
    LlmProvider,
    MockProvider,
    OpenAICompatProvider,
    ProviderConfigError,
    ProviderError,
    build_provider,
)
from aitester.adapters.llm import openai_compat
from aitester.config import Settings


def _settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


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


def test_factory_mock_returns_mock_provider() -> None:
    assert isinstance(build_provider(_settings(llm_provider="mock")), MockProvider)


def test_factory_deepseek_without_key_raises_with_env_var_name() -> None:
    with pytest.raises(ProviderConfigError) as exc_info:
        build_provider(_settings(llm_provider="deepseek", deepseek_api_key=""))
    assert "DEEPSEEK_API_KEY" in exc_info.value.detail


def test_factory_dashscope_without_key_raises_with_env_var_name() -> None:
    with pytest.raises(ProviderConfigError) as exc_info:
        build_provider(_settings(llm_provider="dashscope", dashscope_api_key="  "))
    assert "DASHSCOPE_API_KEY" in exc_info.value.detail


def test_factory_unknown_provider_lists_choices() -> None:
    with pytest.raises(ProviderConfigError) as exc_info:
        build_provider(_settings(llm_provider="glm"))
    detail = exc_info.value.detail
    assert "glm" in detail
    assert "mock" in detail and "deepseek" in detail and "dashscope" in detail


def test_factory_deepseek_with_key_builds_openai_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    provider = build_provider(
        _settings(llm_provider="DeepSeek", deepseek_api_key="sk-real", deepseek_model="deepseek-flash")
    )
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.model_ref == "deepseek/deepseek-flash"
    assert recorded == {
        "model": "deepseek-flash",
        "api_key": "sk-real",
        "base_url": "https://api.deepseek.com",
        "timeout": 60,
    }


def test_openai_compat_complete_passes_messages_and_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_openai_compat_wraps_upstream_error_and_redacts_key(monkeypatch: pytest.MonkeyPatch) -> None:
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

