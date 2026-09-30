from pathlib import Path

from aitester.config import Settings
from aitester.services.model_config import ModelConfigService, mask_key
from aitester.storage import FileModelConfigRepository


def _svc(tmp_path: Path, **settings_kwargs: object) -> ModelConfigService:
    return ModelConfigService(
        FileModelConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )


def test_first_start_seeds_from_settings_and_persists(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="  sk-seed-abcdef123456  ")
    stored = FileModelConfigRepository(tmp_path / "model_config.json").load()
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
    repo = FileModelConfigRepository(tmp_path / "model_config.json")
    repo.save(saved)
    svc = ModelConfigService(
        repo, Settings(_env_file=None, deepseek_api_key="sk-should-not-appear")
    )
    assert svc.default_uid == "custom/x"
    assert FileModelConfigRepository(tmp_path / "model_config.json").load() == saved


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
