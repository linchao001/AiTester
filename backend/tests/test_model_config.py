from pathlib import Path

import pytest

from aitester.adapters.llm import ProviderConfigError
from aitester.adapters.llm import openai_compat
from aitester.adapters.llm.probe import ProbeError
from aitester.config import Settings
from aitester.services import model_config
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
    # 原型 DashScope 模型表首行是 Qwen3.8 Max，回落按表序取其首
    assert svc.default_uid == "dashscope/qwen3.8-max"


def test_seed_without_keys_leaves_default_empty(tmp_path: Path) -> None:
    assert _svc(tmp_path).default_uid == ""


def test_seed_stores_only_runtime_keys(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-seed-abcdef123456")
    stored = FileJsonConfigRepository(tmp_path / "model_config.json").load()
    assert stored is not None
    model = stored["providers"][0]["models"][0]
    assert set(model) == {"id", "enabled", "max_output", "context"}
    assert svc.default_uid == "deepseek/deepseek-flash"


def test_view_carries_catalog_display_fields(tmp_path: Path) -> None:
    view = _svc(tmp_path).get_view()
    dashscope = next(p for p in view["providers"] if p["id"] == "dashscope")
    by_id = {m["id"]: m for m in dashscope["models"]}
    assert by_id["qwen3.7-max"]["name"] == "Qwen3.7 Max"
    assert by_id["qwen3.7-max"]["recommended"] is True
    assert by_id["qwen3.6-plus"]["caps"] == ["FC", "视觉", "视频"]
    assert by_id["qwen3.8-max"]["note"] == "目录数据 · 128K 上下文"


def test_view_orders_models_by_catalog(tmp_path: Path) -> None:
    saved = {
        "version": 1,
        "default_uid": "",
        "providers": [
            {
                "id": "dashscope",
                "name": "通义千问 · DashScope",
                "base_url": "https://x",
                "api_key": "",
                "models": [
                    {"id": "custom-new", "enabled": True, "max_output": 1, "context": 1},
                    {"id": "qwen3.6-plus", "enabled": True, "max_output": 1, "context": 1},
                    {"id": "qwen3.8-max", "enabled": True, "max_output": 1, "context": 1},
                    {"id": "qwen3.7-max", "enabled": True, "max_output": 1, "context": 1},
                ],
            }
        ],
    }
    repo = FileJsonConfigRepository(tmp_path / "model_config.json")
    repo.save(saved)
    view = ModelConfigService(repo, Settings(_env_file=None)).get_view()
    ids = [m["id"] for m in view["providers"][0]["models"]]
    # 目录序在前，目录外的模型保持存储原序排在最后
    assert ids == ["qwen3.8-max", "qwen3.7-max", "qwen3.6-plus", "custom-new"]
    assert view["providers"][0]["models"][-1].get("name") is None


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
    # 提供商序 → 模型表序：DashScope 表首为 Qwen3.8 Max（与原型一致）
    assert svc.default_uid == "dashscope/qwen3.8-max"


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
        "stream_usage": True,
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


def test_build_provider_with_explicit_uid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    class _FakeChatForBuild:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

    monkeypatch.setattr(openai_compat, "ChatOpenAI", _FakeChatForBuild)
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789")
    provider = svc.build_provider("dashscope/qwen3.7-max")
    assert provider.model_ref == "dashscope/qwen3.7-max"
    assert recorded["model"] == "qwen3.7-max"
    assert recorded["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_build_provider_empty_uid_keeps_default_model_copy(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.build_provider("")
    # 文案逐字保持：现有 400 断言依赖「设置 · 模型设置」
    assert exc_info.value.detail == "尚未配置默认模型：请在 设置 · 模型设置 中填写 API Key 并选择默认 LLM"


def test_build_provider_rejects_unusable_model(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    svc.set_model_enabled("deepseek", "deepseek-flash", False)
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.build_provider("deepseek/deepseek-flash")
    assert "已停用" in exc_info.value.detail

    other_dir = Path(tmp_path, "other")
    other_dir.mkdir()
    other = _svc(other_dir)  # 两个提供商都没配 Key
    with pytest.raises(ProviderConfigError) as exc_info:
        other.build_provider("dashscope/qwen3.7-max")
    assert "未配置 API Key" in exc_info.value.detail


def test_build_provider_unknown_uid_raises_not_found(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    with pytest.raises(ConfigNotFoundError):
        svc.build_provider("deepseek/nope")
    with pytest.raises(ConfigNotFoundError):
        svc.build_provider("glm/deepseek-flash")


def test_build_default_provider_delegates_to_build_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[str] = []

    class _FakeChatDeleg:
        def __init__(self, **kwargs: object) -> None:
            recorded.append(str(kwargs["model"]))

    monkeypatch.setattr(openai_compat, "ChatOpenAI", _FakeChatDeleg)
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert svc.build_default_provider().model_ref == "deepseek/deepseek-flash"
    assert svc.build_provider("deepseek/deepseek-flash").model_ref == "deepseek/deepseek-flash"
    assert recorded == ["deepseek-flash", "deepseek-flash"]


def _fake_probe(monkeypatch, *, latency: int = 123, error: Exception | None = None) -> list:
    calls: list[dict] = []

    def fake_probe_model(**kwargs: object) -> int:
        calls.append(kwargs)
        if error is not None:
            raise error
        return latency

    monkeypatch.setattr(model_config, "probe_model", fake_probe_model)
    return calls


def test_probe_success_reports_latency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_probe(monkeypatch, latency=87)
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert svc.probe_provider("deepseek") == {"ok": True, "latency_ms": 87}
    assert calls == [
        {
            "model": "deepseek-flash",
            "api_key": "sk-x123456789",
            "base_url": "https://api.deepseek.com",
        }
    ]


def test_probe_draft_key_beats_missing_stored_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_probe(monkeypatch)
    svc = _svc(tmp_path)
    assert svc.probe_provider("deepseek", "  sk-draft-123456  ")["ok"] is True
    assert calls[0]["api_key"] == "sk-draft-123456"


def test_probe_draft_key_beats_stored_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_probe(monkeypatch)
    svc = _svc(tmp_path, deepseek_api_key="sk-stored-123456")
    svc.probe_provider("deepseek", "sk-draft-123456")
    assert calls[0]["api_key"] == "sk-draft-123456"


def test_probe_blank_draft_falls_back_to_stored_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_probe(monkeypatch)
    svc = _svc(tmp_path, deepseek_api_key="sk-stored-123456")
    svc.probe_provider("deepseek", "   ")
    assert calls[0]["api_key"] == "sk-stored-123456"


def test_probe_never_persists_draft_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_probe(monkeypatch)
    svc = _svc(tmp_path)
    before = FileJsonConfigRepository(tmp_path / "model_config.json").load()
    svc.probe_provider("deepseek", "sk-draft-123456")
    after = FileJsonConfigRepository(tmp_path / "model_config.json").load()
    assert after == before
    assert after is not None
    assert "sk-draft-123456" not in str(after)
    assert svc.get_view()["providers"][0]["has_key"] is False
    assert svc.default_uid == ""


def test_probe_targets_first_enabled_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_probe(monkeypatch)
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    svc.probe_provider("deepseek")
    assert calls[0]["model"] == "deepseek-flash"

    calls.clear()
    svc.set_model_enabled("deepseek", "deepseek-flash", False)
    svc.probe_provider("deepseek")
    assert calls[0]["model"] == "deepseek-v4-pro"


def test_probe_without_key_fails_without_requesting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_probe(monkeypatch)
    result = _svc(tmp_path).probe_provider("deepseek")
    assert result["ok"] is False
    assert "未配置 API Key" in result["reason"]
    assert calls == []


def test_probe_without_enabled_model_fails_without_requesting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_probe(monkeypatch)
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    svc.set_model_enabled("deepseek", "deepseek-flash", False)
    svc.set_model_enabled("deepseek", "deepseek-v4-pro", False)
    result = svc.probe_provider("deepseek")
    assert result["ok"] is False
    assert "已启用模型" in result["reason"]
    assert calls == []


def test_probe_reports_upstream_failure_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_probe(monkeypatch, error=ProbeError("API Key 无效或无权限"))
    result = _svc(tmp_path, deepseek_api_key="sk-x123456789").probe_provider("deepseek")
    assert result == {"ok": False, "reason": "API Key 无效或无权限"}


def test_probe_unknown_provider_raises_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_probe(monkeypatch)
    with pytest.raises(ConfigNotFoundError):
        _svc(tmp_path).probe_provider("glm")
    assert calls == []
