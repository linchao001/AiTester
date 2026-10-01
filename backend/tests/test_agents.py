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
- 全程使用中文与 Markdown 表格，不省略步骤"""


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
            ],
        }
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
