from pathlib import Path

import pytest

from aitester.config import Settings
from aitester.services.capability_config import (
    AGENT_CATALOG,
    TOOL_CATALOG,
    CapabilityConfigError,
    CapabilityConfigService,
)
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
from aitester.storage import FileJsonConfigRepository


def _svc(
    tmp_path: Path, **settings_kwargs: object
) -> tuple[CapabilityConfigService, ModelConfigService]:
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    return capability, model_config


def _stored(tmp_path: Path) -> dict[str, object]:
    loaded = FileJsonConfigRepository(tmp_path / "capability_config.json").load()
    assert loaded is not None
    return loaded


def test_first_start_seeds_and_persists(tmp_path: Path) -> None:
    _svc(tmp_path)
    stored = _stored(tmp_path)
    assert stored["version"] == 1
    assert stored["tool_state"] == {
        "read": True,
        "write": True,
        "edit": True,
        "pwsh": True,
        "bash": False,
        "web_search": True,
    }
    assert stored["agents"] == {
        "a1": {"default_uid": "", "tool_ids": ["read", "write", "edit", "web_search"]}
    }


def test_existing_file_is_not_reseeded(tmp_path: Path) -> None:
    saved = {
        "version": 1,
        "tool_state": {
            "read": False,
            "write": True,
            "edit": True,
            "pwsh": True,
            "bash": True,
            "web_search": False,
        },
        "agents": {
            "a1": {"default_uid": "deepseek/deepseek-flash", "tool_ids": ["write"]}
        },
    }
    FileJsonConfigRepository(tmp_path / "capability_config.json").save(saved)
    capability, _ = _svc(tmp_path)
    assert _stored(tmp_path) == saved
    agent = capability.get_view()["agents"][0]
    assert agent["default_uid"] == "deepseek/deepseek-flash"
    assert agent["tool_ids"] == ["write"]


def test_catalog_seeds_exactly_one_agent_and_six_tools() -> None:
    assert [a["id"] for a in AGENT_CATALOG] == ["a1"]
    assert AGENT_CATALOG[0]["name"] == "用例设计智能体"
    assert [t["id"] for t in TOOL_CATALOG] == [
        "read",
        "write",
        "edit",
        "pwsh",
        "bash",
        "web_search",
    ]


def test_get_view_tools_shape_and_carried_by(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    view = capability.get_view()
    assert [t["id"] for t in view["tools"]] == [t["id"] for t in TOOL_CATALOG]
    read = view["tools"][0]
    assert read["group"] == "文件处理工具"
    assert read["enabled"] is True
    assert read["carried_by"] == ["a1"]
    bash = next(t for t in view["tools"] if t["id"] == "bash")
    assert bash["enabled"] is False
    assert bash["os"] == "macOS"
    assert bash["carried_by"] == []


def test_get_view_agent_carries_readonly_prompt(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    agent = capability.get_view()["agents"][0]
    assert agent["id"] == "a1"
    assert agent["icon"] == "📋"
    assert "## 职责" in agent["prompt"]
    assert agent["default_uid"] == ""
    assert agent["effective_uid"] == ""
    assert agent["tool_ids"] == ["read", "write", "edit", "web_search"]


def test_effective_uid_follows_global_default_when_unset(tmp_path: Path) -> None:
    capability, model_config = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert model_config.default_uid == "deepseek/deepseek-flash"
    assert capability.get_view()["agents"][0]["effective_uid"] == "deepseek/deepseek-flash"


def test_effective_uid_prefers_agent_default(tmp_path: Path) -> None:
    capability, model_config = _svc(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("a1", "dashscope/qwen3.7-max")
    agent = capability.get_view()["agents"][0]
    assert agent["default_uid"] == "dashscope/qwen3.7-max"
    assert agent["effective_uid"] == "dashscope/qwen3.7-max"


def test_effective_uid_falls_back_when_agent_default_becomes_unusable(tmp_path: Path) -> None:
    capability, model_config = _svc(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("a1", "dashscope/qwen3.7-max")
    model_config.set_model_enabled("dashscope", "qwen3.7-max", False)
    agent = capability.get_view()["agents"][0]
    # 配置不自愈：default_uid 原样保留，只在读取时回落全局默认
    assert agent["default_uid"] == "dashscope/qwen3.7-max"
    assert agent["effective_uid"] == "deepseek/deepseek-flash"


def test_effective_uid_empty_when_nothing_configured(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    assert capability.get_view()["agents"][0]["effective_uid"] == ""


def test_set_agent_default_model_empty_and_valid_persist(tmp_path: Path) -> None:
    capability, model_config = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    capability.set_agent_default_model("a1", "deepseek/deepseek-v4-pro")
    assert _stored(tmp_path)["agents"]["a1"]["default_uid"] == "deepseek/deepseek-v4-pro"
    capability.set_agent_default_model("a1", "")
    assert _stored(tmp_path)["agents"]["a1"]["default_uid"] == ""


def test_set_agent_default_model_rejects_unusable(tmp_path: Path) -> None:
    capability, model_config = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    model_config.set_model_enabled("deepseek", "deepseek-flash", False)
    with pytest.raises(CapabilityConfigError) as exc_info:
        capability.set_agent_default_model("a1", "deepseek/deepseek-flash")
    assert "设置 · 模型设置" in exc_info.value.detail
    with pytest.raises(CapabilityConfigError):
        capability.set_agent_default_model("a1", "dashscope/qwen3.7-max")  # 未配 Key
    with pytest.raises(CapabilityConfigError):
        capability.set_agent_default_model("a1", "deepseek/nope")  # 不存在
    assert _stored(tmp_path)["agents"]["a1"]["default_uid"] == ""


def test_set_agent_default_model_unknown_agent_raises_404_error(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        capability.set_agent_default_model("a9", "")


def test_set_agent_tools_keeps_order_and_dedups(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_agent_tools("a1", ["edit", "read", "read", "pwsh"])
    assert _stored(tmp_path)["agents"]["a1"]["tool_ids"] == ["edit", "read", "pwsh"]
    view = capability.get_view()
    assert view["agents"][0]["tool_ids"] == ["edit", "read", "pwsh"]
    assert next(t for t in view["tools"] if t["id"] == "pwsh")["carried_by"] == ["a1"]


def test_set_agent_tools_empty_list_is_allowed(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_agent_tools("a1", [])
    assert _stored(tmp_path)["agents"]["a1"]["tool_ids"] == []
    assert all(t["carried_by"] == [] for t in capability.get_view()["tools"])


def test_set_agent_tools_rejects_disabled_tool(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    with pytest.raises(CapabilityConfigError) as exc_info:
        capability.set_agent_tools("a1", ["read", "bash"])
    assert "bash" in exc_info.value.detail
    assert "工具" in exc_info.value.detail
    assert _stored(tmp_path)["agents"]["a1"]["tool_ids"] == [
        "read",
        "write",
        "edit",
        "web_search",
    ]


def test_set_agent_tools_unknown_ids_raise_404_error(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        capability.set_agent_tools("a1", ["nope"])
    with pytest.raises(ConfigNotFoundError):
        capability.set_agent_tools("a9", ["read"])


def test_disable_tool_strips_every_agent(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_tool_enabled("read", False)
    view = capability.get_view()
    read = next(t for t in view["tools"] if t["id"] == "read")
    assert read["enabled"] is False
    assert read["carried_by"] == []
    assert "read" not in view["agents"][0]["tool_ids"]
    stored = _stored(tmp_path)
    assert stored["tool_state"]["read"] is False
    assert stored["agents"]["a1"]["tool_ids"] == ["write", "edit", "web_search"]


def test_reenable_tool_does_not_restore_carriers(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_tool_enabled("read", False)
    capability.set_tool_enabled("read", True)
    stored = _stored(tmp_path)
    assert stored["tool_state"]["read"] is True
    assert "read" not in stored["agents"]["a1"]["tool_ids"]
    read = next(t for t in capability.get_view()["tools"] if t["id"] == "read")
    assert read["enabled"] is True and read["carried_by"] == []


def test_set_tool_enabled_unknown_tool_raises_404_error(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        capability.set_tool_enabled("nope", False)
    with pytest.raises(ConfigNotFoundError):
        capability.set_agent_tools("a1", ["nope"])
    assert capability.set_tool_enabled("bash", True) is None
