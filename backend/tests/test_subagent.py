"""子智能体：目录结构锁、能力视图子表、存量激活路径（T2）；task 壳（T3）；drive 守卫（T4）。"""

from pathlib import Path

from aitester.agents import (
    AGENT_CATALOG,
    DEFAULT_AGENT_STATE,
    SUBAGENT_CATALOG,
    find_agent,
    find_subagent,
)
from aitester.services.capability_config import TOOL_CATALOG
from aitester.storage import FileJsonConfigRepository
from test_capability_config import _stored, _svc  # noqa: F401  复用能力配置夹具（跨文件复用先例）


def test_subagent_catalog_is_separate_from_agent_catalog() -> None:
    assert [s.id for s in SUBAGENT_CATALOG] == ["general-purpose"]
    spec = SUBAGENT_CATALOG[0]
    assert spec.icon == "🕵️" and spec.name == "通用子智能体"
    assert spec.desc.startswith("Read-only investigator")            # 模型可见：英文
    assert spec.default_tool_ids == ("read", "grep_search", "glob_search", "web_search")
    assert "task" not in spec.default_tool_ids                       # 深度 1 结构锁
    assert all(s.id != "general-purpose" for s in AGENT_CATALOG)     # 不进直选面
    assert find_subagent("general-purpose") is spec
    assert find_subagent("case_design") is None


def test_case_design_default_face_includes_task() -> None:
    spec = find_agent("case_design")
    assert spec is not None and "task" in spec.default_tool_ids


def test_tool_catalog_has_task_after_knowledge_tools() -> None:
    assert len(TOOL_CATALOG) == 11 and TOOL_CATALOG[-1]["id"] == "task"
    entry = TOOL_CATALOG[-1]
    assert entry["group"] == "子智能体工具" and entry["os"] == "全平台"
    assert entry["desc"].startswith("把一件调查")                     # UI 文案：中文（R12）


def test_view_exposes_subagents_beside_agents(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    view = capability.get_view()
    assert [s["id"] for s in view["subagents"]] == ["general-purpose"]
    assert [a["id"] for a in view["agents"]] == ["case_design"]      # 不进直选面
    sub = view["subagents"][0]
    assert sub["tool_ids"] == ["read", "grep_search", "glob_search", "web_search"]
    assert sub["default_uid"] == "" and sub["prompt"].startswith("你是「通用子智能体」")


def test_legacy_config_activates_task_via_settings_path(tmp_path: Path) -> None:
    """验收 4：老 capability_config 也能激活——启用（种子已开）→ 勾选（设置接口）两步。"""
    FileJsonConfigRepository(tmp_path / "capability_config.json").save(
        {
            "version": 1,
            "tool_state": {t["id"]: True for t in TOOL_CATALOG if t["id"] != "task"},
            "agents": {
                "case_design": {
                    "default_uid": "",
                    "tool_ids": ["read", "write", "edit", "grep_search", "glob_search",
                                 "web_search"],
                }
            },
        }
    )
    capability, _ = _svc(tmp_path)
    stored = _stored(tmp_path)
    assert stored["tool_state"]["task"] is True                      # 新工具按种子默认启用
    assert "task" not in stored["agents"]["case_design"]["tool_ids"]  # 存量勾选不被静默改写
    assert stored["agents"]["general-purpose"] == {                  # 子智能体状态随目录补种
        "default_uid": "", "tool_ids": ["read", "grep_search", "glob_search", "web_search"]}
    capability.set_agent_tools(
        "case_design",
        ["read", "write", "edit", "grep_search", "glob_search", "web_search", "task"])
    assert "task" in _stored(tmp_path)["agents"]["case_design"]["tool_ids"]
