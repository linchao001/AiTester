import copy
import sys
from pathlib import Path
from typing import Any

import pytest

from aitester.adapters.tools.availability import unavailable_reason
from aitester.agents import AGENT_CATALOG, DEFAULT_AGENT_STATE, LEGACY_AGENT_IDS
from aitester.config import Settings
from aitester.services.capability_config import (
    TOOL_CATALOG,
    CapabilityConfigError,
    CapabilityConfigService,
    default_tool_enabled,
)
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
from aitester.storage import FileJsonConfigRepository


def _seed_tool_state() -> dict[str, bool]:
    """按当前机器的探测结果现算种子，测试断言不写死平台结论。"""
    return {t["id"]: default_tool_enabled(t["id"]) for t in TOOL_CATALOG}


_FAKE_REASONS: dict[str, str] = {"pwsh": "本机未找到 pwsh 可执行文件"}



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


class _CountingRepo:
    """假仓储：只记录 save 次数，用来证明「形状没漂移就不重写磁盘」。"""

    def __init__(self, config: dict[str, Any] | None) -> None:
        self._config = config
        self.save_calls = 0

    def load(self) -> dict[str, Any] | None:
        return self._config

    def save(self, config: dict[str, Any]) -> None:
        self.save_calls += 1
        self._config = config


def _model_config(tmp_path: Path) -> ModelConfigService:
    return ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None),
    )


def test_first_start_seeds_and_persists(tmp_path: Path) -> None:
    _svc(tmp_path)
    stored = _stored(tmp_path)
    assert stored["version"] == 1
    assert stored["tool_state"] == _seed_tool_state()
    assert stored["tool_state"]["read"] is True  # 文件工具全平台可用
    assert stored["tool_state"]["web_search"] is True  # 已实现：全平台可用，种子即启用
    if sys.platform == "win32":
        assert stored["tool_state"]["bash"] is False  # Windows 默认关 Bash，用户可显式开启
    assert stored["agents"] == {
        "case_design": {
            "default_uid": "",
            "tool_ids": ["read", "write", "edit", "grep_search", "glob_search",
                         "web_search", "task"],
        },
        "general-purpose": {
            "default_uid": "",
            "tool_ids": ["read", "grep_search", "glob_search", "web_search"],
        },
    }


def test_existing_file_is_not_reseeded(tmp_path: Path) -> None:
    saved = {
        "version": 1,
        "tool_state": {**_seed_tool_state(), "read": False},
        "agents": {
            "case_design": {"default_uid": "deepseek/deepseek-flash", "tool_ids": ["write"]},
            # read 已被禁用 → 新补种的子智能体面里也不留 read（剥禁用工具对所有条目一视同仁）
            "general-purpose": {"default_uid": "",
                                "tool_ids": ["grep_search", "glob_search", "web_search"]},
        },
    }
    FileJsonConfigRepository(tmp_path / "capability_config.json").save(saved)
    capability, _ = _svc(tmp_path)
    assert _stored(tmp_path) == saved
    agent = capability.get_view()["agents"][0]
    assert agent["default_uid"] == "deepseek/deepseek-flash"
    assert agent["tool_ids"] == ["write"]


def test_hand_edited_drift_is_normalized_and_persisted(tmp_path: Path) -> None:
    FileJsonConfigRepository(tmp_path / "capability_config.json").save(
        {
            "version": 9,
            "tool_state": {"read": "yes", "qw": True},
            "agents": {
                "case_design": {"tool_ids": ["qw", "pwsh", "read", "read"]},
                "ghost": {},
            },
        }
    )
    capability, _ = _svc(tmp_path)  # 构造与读取都不抛 KeyError
    view = capability.get_view()
    assert [t["id"] for t in view["tools"]] == [t["id"] for t in TOOL_CATALOG]
    assert [t["enabled"] for t in view["tools"]] == [
        _seed_tool_state()[t["id"]] for t in TOOL_CATALOG
    ]
    assert [a["id"] for a in view["agents"]] == ["case_design"]
    # 未知 id 丢弃、去重保序，不可用的 pwsh 一并落掉（见下方落盘断言）
    kept = [t for t in ("pwsh", "read") if _seed_tool_state()[t]]
    assert view["agents"][0]["tool_ids"] == kept
    stored = _stored(tmp_path)
    assert stored["version"] == 1  # version 归一到目录种子并被真正使用
    assert stored["tool_state"] == _seed_tool_state()  # bool("yes") 收敛为 True，与种子同值
    assert stored["agents"] == {
        "case_design": {"default_uid": "", "tool_ids": kept},
        "general-purpose": {"default_uid": "",
                            "tool_ids": ["read", "grep_search", "glob_search", "web_search"]},
    }


def test_seed_identical_config_is_not_rewritten(tmp_path: Path) -> None:
    model_config = _model_config(tmp_path)
    repo = _CountingRepo(
        {
            "version": 1,
            "tool_state": _seed_tool_state(),
            "agents": copy.deepcopy(DEFAULT_AGENT_STATE),
        }
    )
    CapabilityConfigService(repo, model_config)
    assert repo.save_calls == 0  # 与磁盘现状相同就不重写
    # 对照组：文件缺失仍按既有语义种子并落盘一次，同时证明计数器不是恒零的假件
    missing = _CountingRepo(None)
    CapabilityConfigService(missing, model_config)
    assert missing.save_calls == 1


def test_catalog_seeds_exactly_one_agent_and_eleven_tools() -> None:
    assert [s.id for s in AGENT_CATALOG] == ["case_design"]
    assert AGENT_CATALOG[0].name == "用例设计智能体"
    assert [t["id"] for t in TOOL_CATALOG] == [
        "read",
        "write",
        "edit",
        "grep_search",
        "glob_search",
        "pwsh",
        "bash",
        "web_search",
        "knowledge_search",
        "save_to_knowledge",
        "task",
    ]


def test_get_view_tools_shape_and_carried_by(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    view = capability.get_view()
    assert [t["id"] for t in view["tools"]] == [t["id"] for t in TOOL_CATALOG]
    read = view["tools"][0]
    assert read["group"] == "文件处理工具"
    assert read["enabled"] is True
    assert read["available"] is True and read["unavailable_reason"] is None
    assert read["carried_by"] == ["case_design", "general-purpose"]
    bash = next(t for t in view["tools"] if t["id"] == "bash")
    assert bash["os"] == "macOS / Linux"
    assert bash["enabled"] is _seed_tool_state()["bash"]
    assert bash["carried_by"] == []


def test_view_availability_matches_probe(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    view = capability.get_view()
    for tool in view["tools"]:
        assert tool["available"] is (unavailable_reason(tool["id"]) is None)
        assert tool["unavailable_reason"] == unavailable_reason(tool["id"])
    web = next(t for t in view["tools"] if t["id"] == "web_search")
    assert web["available"] is True
    assert web["unavailable_reason"] is None
    assert web["enabled"] is True


def test_get_view_agent_carries_readonly_prompt(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    agent = capability.get_view()["agents"][0]
    assert agent["id"] == "case_design"
    assert agent["icon"] == "📋"
    assert "## 职责" in agent["prompt"]
    assert agent["default_uid"] == ""
    assert agent["effective_uid"] == ""
    assert agent["tool_ids"] == [
        "read",
        "write",
        "edit",
        "grep_search",
        "glob_search",
        "web_search",
        "task",
    ]


def test_effective_uid_follows_global_default_when_unset(tmp_path: Path) -> None:
    capability, model_config = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert model_config.default_uid == "deepseek/deepseek-flash"
    assert capability.get_view()["agents"][0]["effective_uid"] == "deepseek/deepseek-flash"


def test_effective_uid_prefers_agent_default(tmp_path: Path) -> None:
    capability, model_config = _svc(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("case_design", "dashscope/qwen3.7-max")
    agent = capability.get_view()["agents"][0]
    assert agent["default_uid"] == "dashscope/qwen3.7-max"
    assert agent["effective_uid"] == "dashscope/qwen3.7-max"


def test_effective_uid_falls_back_when_agent_default_becomes_unusable(tmp_path: Path) -> None:
    capability, model_config = _svc(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("case_design", "dashscope/qwen3.7-max")
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
    capability.set_agent_default_model("case_design", "deepseek/deepseek-v4-pro")
    assert _stored(tmp_path)["agents"]["case_design"]["default_uid"] == "deepseek/deepseek-v4-pro"
    capability.set_agent_default_model("case_design", "")
    assert _stored(tmp_path)["agents"]["case_design"]["default_uid"] == ""


def test_set_agent_default_model_rejects_unusable(tmp_path: Path) -> None:
    capability, model_config = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    model_config.set_model_enabled("deepseek", "deepseek-flash", False)
    with pytest.raises(CapabilityConfigError) as exc_info:
        capability.set_agent_default_model("case_design", "deepseek/deepseek-flash")
    assert "设置 · 模型设置" in exc_info.value.detail
    with pytest.raises(CapabilityConfigError):
        capability.set_agent_default_model("case_design", "dashscope/qwen3.7-max")  # 未配 Key
    with pytest.raises(CapabilityConfigError):
        capability.set_agent_default_model("case_design", "deepseek/nope")  # 不存在
    assert _stored(tmp_path)["agents"]["case_design"]["default_uid"] == ""


def test_set_agent_default_model_unknown_agent_raises_404_error(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        capability.set_agent_default_model("a9", "")


def test_set_agent_tools_keeps_order_and_dedups(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_agent_tools("case_design", ["edit", "read", "read", "write"])
    assert _stored(tmp_path)["agents"]["case_design"]["tool_ids"] == ["edit", "read", "write"]
    view = capability.get_view()
    assert view["agents"][0]["tool_ids"] == ["edit", "read", "write"]
    assert [t["id"] for t in view["tools"] if t["carried_by"]] == [
        "read", "write", "edit", "grep_search", "glob_search", "web_search"]


def test_set_agent_tools_empty_list_is_allowed(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_agent_tools("case_design", [])
    assert _stored(tmp_path)["agents"]["case_design"]["tool_ids"] == []
    carried = {t["id"]: t["carried_by"] for t in capability.get_view()["tools"]}
    # 主智能体清空后不再有携带者；剩下的携带者只可能来自子智能体那一张面
    assert {tid: v for tid, v in carried.items() if v} == {
        tid: ["general-purpose"]
        for tid in ("read", "grep_search", "glob_search", "web_search")}


def test_set_agent_tools_rejects_disabled_tool(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_tool_enabled("edit", False)  # 先制造一个确定被禁用的工具
    with pytest.raises(CapabilityConfigError) as exc_info:
        capability.set_agent_tools("case_design", ["read", "edit"])
    assert "edit" in exc_info.value.detail
    assert "工具" in exc_info.value.detail
    assert _stored(tmp_path)["agents"]["case_design"]["tool_ids"] == [
        "read",
        "write",
        "grep_search",
        "glob_search",
        "web_search",
        "task",
    ]


def test_set_agent_tools_accepts_web_search(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_agent_tools("case_design", ["read", "web_search"])
    assert _stored(tmp_path)["agents"]["case_design"]["tool_ids"] == ["read", "web_search"]


def test_web_search_tool_can_be_toggled(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_tool_enabled("web_search", False)
    assert _stored(tmp_path)["tool_state"]["web_search"] is False
    capability.set_tool_enabled("web_search", True)
    assert _stored(tmp_path)["tool_state"]["web_search"] is True


def test_missing_shell_cannot_be_enabled_or_carried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aitester.services.capability_config.unavailable_reason",
        lambda tool_id: _FAKE_REASONS.get(tool_id),
    )
    capability, _ = _svc(tmp_path)  # 种子按假探测结果：pwsh 落为禁用
    assert _stored(tmp_path)["tool_state"]["pwsh"] is False
    with pytest.raises(CapabilityConfigError) as exc_info:
        capability.set_tool_enabled("pwsh", True)
    assert "本机未找到 pwsh 可执行文件" in exc_info.value.detail
    with pytest.raises(CapabilityConfigError) as exc_info:
        capability.set_agent_tools("case_design", ["read", "pwsh"])
    assert "不可用" in exc_info.value.detail


def test_stored_config_heals_unavailable_carriers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aitester.services.capability_config.unavailable_reason",
        lambda tool_id: _FAKE_REASONS.get(tool_id),
    )
    FileJsonConfigRepository(tmp_path / "capability_config.json").save(
        {
            "version": 1,
            "tool_state": {**_seed_tool_state(), "pwsh": True},
            "agents": {"case_design": {"default_uid": "", "tool_ids": ["read", "pwsh"]}},
        }
    )
    capability, _ = _svc(tmp_path)
    stored = _stored(tmp_path)
    assert stored["tool_state"]["pwsh"] is False  # 手改出来的启用态被落回
    assert stored["agents"]["case_design"]["tool_ids"] == ["read"]
    assert capability.get_view()["agents"][0]["tool_ids"] == ["read"]


def test_set_agent_tools_unknown_ids_raise_404_error(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        capability.set_agent_tools("case_design", ["nope"])
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
    assert stored["agents"]["case_design"]["tool_ids"] == [
        "write",
        "edit",
        "grep_search",
        "glob_search",
        "web_search",
        "task",
    ]


def test_reenable_tool_does_not_restore_carriers(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_tool_enabled("read", False)
    capability.set_tool_enabled("read", True)
    stored = _stored(tmp_path)
    assert stored["tool_state"]["read"] is True
    assert "read" not in stored["agents"]["case_design"]["tool_ids"]
    read = next(t for t in capability.get_view()["tools"] if t["id"] == "read")
    assert read["enabled"] is True and read["carried_by"] == []


def test_set_tool_enabled_unknown_tool_raises_404_error(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        capability.set_tool_enabled("nope", False)
    with pytest.raises(ConfigNotFoundError):
        capability.set_agent_tools("case_design", ["nope"])
    assert capability.set_tool_enabled("edit", True) is None


def test_legacy_agent_id_state_is_migrated_once(tmp_path: Path) -> None:
    # 只用全平台可用的 read/write：本机 pwsh 携带态的保留由 Task 6 Step 9 实测覆盖
    FileJsonConfigRepository(tmp_path / "capability_config.json").save(
        {
            "version": 1,
            "tool_state": _seed_tool_state(),
            "agents": {
                "a1": {
                    "default_uid": "deepseek/deepseek-flash",
                    "tool_ids": ["read", "write"],
                }
            },
        }
    )
    capability, _ = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    stored = _stored(tmp_path)
    # 状态整搬过来，旧键消失（不是别名）
    assert "a1" not in stored["agents"]
    assert stored["agents"]["case_design"]["default_uid"] == "deepseek/deepseek-flash"
    assert stored["agents"]["case_design"]["tool_ids"] == ["read", "write"]
    agent = capability.get_view()["agents"][0]
    assert agent["id"] == "case_design"
    assert agent["default_uid"] == "deepseek/deepseek-flash"
    assert agent["effective_uid"] == "deepseek/deepseek-flash"


def test_migration_does_not_overwrite_existing_new_key(tmp_path: Path) -> None:
    FileJsonConfigRepository(tmp_path / "capability_config.json").save(
        {
            "version": 1,
            "tool_state": _seed_tool_state(),
            "agents": {
                "a1": {"default_uid": "deepseek/deepseek-flash", "tool_ids": ["read"]},
                "case_design": {"default_uid": "", "tool_ids": ["write"]},
            },
        }
    )
    _svc(tmp_path)
    stored = _stored(tmp_path)
    assert stored["agents"] == {
        "case_design": {"default_uid": "", "tool_ids": ["write"]},
        "general-purpose": {"default_uid": "",
                            "tool_ids": ["read", "grep_search", "glob_search", "web_search"]},
    }


def test_legacy_id_is_not_an_alias_at_read_time(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    assert [a["id"] for a in capability.get_view()["agents"]] == ["case_design"]
    assert LEGACY_AGENT_IDS == {"a1": "case_design"}
    with pytest.raises(ConfigNotFoundError) as exc_info:
        capability.agent_state("a1")
    assert "未知智能体" in exc_info.value.detail


def test_agent_state_returns_copy_and_is_public(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    state = capability.agent_state("case_design")
    assert state == {
        "default_uid": "",
        "tool_ids": ["read", "write", "edit", "grep_search", "glob_search", "web_search", "task"],
    }
    state["tool_ids"].append("pwsh")
    state["default_uid"] = "hacked"
    assert capability.agent_state("case_design") == {
        "default_uid": "",
        "tool_ids": ["read", "write", "edit", "grep_search", "glob_search", "web_search", "task"],
    }


def test_effective_uid_is_public_and_rejects_unknown_agent(tmp_path: Path) -> None:
    capability, model_config = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert capability.effective_uid("case_design") == "deepseek/deepseek-flash"
    capability.set_agent_default_model("case_design", "deepseek/deepseek-v4-pro")
    assert capability.effective_uid("case_design") == "deepseek/deepseek-v4-pro"
    with pytest.raises(ConfigNotFoundError):
        capability.effective_uid("ghost")
