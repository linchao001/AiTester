"""装配器单测：实例内容来自数据（提示词/模型/工具），实例本身不持有状态。"""

from pathlib import Path
from typing import Any

import pytest

from aitester.agents import find_agent
from aitester.adapters.llm import MockProvider, ProviderConfigError
from aitester.adapters.llm import openai_compat
from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools import FileObservationStore
from aitester.config import Settings
from aitester.orchestration import build_agent_graph
from aitester.services.agent_runtime import AgentInstance, AgentRuntime
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
from aitester.storage import FileJsonConfigRepository


def _runtime(
    tmp_path: Path, **settings_kwargs: object
) -> tuple[AgentRuntime, CapabilityConfigService, ModelConfigService]:
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    return AgentRuntime(capability, model_config), capability, model_config


class _FakeChat:
    """记录构造参数，避免真建 OpenAI 客户端。"""

    last: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last = dict(kwargs)


@pytest.fixture(autouse=True)
def _no_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_compat, "ChatOpenAI", _FakeChat)
    _FakeChat.last = {}


def test_unknown_agent_raises_config_not_found(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    with pytest.raises(ConfigNotFoundError) as exc_info:
        runtime.build("ghost", "s1", provider_override=MockProvider())
    assert exc_info.value.detail == "未知智能体「ghost」"


def test_legacy_id_is_not_resolved_by_runtime(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        runtime.build("a1", "s1", provider_override=MockProvider())


def test_prompt_and_builder_come_from_spec(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    instance = runtime.build("case_design", "s1", provider_override=MockProvider())
    spec = find_agent("case_design")
    assert isinstance(instance, AgentInstance)
    assert instance.agent_id == "case_design"
    assert instance.system_prompt == spec.prompt
    assert instance.build_graph is build_agent_graph


def test_agent_default_model_wins_over_global(tmp_path: Path) -> None:
    runtime, capability, _ = _runtime(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("case_design", "dashscope/qwen3.7-max")
    instance = runtime.build("case_design", "s1")
    assert instance.provider.model_ref == "dashscope/qwen3.7-max"
    assert _FakeChat.last["model"] == "qwen3.7-max"


def test_falls_back_to_global_default_when_agent_default_unusable(tmp_path: Path) -> None:
    runtime, capability, model_config = _runtime(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("case_design", "dashscope/qwen3.7-max")
    model_config.set_model_enabled("dashscope", "qwen3.7-max", False)
    instance = runtime.build("case_design", "s1")
    assert instance.provider.model_ref == "deepseek/deepseek-flash"


def test_nothing_configured_raises_actionable_400(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    with pytest.raises(ProviderConfigError) as exc_info:
        runtime.build("case_design", "s1")
    assert "设置 · 模型设置" in exc_info.value.detail


def test_provider_override_skips_model_resolution(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)  # 全局也没配 Key
    mock = MockProvider()
    instance = runtime.build("case_design", "s1", provider_override=mock)
    assert instance.provider is mock
    assert _FakeChat.last == {}


def test_tools_are_carried_exactly_as_enabled_state(tmp_path: Path) -> None:
    runtime, capability, _ = _runtime(tmp_path)
    capability.set_agent_tools("case_design", ["read", "web_search"])
    instance = runtime.build("case_design", "s1", provider_override=MockProvider())
    assert [t.tool_id() for t in instance.tools] == ["read", "web_search"]


def test_empty_carrier_list_builds_no_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, capability, _ = _runtime(tmp_path)
    capability.set_agent_tools("case_design", [])

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("无携带工具时不应构建工具注册表")

    monkeypatch.setattr("aitester.services.agent_runtime.build_default_registry", boom)
    instance = runtime.build("case_design", "s1", provider_override=MockProvider())
    assert instance.tools == []


def test_registry_uses_dot_cwd_and_shared_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class _Registry:
        def get_many(self, tool_ids: list[str]) -> list[Any]:
            return []

    def fake_registry(cwd: str = ".", session_id: str = "default", observed: Any = None) -> Any:
        captured.update({"cwd": cwd, "session_id": session_id, "observed": observed})
        return _Registry()

    monkeypatch.setattr("aitester.services.agent_runtime.build_default_registry", fake_registry)
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"), Settings(_env_file=None)
    )
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    store = FileObservationStore()
    runtime = AgentRuntime(capability, model_config, store)
    runtime.build("case_design", "s9", provider_override=MockProvider())
    assert captured["cwd"] == "."  # 项目目录接入是留给项目专项的缝
    assert captured["session_id"] == "s9"
    assert captured["observed"] is store


def test_instances_are_independent_objects(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    mock = MockProvider()  # 同一个注入对象传两次，才能验证「实例各自新建」而非「provider 相同」
    a = runtime.build("case_design", "s1", provider_override=mock)
    b = runtime.build("case_design", "s1", provider_override=mock)
    assert a is not b
    assert a.provider is b.provider  # 注入的是同一个 mock，但实例本身各自新建
    assert a.tools and len(a.tools) == len(b.tools)
    assert [t.tool_id() for t in a.tools] == [t.tool_id() for t in b.tools]
    assert all(x is not y for x, y in zip(a.tools, b.tools))  # 工具对象也每次新建，不跨请求复用
