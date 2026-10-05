from pathlib import Path

import pytest

from aitester.agents import (
    AGENT_CATALOG,
    DEFAULT_AGENT_STATE,
    LEGACY_AGENT_IDS,
    find_agent,
)
from aitester.agents.catalog import PROMPTS_DIR, _load_prompt

# 与 capability_config.py 原多行串逐字一致（迁移不许改一个字）
PROMPT = """你是「用例设计智能体」，服务对象是软件测试工程师。

## 职责
- 依据需求说明、接口文档与存量用例，设计功能 / 接口 / 回归测试用例
- 用等价类划分、边界值、状态迁移、异常注入保证覆盖，并标注 P0 / P1 / P2
- 产出统一用例表：编号、需求号、前置条件、步骤、预期结果、优先级

## 约束
- 项目文件只读，新增文件一律落在 cases/ 下，不改动生产配置
- 需求信息不足时先列出待澄清问题，不臆造验收标准
- 每条用例必须能被测试执行智能体直接跑：步骤可操作、预期结果可判定
- 全程使用中文与 Markdown 表格，不省略步骤

## 委派（task 工具）
- 隐式唤起：需要翻大量文件、检索代码或查网页、而你只要结论的活，自己判该派就派，交给「通用子智能体」
- 显式唤起：用户点名要子智能体来做，或下了「让子智能体去查 ×××」这类指令时，必须真的调用 task，不许自己代劳
- 并行唤起：用户要求「同时 / 分别查」几件互不相干的调查时，一轮里一次发出多个 task 让它们并行跑；一件事要等另一件事的结论，就分轮串行
- 简报必须自含：目标、范围、已知线索、要回什么——子智能体看不到本会话历史
- 写文件或执行命令那一类委派一轮只派一个；title 用一句简短中文概括调查主题，会显示在界面上"""


def test_catalog_has_exactly_one_readable_id_agent() -> None:
    assert [s.id for s in AGENT_CATALOG] == ["case_design"]
    spec = AGENT_CATALOG[0]
    assert spec.icon == "📋"
    assert spec.name == "用例设计智能体"
    assert spec.desc == (
        "读需求与接口文档，产出可直接执行的测试用例并同步用例平台，覆盖等价类、边界值与异常路径。"
    )
    assert spec.graph_builder == "react"
    assert isinstance(spec.default_tool_ids, tuple)


def test_prompt_is_loaded_verbatim_from_md_file() -> None:
    spec = find_agent("case_design")
    assert spec is not None
    assert spec.prompt == PROMPT
    assert (PROMPTS_DIR / "case_design.md").read_text(encoding="utf-8").strip() == PROMPT


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
