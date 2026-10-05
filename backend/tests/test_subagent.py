"""子智能体：目录结构锁、能力视图子表、存量激活路径（T2）；task 壳（T3）；drive 守卫（T4）。"""

from pathlib import Path

import pytest
from langchain_core.tools import ToolException

from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools.observation import FileObservationStore
from aitester.adapters.tools.subagent_tools import (
    TASK_TOOL_DESC,
    build_task_tool,
    render_description,
)
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
    assert not spec.desc.startswith("Read-only")                      # 面可调：静态文案不许断言只读
    assert spec.desc.startswith("Investigator for delegated tasks")   # 模型可见：英文
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


# —— T3：task 工具壳与注册表缝 ——

_ROSTER = {"general-purpose": {"name": "通用子智能体", "desc": "Investigator.",
                              "tools": ["read", "grep_search"]}}


def test_task_tool_rejects_unknown_subagent_type() -> None:
    """R11：未知子类型是模型可见错误——英文、带在册清单，不许静默回退默认体。"""
    tool = build_task_tool(_ROSTER, build_child=lambda _aid: None,
                           drive=lambda _c, _b, **_k: "摘要")
    with pytest.raises(ToolException) as exc:
        tool._run(description="look into it", subagent_type="nope")
    assert "Unknown subagent_type 'nope'" in str(exc.value)
    assert "general-purpose" in str(exc.value)


def test_task_tool_wraps_build_failure_in_english() -> None:
    """装配失败（如 provider 配置缺失）也要以模型可见英文错误收场，模型可自纠。"""
    def boom(_aid: str):
        raise RuntimeError("provider config missing")

    tool = build_task_tool(_ROSTER, build_child=boom, drive=lambda _c, _b, **_k: "摘要")
    with pytest.raises(ToolException) as exc:
        tool._run(description="x")
    assert str(exc.value) == ("Subagent 'general-purpose' could not be started: "
                              "provider config missing")


def test_task_tool_passes_drive_the_contract_keys() -> None:
    """drive 收到的七件：child / brief / call_id / name / title（缺省回落名字）/ config /
    isolated（并行安全表决定走不走派生 ns，R14）。"""
    seen: dict = {}
    child = object()

    def drive(c, brief, *, call_id, name, title, config, isolated):
        seen.update(child=c, brief=brief, call_id=call_id, name=name, title=title,
                    config=config, isolated=isolated)
        return "子摘要"

    tool = build_task_tool(_ROSTER, build_child=lambda _aid: child, drive=drive,
                           parallel={"general-purpose": True})
    cfg = {"configurable": {"thread_id": "t"}}
    out = tool._run(description="调查失败用例", subagent_type="general-purpose",
                    title="", tool_call_id="c9", config=cfg)
    assert out == "子摘要"
    assert seen == {"child": child, "brief": "调查失败用例", "call_id": "c9",
                    "name": "通用子智能体", "title": "通用子智能体", "config": cfg,
                    "isolated": True}


def test_task_tool_defaults_to_serial_when_parallel_table_is_silent() -> None:
    """表里没有 = 按串行办：isolated 缺省 False，宁可少并行也不踩同 ns 双跑。"""
    seen: dict = {}

    def drive(c, brief, *, call_id, name, title, config, isolated):
        seen["isolated"] = isolated
        return "摘要"

    tool = build_task_tool(_ROSTER, build_child=lambda _aid: object(), drive=drive)
    tool._run(description="x", subagent_type="general-purpose", tool_call_id="c1",
              config=None)
    assert seen == {"isolated": False}


def test_render_description_lists_roster() -> None:
    text = render_description(_ROSTER)
    assert text.startswith(TASK_TOOL_DESC)
    assert "Available subagents:" in text
    assert ("- 通用子智能体 (general-purpose): Investigator. "
            "[tool face: read, grep_search]") in text


def test_render_description_preamble_does_not_claim_read_only() -> None:
    """T10 走查 8：固定文案曾写「read-only investigation」，父模型据此拒绝派发写任务。
    只读只能由每行的当前工具面表达，不能在 preamble 里断言。"""
    assert "read-only investigation" not in TASK_TOOL_DESC
    assert "[tool face: none]" in render_description(
        {"ghost": {"name": "幽灵", "desc": "d"}})      # 面无条目时不崩、不假装有能力


def test_registry_registers_task_only_when_injected(tmp_path: Path) -> None:
    """注入缝：默认注册表没有 task；装配层传了才有——深度 1 结构锁的装配基点（R7）。"""
    tool = build_task_tool(_ROSTER, build_child=lambda _aid: object(),
                           drive=lambda _c, _b, **_k: "摘要")
    plain = build_default_registry(cwd=str(tmp_path), session_id="s1",
                                   observed=FileObservationStore())
    assert "task" not in {t.name for t in plain.as_langchain_tools()}
    with_task = build_default_registry(cwd=str(tmp_path), session_id="s1",
                                       observed=FileObservationStore(), task=tool)
    assert "task" in {t.name for t in with_task.as_langchain_tools()}
