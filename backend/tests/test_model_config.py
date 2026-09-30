from pathlib import Path

import pytest

from aitester.adapters.llm import ProviderConfigError
from aitester.adapters.llm import openai_compat
from aitester.config import Settings
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService, mask_key
from aitester.storage import FileJsonConfigRepository


def _svc(tmp_path: Path, **settings_kwargs: object) -> ModelConfigService:
    return ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )


def test_first_start_seeds_from_settings_and_persists(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="  sk-seed-abcdef123456  ")
    stored = FileJsonConfigRepository(tmp_path / "model_config.json").load()
    assert stored is not None
    assert stored["version"] == 1
    assert stored["providers"][0]["api_key"] == "sk-seed-abcdef123456"
    assert svc.default_uid == "deepseek/deepseek-flash"


def test_seed_prefers_dashscope_when_only_it_has_key(tmp_path: Path) -> None:
    svc = _svc(tmp_path, dashscope_api_key="sk-dash-000111")
    assert svc.default_uid == "dashscope/qwen3.7-max"


def test_seed_without_keys_leaves_default_empty(tmp_path: Path) -> None:
    assert _svc(tmp_path).default_uid == ""


def test_existing_file_is_not_reseeded(tmp_path: Path) -> None:
    saved = {
        "version": 1,
        "default_uid": "custom/x",
        "providers": [
            {
                "id": "custom",
                "name": "Custom",
                "base_url": "https://x",
                "api_key": "k",
                "models": [{"id": "x", "enabled": True, "max_output": 1, "context": 1}],
            }
        ],
    }
    repo = FileJsonConfigRepository(tmp_path / "model_config.json")
    repo.save(saved)
    svc = ModelConfigService(
        repo, Settings(_env_file=None, deepseek_api_key="sk-should-not-appear")
    )
    assert svc.default_uid == "custom/x"
    assert FileJsonConfigRepository(tmp_path / "model_config.json").load() == saved


def test_get_view_shape_and_masking(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-ABCDEFGHIJKLMNOP")
    view = svc.get_view()
    assert view["default_uid"] == "deepseek/deepseek-flash"
    provider = view["providers"][0]
    assert provider["id"] == "deepseek"
    assert provider["has_key"] is True
    assert provider["key_masked"] == "sk-A…MNOP"
    assert provider["models"][0]["id"] == "deepseek-flash"
    assert "sk-ABCDEFGHIJKLMNOP" not in str(view)
    assert len(view["providers"]) == 2


def test_mask_key_rules() -> None:
    assert mask_key("") == ""
    assert mask_key("12345678") == "***"
    assert mask_key("123456789") == "1234…6789"


def test_update_api_key_none_keeps_string_sets(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.update_api_key("deepseek", None)
    assert svc.get_view()["providers"][0]["has_key"] is False
    svc.update_api_key("deepseek", "  sk-x123456789  ")
    assert svc.get_view()["providers"][0]["has_key"] is True


def test_update_unknown_provider_raises_404_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigNotFoundError):
        _svc(tmp_path).update_api_key("glm", "k")


def test_set_default_validations(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.set_default("deepseek/deepseek-flash")
    assert "设置 · 模型设置" in exc_info.value.detail
    svc.update_api_key("deepseek", "sk-x123456789")
    svc.set_model_enabled("deepseek", "deepseek-flash", False)
    with pytest.raises(ProviderConfigError) as exc_info2:
        svc.set_default("deepseek/deepseek-flash")
    assert "启用" in exc_info2.value.detail
    with pytest.raises(ConfigNotFoundError):
        svc.set_default("deepseek/nope")
    with pytest.raises(ProviderConfigError):
        svc.set_default("garbage")


def test_cascade_on_disable_default_persisted(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert svc.default_uid == "deepseek/deepseek-flash"
    svc.set_model_enabled("deepseek", "deepseek-flash", False)
    assert svc.default_uid == "deepseek/deepseek-v4-pro"
    svc.set_model_enabled("deepseek", "deepseek-v4-pro", False)
    assert svc.default_uid == ""
    assert _svc(tmp_path).default_uid == ""


def test_cascade_on_clear_key_falls_back_to_next_provider(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789")
    svc.update_api_key("deepseek", "")
    assert svc.default_uid == "dashscope/qwen3.7-max"


def test_build_default_provider_passes_expected_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    provider = svc.build_default_provider()
    assert provider.model_ref == "deepseek/deepseek-flash"
    assert recorded == {
        "model": "deepseek-flash",
        "api_key": "sk-x123456789",
        "base_url": "https://api.deepseek.com",
        "timeout": 60,
    }


def test_build_default_provider_without_default(tmp_path: Path) -> None:
    with pytest.raises(ProviderConfigError) as exc_info:
        _svc(tmp_path).build_default_provider()
    assert "设置 · 模型设置" in exc_info.value.detail


def test_is_usable_uid_rules(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert svc.is_usable_uid("deepseek/deepseek-flash") is True
    svc.set_model_enabled("deepseek", "deepseek-flash", False)
    assert svc.is_usable_uid("deepseek/deepseek-flash") is False
    assert svc.is_usable_uid("dashscope/qwen3.7-max") is False  # 未配 Key
    assert svc.is_usable_uid("deepseek/nope") is False
    assert svc.is_usable_uid("garbage") is False
