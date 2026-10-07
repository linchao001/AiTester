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
from aitester.case_design.schema import CaseDraft
from aitester.case_design.writing import (
    collect_covers, compose_case_delivery, denominator_points, plan_case_targets,
    run_case_checks,
)


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


def test_load_backfill_is_narrow_not_deep_merge(tmp_path: Path):
    """背填是窄补位不是深合并：只补 writing 与 counters.case，别的缺什么不发明什么。

    _backfill_fourth_slice 若顺手 deep-merge，真坏掉的账本（比如 gate 节整体丢失）会被
    洗成「看起来完好」——腐坏被静默吞掉，后续环基于假地基推进。缺键必须露出来。
    """
    design_dir = tmp_path / "design"
    design_dir.mkdir()
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
        # 刻意缺 gate（旧账本本应有它）+ 缺 writing/counters.case（本片新增位）
        "writeback": {"done": False, "log": []},
        "history": [],
    }
    (design_dir / "ledger.json").write_text(
        json.dumps(legacy, ensure_ascii=False, indent=1), encoding="utf-8")

    led = Ledger.load(design_dir)
    assert led is not None
    # 本片新增的两个位照补
    assert led.data["writing"] == _fresh_data()["writing"]
    assert led.data["counters"]["case"] == 0
    # 本不属于本片背填范围的缺键：不发明，保持缺失让坏账本自己露出来
    assert "gate" not in led.data


def _pt(pid, story, chain_stories=None):
    return {"id": pid, "story": story, "name": f"点{pid}", "directions": ["正向"]}


def test_plan_case_targets_batches_by_cap_and_sorts_chains():
    chain_rows = [{"id": "ch-0002"}, {"id": "ch-0001"}]
    story_rows = [{"id": "st-0001", "chains": ["ch-0001"]},
                  {"id": "st-0002", "chains": ["ch-0002"]}]
    point_rows = [_pt(f"pt-{i:04d}", "st-0001") for i in range(1, 13)]   # 12 点 → 10+2 两批
    point_rows += [_pt("pt-0101", "st-0002")]
    targets = plan_case_targets(chain_rows, story_rows, point_rows,
                                {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001", "st-0002"}})
    assert [t["chain"] for t in targets] == ["ch-0001", "ch-0002"]
    assert [b["id"] for b in targets[0]["batches"]] == ["ch-0001-b1", "ch-0001-b2"]
    assert len(targets[0]["batches"][0]["points"]) == 10
    assert len(targets[0]["batches"][1]["points"]) == 2
    assert targets[1]["batches"][0]["points"] == ["pt-0101"]


def test_chain_without_points_gets_no_batches():
    targets = plan_case_targets([{"id": "ch-0001"}], [{"id": "st-0001", "chains": ["ch-0001"]}],
                                [], {"chains": {"ch-0001"}, "stories": {"st-0001"}})
    assert targets[0]["batches"] == []


def test_denominator_points_empty_scope_needs_story_table_else_fail_closed():
    """「范围空 = 全量」要靠 story 表分支才成立；story_rows 缺席的降级分支只会 fail-closed 归空。

    两句都走降级分支（chain 参数不参与筛选）：第一句 scope 没给 stories ⇒ 空分母；
    第二句只因 scope 给了 stories 才把范围内的两点全放行。真正的空 scope 全量行为在
    `test_plan_case_targets_story_chain_membership_decides_ownership`（带 story 表、scope 为 {}）。
    """
    rows = [_pt("pt-0001", "st-0001"), _pt("pt-0002", "st-0009")]
    assert denominator_points(rows, {}, "ch-0001") == []      # 链路归属由 story 表反查，见下一条
    got = denominator_points(rows, {"stories": {"st-0001", "st-0009"}}, "ch-0001")
    assert {p["id"] for p in got} == {"pt-0001", "pt-0002"}


def test_plan_case_targets_story_chain_membership_decides_ownership():
    # st-0001 属 ch-0001，st-0002 属 ch-0002；点只算给它所属故事的链路
    targets = plan_case_targets([{"id": "ch-0001"}, {"id": "ch-0002"}],
                                [{"id": "st-0001", "chains": ["ch-0001"]},
                                 {"id": "st-0002", "chains": ["ch-0001", "ch-0002"]}],
                                [_pt("pt-0001", "st-0001"), _pt("pt-0002", "st-0002")],
                                {})
    by_chain = {t["chain"]: [p for b in t["batches"] for p in b["points"]] for t in targets}
    assert by_chain == {"ch-0001": ["pt-0001", "pt-0002"], "ch-0002": ["pt-0002"]}


def test_duplicate_point_rows_dedup_and_last_row_wins():
    """mixed 的分母取 `_rows_of(POINT)`（KB 存量在前、本 run 草稿在后）：同 id 两行必须并一行、取后者。

    不去重会把同一点切进两个批次 ⇒ 末门「未落实点」虚增（假漏测）；取错行会拿回写前的旧内容当分母。
    """
    rows = [{"id": "pt-0001", "story": "st-0001", "name": "旧名"},
            {"id": "pt-0001", "story": "st-0001", "name": "新名"}]
    stories = [{"id": "st-0001", "chains": ["ch-0001"]}]
    got = denominator_points(rows, {}, "ch-0001", stories)
    assert [p["id"] for p in got] == ["pt-0001"] and got[0]["name"] == "新名"
    targets = plan_case_targets([{"id": "ch-0001"}], stories, rows, {})
    assert [b["points"] for b in targets[0]["batches"]] == [["pt-0001"]]


def _case(cid, covers, *, expected=None, pre="账号已登录", steps=("提交下单",), title="用例"):
    return CaseDraft(case_id=cid, title=title, covers=list(covers), preconditions=pre,
                     steps=list(steps), expected=list(expected or ["订单金额按券面规则抵扣"]))


def test_run_case_checks_clean_when_every_point_claimed():
    points = [{"id": "pt-0001", "name": "甲"}, {"id": "pt-0002", "name": "乙"}]
    cases = [_case("cc-0001", ["pt-0001", "pt-0002"])]     # 多点合一条：合法（裁定 38）
    out = run_case_checks(points, cases)
    assert out["hard"] == []
    assert {f["point"] for f in out["fulfillment"]} == {"pt-0001", "pt-0002"}
    assert out["fulfillment"][0]["owners"] == ["cc-0001"]


def test_uncovered_point_is_hard_and_names_the_point():
    out = run_case_checks([{"id": "pt-0001", "name": "甲"}, {"id": "pt-0002", "name": "乙"}],
                          [_case("cc-0001", ["pt-0001"])])
    codes = [(f["code"], f["where"]) for f in out["hard"]]
    assert codes == [("uncovered_point", "pt-0002")]


def test_phantom_cover_is_hard_even_when_all_points_claimed():
    out = run_case_checks([{"id": "pt-0001", "name": "甲"}],
                          [_case("cc-0001", ["pt-0001"]), _case("cc-0002", ["pt-9999"])])
    assert ("phantom_cover", "cc-0002") in [(f["code"], f["where"]) for f in out["hard"]]


def test_case_count_is_never_a_criterion():
    """假指标防线：用例数 ≥ 点数不得判通过——未认领点必须仍为 hard。"""
    points = [{"id": "pt-0001", "name": "甲"}]
    cases = [_case(f"cc-000{i}", ["pt-0002"]) for i in (1, 2, 3)]   # 三条用例、全认领不存在的点
    out = run_case_checks(points, cases)
    assert {f["code"] for f in out["hard"]} == {"uncovered_point", "phantom_cover"}


def test_hard_codes_are_exactly_two():
    from aitester.case_design.writing import _HARD_CODES
    assert _HARD_CODES == ("uncovered_point", "phantom_cover")


def test_report_counts_only_advisory_marks_never_hard():
    """含糊断言与环境存量前置：只呈递，不进 hard（裁定 37），且点数与内容对得上。"""
    points = [{"id": "pt-0001", "name": "甲"}]
    cases = [_case("cc-0001", ["pt-0001"], expected=["下单正常"],
                   pre="账号里已有历史订单", steps=("提交下单",)),
             _case("cc-0002", ["pt-0001"], expected=["订单金额按券面规则抵扣"])]
    out = run_case_checks(points, cases)
    assert out["hard"] == []
    rep = out["report"]
    assert rep["vague_assertions"] == 1 and rep["env_preconditions"] == 1
    assert rep["cases_total"] == 2
    assert any("含糊断言" in item["detail"] for item in rep["notes"])
    assert any("环境存量前置" in item["detail"] for item in rep["notes"])


def _delivery_fixture():
    points = [{"id": "pt-0001", "name": "甲"}, {"id": "pt-0002", "name": "乙"}]
    cases = [_case("cc-0001", ["pt-0001"], expected=["下单正常"]),
             _case("cc-0002", ["pt-0001"])]
    checks = run_case_checks(points, cases)
    ledger_data = {"writing": {"status": "awaiting_review", "targets": [
        {"chain": "ch-0001", "batches": [{"id": "ch-0001-b1", "points": ["pt-0001", "pt-0002"]}]}],
        "gate": {"round": 0}, "stale_batches": ["ch-0002-b1"], "note": "设计侧回写完成 @t"}}
    extras = {"uncovered": ["pt-0002"], "notes": checks["report"]["notes"],
              "dispositions": [{"ref": "op-01", "chain": "ch-0001", "case": "cc-0001",
                                "kind": "颗粒度", "ask": "拆成两步", "status": "已销账",
                                "note": "已拆"}],
              "unresolved": [{"chain": "ch-0001", "ref": "op-02", "ask": "补负向",
                              "cause": "评审分歧", "note": "评审与本块分歧"}],
              "cases_by_chain": {"ch-0001": cases}}
    return compose_case_delivery(ledger_data, ledger_data["writing"]["targets"],
                                 {"ch-0001": checks}, extras)


def test_delivery_has_all_required_sections_in_order():
    text = _delivery_fixture()
    order = ["## 前置判定", "## 用例清单", "## 履约差异表", "## 规范校验表（呈递项）",
             "## 未消化项（含不收敛归因）", "## 失效待重算批次",
             "## 意见落点对照表（每条意见的去向）"]
    pos = [text.index(h) for h in order]
    assert pos == sorted(pos), "末门可视对象的段落顺序必须固定"


def test_delivery_shows_measured_counts_not_ai_verdict():
    """裁定 36 的落地：门上必须摆实测计数（人在这里看到的「未落实点」是数出来的）。"""
    text = _delivery_fixture()
    assert "实测" in text
    assert "pt-0002" in text                       # 未落实的点必须逐条点名，不许只给数


def test_delivery_marks_stale_batches_and_note():
    text = _delivery_fixture()
    assert "ch-0002-b1" in text and "失效待重算" in text
    assert "设计侧回写完成" in text


def test_delivery_lists_advisory_notes_as_presented_items():
    text = _delivery_fixture()
    assert "含糊断言" in text and "cc-0001" in text
    # 呈递行不许把类别冠两遍：detail 自带尾注，renderer 不再重复 `（kind）`
    assert next(line for line in text.splitlines() if "含糊断言" in line).count("含糊断言") == 1


def test_shared_point_counted_once_in_measured_line():
    """一点挂两条链路（裁定 2 的重复归属）：实测计数走全局去重表，不得按链路累加翻倍。"""
    checks = run_case_checks([{"id": "pt-0001", "name": "共享点"}], [])
    targets = [{"chain": "ch-0001", "batches": [{"id": "ch-0001-b1", "points": ["pt-0001"]}]},
               {"chain": "ch-0002", "batches": [{"id": "ch-0002-b1", "points": ["pt-0001"]}]}]
    text = compose_case_delivery({"writing": {"targets": targets, "gate": {},
                                              "stale_batches": [], "note": ""}},
                                 targets, {"ch-0001": checks, "ch-0002": checks},
                                 {"uncovered": ["pt-0001"], "notes": [], "dispositions": [],
                                  "unresolved": [], "cases_by_chain": {}})
    assert "未落实点 1 个" in text and "未落实点 2 个" not in text
    assert text.count("pt-0001") >= 2          # 两条链路各自可见（呈递不缩水），但计数只算一次


# ---- R-59（T25 追加子项）：分母与写库侧同源，同 id 取「将被写库的那一份」 ----

def test_denominator_takes_the_row_writeback_would_write():
    """R-59：同一个 pt- id 出现在两个点层草稿块时，分母必须取**先出现**的那一条——
    与写库侧 `_collect_writeback_items` 的 `seen` 首见即留同源。旧 last-row-wins 会取后块，
    于是履约表按一份从未进过库的场景算覆盖。"""
    rows = [{"id": "pt-0001", "story": "st-0001", "name": "存量点", "state": "存量"},
            {"id": "pt-0001", "story": "st-0001", "name": "前块草稿点", "op": "upsert",
             "state": "更新"},
            {"id": "pt-0001", "story": "st-0001", "name": "后块草稿点", "op": "upsert",
             "state": "更新"},
            {"id": "pt-0002", "story": "st-0001", "name": "另一点", "op": "upsert",
             "state": "更新"}]
    stories = [{"id": "st-0001", "chains": ["ch-0001"]}]
    got = denominator_points(rows, {"stories": {"st-0001"}}, "ch-0001", stories)
    assert [p["name"] for p in got] == ["前块草稿点", "另一点"]
    assert all(p["name"] != "存量点" for p in got)         # 有草稿就不呈存量那份


def test_denominator_keeps_stock_row_when_no_draft_exists():
    """R-59 的另一半：只有存量行时分母照旧要有它（否则真漏测被算成界外点）。"""
    rows = [{"id": "pt-0001", "story": "st-0001", "name": "存量点", "state": "存量"}]
    got = denominator_points(rows, {"stories": {"st-0001"}}, "ch-0001",
                             [{"id": "st-0001", "chains": ["ch-0001"]}])
    assert [p["name"] for p in got] == ["存量点"]
