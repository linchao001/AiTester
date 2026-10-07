"""第四层编写环的确定性面：常量、账本 writing 节、用例草稿与核对（裁定 35–41）。

不联网、不调真模型；全部 tmp_path，零真实 KB。
"""

from __future__ import annotations

from pathlib import Path

from aitester.case_design.constants import (
    CASE_BATCH_CAP, CASE_DELIVERY_NAME, CASE_ID_RE, CASE_PREFIX, CASES_DIR_NAME,
    ENV_PRECONDITION_MARKS, ID_RE, VAGUE_ASSERTION_MARKS,
)
from aitester.case_design.env import CaseDesignEnv
from aitester.case_design.ledger import Ledger


def test_case_id_constants():
    assert CASE_PREFIX == "cc"
    assert CASE_BATCH_CAP == 10
    assert CASES_DIR_NAME == "cases"
    assert CASE_DELIVERY_NAME == "case-delivery.md"
    assert CASE_ID_RE.match("cc-0001")
    assert not CASE_ID_RE.match("cc-1")
    assert not CASE_ID_RE.match("pt-0001")
    # 三层 id 轴绝不被用例 id 污染（裁定 35：不扩 LAYERS/TYPE_PREFIX）
    assert ID_RE.match("pt-0001") and not ID_RE.match("cc-0001")


def test_marks_are_non_empty_and_have_no_product_line_terms():
    assert VAGUE_ASSERTION_MARKS and ENV_PRECONDITION_MARKS
    joined = "，".join(VAGUE_ASSERTION_MARKS + ENV_PRECONDITION_MARKS)
    for banned in ("智会宝", "zhb", "云杉"):
        assert banned not in joined.lower()


def test_env_cases_dir(tmp_path: Path):
    env = CaseDesignEnv(project_dir=str(tmp_path), kb=None)
    env.ensure_dirs()
    assert env.cases_dir() == tmp_path / "design" / "cases"
    assert env.cases_dir().is_dir()


def test_ledger_fresh_has_writing_section_and_case_counter(tmp_path: Path):
    led = Ledger.fresh(tmp_path / "design")
    writing = led.data["writing"]
    assert writing["status"] == ""
    assert writing["targets"] == []
    assert writing["gate"] == {"round": 0, "int_round": 0, "unclear": 0, "approved_at": ""}
    assert writing["stale_batches"] == []
    assert writing["note"] == ""
    # 意见簿三件套（T21/T22 只换取桶路径、不换字段）必须从开账即在位
    assert writing["batches"] == [] and writing["opinions"] == [] and writing["unresolved"] == []
    assert led.data["counters"]["case"] == 0


def test_next_case_seq_four_digit_and_bounded(tmp_path: Path):
    led = Ledger.fresh(tmp_path / "design")
    assert led.next_case_seq() == "cc-0001"
    assert led.next_case_seq() == "cc-0002"
    led.data["counters"]["case"] = 9999
    try:
        led.next_case_seq()
    except ValueError as exc:
        assert "上限" in str(exc)
    else:
        raise AssertionError("序号到 9999 后必须响亮失败，不许静默产 cc-10000")
