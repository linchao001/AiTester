from pathlib import Path

import pytest

from aitester.agents import (
    AGENT_CATALOG,
    DEFAULT_AGENT_STATE,
    LEGACY_AGENT_IDS,
    find_agent,
)
from aitester.agents.catalog import PROMPTS_DIR, _load_prompt


def test_catalog_has_exactly_one_readable_id_agent() -> None:
    assert [s.id for s in AGENT_CATALOG] == ["case_design"]
    spec = AGENT_CATALOG[0]
    assert spec.icon == "📋"
    assert spec.name == "用例设计智能体"
    assert spec.desc == (
        "拆解业务链路、用户故事、测试点三层测试设计，产出增量测试大纲并人工审核后回写知识库；"
        "再按子链路分批编写第四层用例正文，用例只落项目空间、经末门人工确认后交付，不写知识库。"
    )
    assert spec.graph_builder == "case_design_loop"
    assert isinstance(spec.default_tool_ids, tuple)


def test_prompt_is_loaded_verbatim_from_md_file() -> None:
    spec = find_agent("case_design")
    assert spec is not None
    text = (PROMPTS_DIR / "case_design.md").read_text(encoding="utf-8").strip()
    assert spec.prompt == text                       # md 文件是唯一真相，目录条目逐字等于它
    assert "## 职责" in text and "## 委派" in text
    for marker in ("业务链路", "用户故事", "测试点"):
        assert marker in text                        # 三层概念必须在（T10 重写的目的）
    for marker in ("用例编写环", "covers", "design/cases/", "case-delivery.md"):
        assert marker in text                        # 第四片：第四层口径必须落到提示词里
    assert "只做设计部分并明示边界" not in text        # 旧边界句必须删除，否则主智能体会拒绝用例侧任务
    assert "同步用例平台" not in text                 # 2026-10-05 裁定：描述与提示词都不再提平台对接


def test_default_agent_state_is_derived_from_catalog() -> None:
    assert DEFAULT_AGENT_STATE == {
        "case_design": {
            "default_uid": "",
            "tool_ids": [
                "read",
                "write",
                "edit",
                "grep_search",
                "glob_search",
                "web_search",
                "knowledge_search",
                "task",
            ],
        },
        "general-purpose": {
            "default_uid": "",
            "tool_ids": ["read", "grep_search", "glob_search", "web_search"],
        },
        "case_review": {
            "default_uid": "",
            "tool_ids": ["read", "grep_search", "glob_search", "web_search",
                         "knowledge_search"],
        },
        "case_review_blind": {
            "default_uid": "",
            "tool_ids": ["read"],
        },
    }


def test_legacy_id_is_migration_table_not_alias() -> None:
    assert LEGACY_AGENT_IDS == {"a1": "case_design"}
    assert find_agent("a1") is None
    assert find_agent("ghost") is None


def test_missing_prompt_file_raises_with_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aitester.agents.catalog.PROMPTS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError) as exc_info:
        _load_prompt("case_design")
    assert "case_design.md" in str(exc_info.value)


def test_kb_assistant_is_platform_agent():
    from aitester.agents import AGENT_CATALOG, PLATFORM_AGENT_CATALOG, find_agent
    spec = find_agent("kb_assistant")
    assert spec is not None
    assert spec.name == "知识库助手"
    assert spec.default_tool_ids == ("read", "grep_search", "glob_search", "knowledge_search", "prepare_kb_write")
    assert all(s.id != "kb_assistant" for s in AGENT_CATALOG)      # 不进能力配置目录
    assert [s.id for s in PLATFORM_AGENT_CATALOG] == ["kb_assistant"]


# tests/ 无 __init__.py，`from tests.test_kb_api import ...` 路径不通，
# 按 test_kb_browse.py 先例原样复制 _RecordingKbManager（断言本身保持简报原文）。
def test_capabilities_view_unchanged(tmp_path):
    # GET /api/capabilities 的 agents 仍只有 case_design（形态同 test_kb_api.py:28-35）
    from types import SimpleNamespace  # noqa: F401
    from fastapi.testclient import TestClient
    from aitester.config import Settings
    from aitester.main import create_app

    class _RecordingKbManager:
        def __init__(self, exc=None):
            self.calls: list[tuple[str, dict]] = []
            self.exc = exc

        def start(self):
            pass

        def close_all(self, timeout: float = 30.0):
            pass

        async def run_job(self, name, *, project_id="default", agent_id="console", **kwargs):
            if self.exc is not None:
                raise self.exc
            self.calls.append((name, kwargs))
            return SimpleNamespace(success=True, answer="ok", metadata={"echo": name})

    app = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.json",
        settings=Settings(_env_file=None),
        kb_manager=_RecordingKbManager(),
    )
    with TestClient(app) as c:
        j = c.get("/api/capabilities").json()
    assert [a["id"] for a in j["agents"]] == ["case_design"]
    assert "prepare_kb_write" not in {t["id"] for t in j["tools"]}  # 设置页工具表保持不可见


def test_is_platform_agent_only_covers_platform_catalog() -> None:
    from aitester.agents import is_platform_agent

    assert is_platform_agent("kb_assistant") is True   # 平台内置：不进能力配置/下拉
    assert is_platform_agent("case_design") is False   # 项目智能体：走落盘与会话列表
    assert is_platform_agent("ghost") is False         # 未知 id 不是「平台」，未知由 find_agent 负责拒
