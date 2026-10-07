"""第四层编写环的确定性面：常量、账本 writing 节、用例草稿与核对（裁定 35–41）。

不联网、不调真模型；全部 tmp_path，零真实 KB。
"""

from __future__ import annotations

import json
from pathlib import Path

from aitester.case_design.constants import (
    CASE_BATCH_CAP, CASE_DELIVERY_NAME, CASE_ID_RE, CASE_PREFIX, CASES_DIR_NAME,
    ENV_PRECONDITION_MARKS, ID_RE, VAGUE_ASSERTION_MARKS,
)
from aitester.case_design.env import CaseDesignEnv
from aitester.case_design.ledger import Ledger, _fresh_data


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


def test_load_backfills_writing_section_for_legacy_ledger(tmp_path: Path):
    """本片之前写的 design/ledger.json 没有 writing 节与 counters.case：load 必须补位。

    T19–T22 无条件硬读 led.data["writing"]，旧账本不补会在驱动层泛捕获里炸成
    「测试设计任务中止（内部错误）」——续跑环对存量项目直接不可用。
    """
    design_dir = tmp_path / "design"
    design_dir.mkdir()
    # 手工写旧形态 JSON（不是 _fresh_data() 现搭的），模拟本片之前落盘的账本
    legacy = {
        "version": 1,
        "status": "active",
        "task": {},
        "cursor": {"stage": "plan", "layer": "", "block": "", "round": 0,
                   "source": "block", "nudge": 0},
        "layers": {layer: {"state": "pending", "mode": "", "blocks": [],
                           "audit_round": 0, "unresolved": [], "opinions": []}
                   for layer in ("chain", "story", "point")},
        "counters": {"chain": 3, "story": 5, "point": 7},
        "gate": {"round": 0, "approved_at": ""},
        "writeback": {"done": False, "log": []},
        "history": [],
    }
    (design_dir / "ledger.json").write_text(
        json.dumps(legacy, ensure_ascii=False, indent=1), encoding="utf-8")

    led = Ledger.load(design_dir)
    assert led is not None
    assert led.data["writing"] == _fresh_data()["writing"]
    assert led.data["counters"]["case"] == 0
    # 补位不吞旧值：三层计数原样保留
    assert led.data["counters"]["point"] == 7
    assert led.data["counters"]["chain"] == 3
    assert led.next_case_seq() == "cc-0001"


def test_load_keeps_existing_writing_section(tmp_path: Path):
    """背填是填空不是覆盖：现账本里已推进的 writing 状态 reload 后必须原样在。"""
    design_dir = tmp_path / "design"
    design_dir.mkdir()
    led = Ledger.fresh(design_dir)
    led.data["writing"]["status"] = "active"
    led.save()

    reloaded = Ledger.load(design_dir)
    assert reloaded is not None
    assert reloaded.data["writing"]["status"] == "active"
