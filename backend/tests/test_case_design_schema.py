import json
from pathlib import Path

import pytest

from aitester.case_design.constants import (
    CHAIN, LAYER_BUCKET, LAYERS, NODE_BUCKETS, POINT, STORY, TYPE_PREFIX,
)
from aitester.case_design.env import CaseDesignEnv
from aitester.case_design.ledger import Ledger
from aitester.case_design.schema import (
    DraftNode, MatrixOut, Opinion, ReviewOut, parse_json_fence, validate_drafts,
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
