import pytest

from aitester.adapters.llm import MockProvider, ProviderConfigError
from aitester.adapters.llm import openai_compat
from aitester.config import Settings
from aitester.services import ChatService
from aitester.services.model_config import ModelConfigService
from aitester.storage import FileJsonConfigRepository


def _model_config(tmp_path, **settings_kwargs: object) -> ModelConfigService:
    return ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )


def test_echo_full_chain_trace_and_reply() -> None:
    svc = ChatService()
    result = svc.echo("s1", "生成登录用例")
    assert result["reply"] == "[mock] 生成登录用例"
    assert result["trace"] == [
        "services",
        "context",
        "orchestration",
        "adapters",
        "memory",
        "storage",
    ]


def test_echo_remembers_previous_turn() -> None:
    svc = ChatService()
    svc.echo("s1", "第一句")
    svc.echo("s1", "第二句")
    history = svc.memory.recall("s1")
    assert [m["content"] for m in history] == ["第一句", "[mock] 第一句", "第二句", "[mock] 第二句"]
    assert svc.repo.get("session:s1") == {"session_id": "s1", "last_reply": "[mock] 第二句"}


def test_send_uses_injected_provider_and_reports_model() -> None:
    svc = ChatService(provider=MockProvider())
    result = svc.send("s1", "生成用例")
    assert result["reply"] == "[mock] 生成用例"
    assert result["model"] == "mock/mock"
    assert result["trace"] == [
        "services",
        "context",
        "orchestration",
        "adapters",
        "memory",
        "storage",
    ]


def test_send_resolves_default_from_model_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

        def invoke(self, messages: list[dict[str, str]]) -> object:
            return type("R", (), {"content": "真实回复"})()

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    svc = ChatService(model_config=_model_config(tmp_path, deepseek_api_key="sk-x123456789"))
    result = svc.send("s1", "hi")
    assert result["reply"] == "真实回复"
    assert result["model"] == "deepseek/deepseek-flash"
    assert recorded["model"] == "deepseek-flash"


def test_send_without_usable_default_raises_actionable_config_error(tmp_path) -> None:
    svc = ChatService(model_config=_model_config(tmp_path))
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.send("s1", "hi")
    assert "设置 · 模型设置" in exc_info.value.detail
