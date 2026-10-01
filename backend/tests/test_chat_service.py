import pytest
from langchain_core.messages import AIMessage

from aitester.agents import find_agent
from aitester.adapters.llm import MockProvider, ProviderConfigError
from aitester.adapters.llm import openai_compat
from aitester.config import Settings
from aitester.services import ChatService
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ModelConfigService
from aitester.storage import FileJsonConfigRepository


def _model_config(tmp_path, **settings_kwargs: object) -> ModelConfigService:
    return ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )


def _runtime(tmp_path, **settings_kwargs: object) -> AgentRuntime:
    model_config = _model_config(tmp_path, **settings_kwargs)
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    return AgentRuntime(capability, model_config)


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


def test_send_uses_injected_provider_and_reports_model(tmp_path) -> None:
    svc = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path))
    result = svc.send("s1", "生成用例", "case_design")
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


def test_send_scopes_history_by_agent_and_session(tmp_path) -> None:
    svc = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path))
    svc.send("s1", "生成用例", "case_design")
    assert svc.memory.recall("case_design:s1")
    assert svc.memory.recall("s1") == []  # 与 echo 的裸键互不串
    assert svc.repo.get("session:case_design:s1") == {
        "session_id": "case_design:s1",
        "last_reply": "[mock] 生成用例",
    }
    svc.echo("s1", "echo 一句")
    assert [m["content"] for m in svc.memory.recall("s1")] == ["echo 一句", "[mock] echo 一句"]


def test_send_requires_agent_runtime() -> None:
    svc = ChatService(provider=MockProvider())
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.send("s1", "hi", "case_design")
    assert "服务未装配智能体运行时，请通过 create_app 启动后端" in exc_info.value.detail


def test_send_uses_agent_system_prompt_from_md(tmp_path) -> None:
    seen: list[list[object]] = []

    class _SpyProvider:
        """独立假 provider，bind_tools 返回自身——MockProvider.bind_tools 会返回新的
        MockProvider()，用它做子类会把 spy 丢掉，断言永远抓不到消息。"""

        name = "spy"
        model_ref = "spy/model"

        def complete(self, messages: list[dict[str, str]]) -> str:
            return "[spy]"

        def bind_tools(self, tools: list) -> "_SpyProvider":
            return self

        def invoke_messages(self, messages: list) -> AIMessage:
            seen.append(list(messages))
            return AIMessage(content="[spy] 收到")

    svc = ChatService(provider=_SpyProvider(), agent_runtime=_runtime(tmp_path))
    result = svc.send("s1", "生成用例", "case_design")
    assert result["reply"] == "[spy] 收到"
    assert type(seen[0][0]).__name__ == "SystemMessage"
    assert str(seen[0][0].content) == find_agent("case_design").prompt


def test_send_resolves_default_from_model_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

        def bind_tools(self, tools: list) -> "FakeChatOpenAI":
            return self

        def invoke(self, messages: list[dict[str, str]]) -> object:
            return type("R", (), {"content": "真实回复"})()

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    svc = ChatService(
        agent_runtime=_runtime(tmp_path, deepseek_api_key="sk-x123456789")
    )
    result = svc.send("s1", "hi", "case_design")
    assert result["reply"] == "真实回复"
    assert result["model"] == "deepseek/deepseek-flash"
    assert recorded["model"] == "deepseek-flash"


def test_send_without_usable_default_raises_actionable_config_error(tmp_path) -> None:
    svc = ChatService(agent_runtime=_runtime(tmp_path))
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.send("s1", "hi", "case_design")
    assert "设置 · 模型设置" in exc_info.value.detail
