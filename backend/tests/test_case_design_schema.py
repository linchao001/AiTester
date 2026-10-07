import json
from pathlib import Path

import pytest

from aitester.case_design.constants import (
    CHAIN, LAYER_BUCKET, LAYERS, NODE_BUCKETS, POINT, STORY, TYPE_PREFIX,
)
from aitester.case_design.env import CaseDesignEnv
from aitester.case_design.ledger import Ledger
from aitester.case_design.schema import (
    DraftNode, MatrixOut, Opinion, ReviewOut, parse_case_file, parse_json_fence,
    validate_cases, validate_drafts,
)


def test_constants_are_single_source():
    assert LAYERS == (CHAIN, STORY, POINT)
    assert TYPE_PREFIX == {CHAIN: "ch", STORY: "st", POINT: "pt"}
    assert NODE_BUCKETS == ("business/chains", "business/stories", "business/test_points")
    assert LAYER_BUCKET[CHAIN] == "business/chains"


def test_env_paths(tmp_path: Path):
    env = CaseDesignEnv(project_dir=str(tmp_path), kb=None)
    assert env.design == tmp_path / "design"
    assert env.drafts_dir(CHAIN) == tmp_path / "design" / "drafts" / "chain"
    assert env.reviews_dir == tmp_path / "design" / "reviews"


def test_parse_json_fence_accepts_single_block():
    text = '前言\n```json\n{"a": 1}\n```\n后记'
    assert parse_json_fence(text) == {"a": 1}


def test_parse_json_fence_rejects_missing_or_multiple():
    with pytest.raises(ValueError):
        parse_json_fence("没有围栏")
    with pytest.raises(ValueError):
        parse_json_fence("```json\n{}\n```\n```json\n{}\n```")


def test_validate_drafts_chain_ok_and_errors():
    raw = {
        "layer": "chain", "block": "ALL",
        "nodes": [{"op": "upsert", "type": "chain", "name": "下单链路",
                   "level": 2, "parent": "ch-0001", "business_scope": "下单主流程",
                   "excluded": "支付失败回滚"}],
    }
    nodes, errors = validate_drafts("chain", raw)
    assert errors == [] and nodes[0].name == "下单链路" and nodes[0].id == ""

    nodes, errors = validate_drafts("chain", {"layer": "chain", "block": "ALL",
                                              "nodes": [{"op": "upsert", "type": "chain", "name": "  "}]})
    assert nodes == [] and any("name" in e for e in errors)


def test_validate_drafts_point_requires_entities_and_direction():
    base = {"op": "upsert", "type": "point", "id": "pt-0001", "name": "下单成功",
            "story": "st-0001", "scenario": "已登录且库存充足时提交订单"}
    nodes, errors = validate_drafts("point", {"layer": "point", "block": "st-0001", "nodes": [base]})
    assert any("entities" in e for e in errors) and any("directions" in e for e in errors)

    ok = {**base, "entities": ["订单"], "directions": ["正向"]}
    nodes, errors = validate_drafts("point", {"layer": "point", "block": "st-0001", "nodes": [ok]})
    assert errors == [] and nodes[0].priority == "P1"


def test_validate_drafts_delete_and_id_shape():
    nodes, errors = validate_drafts("story", {"layer": "story", "block": "ch-0001", "nodes": [
        {"op": "delete", "type": "story", "id": "st-0009", "reason": "与 st-0003 合并"}]})
    assert errors == [] and nodes[0].op == "delete"

    _, errors = validate_drafts("story", {"layer": "story", "block": "ch-0001", "nodes": [
        {"op": "upsert", "type": "story", "id": "故事1", "name": "x", "chains": ["ch-0001"],
         "actor": "用户", "trigger": "点击", "expected": "成功"}]})
    assert any("id" in e for e in errors)


def _valid_story(**over):
    node = {"op": "upsert", "type": "story", "id": "st-0001", "name": "示例故事",
            "chains": ["ch-0001"], "actor": "角色", "trigger": "触发", "expected": "成功"}
    node.update(over)
    return node


def _valid_chain(**over):
    node = {"op": "upsert", "type": "chain", "id": "ch-0001", "name": "示例链路",
            "level": 1, "parent": "", "business_scope": "范围"}
    node.update(over)
    return node


def test_priority_validation_covers_chain_and_story():
    # I-3：priority 合法性此前只在点层校验，chain/story 任意非法值静默通过；
    # 进门后 `PRIORITY_RANK.get(_priority(v), 1)` 把非法值当 P1 参与 hard 判据。
    _, errors = validate_drafts("story", {"layer": "story", "block": "b",
                                          "nodes": [_valid_story(priority="P9")]})
    assert any("priority" in e for e in errors)
    _, errors = validate_drafts("chain", {"layer": "chain", "block": "ALL",
                                          "nodes": [_valid_chain(priority="高")]})
    assert any("priority" in e for e in errors)


def test_priority_default_and_valid_values_pass_all_layers():
    # DraftNode.priority 缺省 "P1" ⇒「不写」仍合法；P0/P1/P2 三层同源合法。
    _, errors = validate_drafts("story", {"layer": "story", "block": "b", "nodes": [_valid_story()]})
    assert errors == []
    nodes, errors = validate_drafts("chain", {"layer": "chain", "block": "ALL",
                                              "nodes": [_valid_chain(priority="P0")]})
    assert errors == [] and nodes[0].priority == "P0"


def test_priority_check_lives_in_common_upsert_branch_not_point_only():
    # 上移到 _check_common 的 upsert 分支后，三层同一条非法值都拒；delete 无 priority 语义不受影响。
    for layer, factory in (("chain", _valid_chain), ("story", _valid_story)):
        _, errors = validate_drafts(layer, {"layer": layer, "block": "b",
                                            "nodes": [factory(priority="P3")]})
        assert any("priority" in e for e in errors), layer


def test_duplicate_id_within_one_draft_file_is_rejected():
    # A-M4：同一 chain 文件两条 id="ch-0001" 的不同 upsert 旧实现 errors==[]、返回 2 节点；
    # 而宇宙侧 checks.py 后者覆盖、大纲侧首条呈递——同一破数据两种静默读法。本文件内重复必须拒。
    raw = {"layer": "chain", "block": "ALL", "nodes": [
        {"op": "upsert", "type": "chain", "id": "ch-0001", "name": "甲",
         "level": 1, "parent": "", "business_scope": "范围"},
        {"op": "upsert", "type": "chain", "id": "ch-0001", "name": "乙",
         "level": 1, "parent": "", "business_scope": "范围"}]}
    nodes, errors = validate_drafts("chain", raw)
    assert nodes == []                                   # 有错 ⇒ 不返回节点表
    assert any("重复" in e and "ch-0001" in e for e in errors)


def test_duplicate_id_across_upsert_and_delete_is_rejected():
    # delete 与 upsert 同表：本文件内 upsert 与 delete 撞同一 id 也拒。
    raw = {"layer": "story", "block": "ch-0001", "nodes": [
        {"op": "upsert", "type": "story", "id": "st-0001", "name": "甲", "chains": ["ch-0001"],
         "actor": "角色", "trigger": "触发", "expected": "成功"},
        {"op": "delete", "type": "story", "id": "st-0001", "reason": "下线"}]}
    _, errors = validate_drafts("story", raw)
    assert any("重复" in e for e in errors)


def test_blank_and_distinct_ids_within_file_still_pass():
    # 护栏：新增节点 id 留空（驱动后分配）不得被当重复；同文件互不相同 id 合法。
    blank = {"layer": "chain", "block": "ALL", "nodes": [
        {"op": "upsert", "type": "chain", "name": "甲", "level": 1, "parent": "", "business_scope": "范围"},
        {"op": "upsert", "type": "chain", "name": "乙", "level": 1, "parent": "", "business_scope": "范围"}]}
    nodes, errors = validate_drafts("chain", blank)
    assert errors == [] and len(nodes) == 2
    distinct = {"layer": "chain", "block": "ALL", "nodes": [
        {"op": "upsert", "type": "chain", "id": "ch-0001", "name": "甲",
         "level": 1, "parent": "", "business_scope": "范围"},
        {"op": "upsert", "type": "chain", "id": "ch-0002", "name": "乙",
         "level": 1, "parent": "", "business_scope": "范围"}]}
    nodes, errors = validate_drafts("chain", distinct)
    assert errors == [] and len(nodes) == 2


def test_ledger_status_rejects_illegal_value_with_valueerror(tmp_path: Path):
    # A-M6：状态枚举校验此前用 `assert`（`-O` 下静默失效）。改 ValueError 后必须响亮失败且不受 -O 影响。
    led = Ledger.fresh(tmp_path)
    with pytest.raises(ValueError):
        led.status = "not_a_real_status"
    led.status = "awaiting_review"                     # 合法值照常写入
    assert led.data["status"] == "awaiting_review"


def test_next_seq_fails_loudly_at_four_digit_ceiling(tmp_path: Path):
    # A-M7：计数过 9999 会产 ch-10000，而 ID_RE 只认四位 ⇒ 模型回填必被判非法、nudge 烧尽后 halted
    # 且无法合法修复。改为到上限即抛错说明需扩位/清账本，不静默产畸形 id。
    led = Ledger.fresh(tmp_path)
    led.data["counters"][CHAIN] = 9998
    assert led.next_seq(CHAIN) == "ch-9999"             # 最后一个合法四位 id
    with pytest.raises(ValueError):
        led.next_seq(CHAIN)                              # 越界必须响亮失败
    assert led.data["counters"][CHAIN] == 9999           # 失败不推进计数，不留脏状态


def test_env_ensure_dirs_uses_layers_single_source(tmp_path: Path, monkeypatch):
    # §9-3：env.py:36 硬编码 ("chain","story","point") ⇒ 违反 constants 单点纪律，改读 LAYERS。
    # 改前模块内无 LAYERS 名，setattr 直接失败（红）；改后 ensure_dirs 随该单点增长。
    from aitester.case_design import env as env_mod
    monkeypatch.setattr(env_mod, "LAYERS", ("chain", "story", "point", "extra_layer"))
    env_mod.CaseDesignEnv(project_dir=str(tmp_path), kb=None).ensure_dirs()
    assert (tmp_path / "design" / "drafts" / "extra_layer").is_dir()


def test_review_and_matrix_models():
    out = ReviewOut.model_validate({
        "opinions": [{"target": {"type": "seam", "value": "st-0001,st-0002"},
                      "kind": "边界归属", "ask": "明确谁认领取消阶段", "evidence": "drafts/story/ch-0001.json"}],
        "resolutions": [{"ref": "op-01", "resolved": True, "note": "已补"}],
    })
    assert isinstance(out.opinions[0], Opinion) and out.opinions[0].target.value == "st-0001,st-0002"

    m = MatrixOut.model_validate({"cells": [{"entity": "订单", "story": "st-0002",
                                             "verdict": "not_needed", "reason": "该故事不做订单实体"}]})
    assert m.cells[0]["verdict"] == "not_needed"


def test_ledger_roundtrip_and_id_allocation(tmp_path: Path):
    env = CaseDesignEnv(project_dir=str(tmp_path), kb=None)
    env.design.mkdir(parents=True)
    led = Ledger.fresh(env.design)
    led.save()
    assert Ledger.load(env.design) is not None
    assert led.next_seq(CHAIN) == "ch-0001"
    assert led.next_seq(CHAIN) == "ch-0002"
    assert led.next_seq(POINT) == "pt-0001"
    led.save()
    assert json.loads((env.design / "ledger.json").read_text(encoding="utf-8"))["counters"]["chain"] == 2


# ---- 第四层用例草稿（裁定 35/38）：cc- 形状 + covers 认领纪律 ----

def _case_raw(**over):
    base = {"chain": "ch-0002", "batch": "ch-0002-b1", "cases": [{
        "case_id": "cc-0001", "title": "下单用满足门槛的券可抵扣",
        "covers": ["pt-0003"], "preconditions": "账号内有一张满足门槛的优惠券",
        "steps": ["登录并进入下单页", "选择该券并提交下单"],
        "expected": ["订单金额按券面规则抵扣", "该券状态变为已核销"],
        "priority": "P1", "note": ""}]}
    base.update(over)
    return base


def test_valid_case_file_parses(tmp_path):
    path = tmp_path / "ch-0002-b1.json"
    path.write_text(json.dumps(_case_raw(), ensure_ascii=False), encoding="utf-8")
    cases, errors = parse_case_file(path)
    assert errors == []
    assert cases[0].covers == ["pt-0003"] and cases[0].case_id == "cc-0001"


def test_case_without_covers_is_rejected():
    _, errors = validate_cases(_case_raw(cases=[{
        "case_id": "cc-0001", "title": "谁都不落实", "covers": [],
        "preconditions": "x", "steps": ["y"], "expected": ["z"]}]))
    assert any("covers 不能为空" in e for e in errors)


def test_case_covering_non_point_is_rejected():
    _, errors = validate_cases(_case_raw(cases=[{
        "case_id": "cc-0001", "title": "认领了故事", "covers": ["st-0001"],
        "preconditions": "x", "steps": ["y"], "expected": ["z"]}]))
    assert any("非测试点 id" in e for e in errors)


def test_case_bad_case_id_shape_is_rejected():
    _, errors = validate_cases(_case_raw(cases=[{
        "case_id": "pt-0001", "title": "借用三层 id", "covers": ["pt-0003"],
        "preconditions": "x", "steps": ["y"], "expected": ["z"]}]))
    assert any("cc-四位数字" in e for e in errors)


def test_case_id_duplicated_in_same_file_is_rejected():
    one = {"case_id": "cc-0001", "title": "甲", "covers": ["pt-0003"], "preconditions": "x",
           "steps": ["y"], "expected": ["z"]}
    two = dict(one, title="乙", covers=["pt-0004"])
    _, errors = validate_cases(_case_raw(cases=[one, two]))
    assert any("本文件内重复" in e for e in errors)


def test_empty_cases_array_is_rejected_not_empty_batch():
    _, errors = validate_cases(_case_raw(cases=[]))
    assert any("cases 必须是非空数组" in e for e in errors)


def test_new_case_with_blank_id_is_accepted_for_driver_patch():
    one = {"case_id": "", "title": "待分配", "covers": ["pt-0003"], "preconditions": "x",
           "steps": ["y"], "expected": ["z"]}
    cases, errors = validate_cases(_case_raw(cases=[one]))
    assert errors == [] and cases[0].case_id == ""


def test_missing_steps_or_expected_or_precondition_is_rejected():
    for field in ("preconditions", "steps", "expected"):
        bad = {"case_id": "cc-0001", "title": "缺字段", "covers": ["pt-0003"],
               "preconditions": "x", "steps": ["y"], "expected": ["z"]}
        bad[field] = "" if field == "preconditions" else []
        _, errors = validate_cases(_case_raw(cases=[bad]))
        assert errors, f"{field} 缺失必须拒收"
