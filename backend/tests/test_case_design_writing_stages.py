# backend/tests/test_case_design_writing_stages.py
"""第四层编写环的阶段处理器与转场（挂具复用 test_case_design_driver.py，零联网零真模型）。"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage

from aitester.case_design.constants import CASE_BATCH_CAP
from aitester.case_design.ledger import Ledger
from aitester.case_design.stages import _writing_enabled

from test_case_design_driver import (  # 复用既有挂具，不抄第二份
    ScriptTask, StubKb, _drive, _end_text, _env, _write, drain,
)


def _kb_with_three_layers(tmp_path: Path) -> StubKb:
    """三层都已维护（点层带完整标记），编写环前置成立。"""
    return StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "name": "下单链路", "business_scope": "下单", "priority": "P1"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"], "actor": "客户",
                   "trigger": "提交下单", "expected": "下单成功", "name": "正常下单",
                   "priority": "P1"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001", "name": "券抵扣",
                   "scenario": "用满足门槛的券下单", "entities": ["订单", "优惠券"],
                   "directions": ["正向"], "priority": "P1"}],
    }, root=tmp_path / "kb")


_CASE_ONLY_PLAN = {"task_kind": "case_only", "entry_layer": "chain", "terminal_layer": "point",
                   "target_subtree": "", "source_files": [], "note": "给下单链路生成用例"}


def _led(env):
    led = Ledger.load(env.design)
    assert led is not None, "账本没落盘"
    return led


def test_writing_enabled_only_for_case_kinds():
    def ctx_of(kind):
        led = Ledger.fresh(Path("unused"))          # 不落盘，只喂一个 task 节
        led.data["task"] = {"descriptor": {"task_kind": kind}}
        return type("_C", (), {"led": led})()

    for kind, expected in (("case_only", True), ("mixed", True), ("design", False)):
        assert _writing_enabled(ctx_of(kind)) is expected      # 只吃账本事实，不猜


def test_case_only_skips_three_layers_and_opens_first_batch(tmp_path):
    """case_only：三层一律 skipped（只读上下文），开账即物化首批并下发第一批生成指令。"""
    kb = _kb_with_three_layers(tmp_path)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    frames: list[dict] = []
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}

    turn = _drive(env, state, ScriptTask(), writer=frames.append)

    assert turn["case"]["route"] == "agent"                   # 下发指令，不空转
    led = _led(env)
    assert {v["state"] for v in led.data["layers"].values()} == {"skipped"}
    assert (led.cursor["stage"], led.cursor["layer"], led.cursor["block"]) \
        == ("case_gen", "ch-0001", "ch-0001-b1")
    writing = led.data["writing"]
    assert writing["status"] == "active"
    assert writing["targets"] == [{"chain": "ch-0001",
                                   "batches": [{"id": "ch-0001-b1", "points": ["pt-0001"]}]}]
    assert writing["batches"] == [{"id": "ch-0001-b1", "chain": "ch-0001",
                                   "state": "todo", "round": 0}]
    manifest = json.loads((env.manifests_dir / "case-ch-0001-b1.json")
                          .read_text(encoding="utf-8"))
    assert manifest["cases_cap"] == CASE_BATCH_CAP
    assert manifest["points"][0]["scenario"] == "用满足门槛的券下单"
    assert manifest["points"][0]["trigger"] == "提交下单"       # 故事上下文随清单到位
    assert manifest["points"][0]["actor"] == "客户"
    text = turn["messages"][-1].content
    assert "design/cases/ch-0001-b1.json" in text and "pt-0001" in text
    assert kb.upserts == [] and kb.deletes == []               # 裁定 35：第四层零 KB 写


def test_design_task_never_enters_writing(tmp_path):
    """design 任务：照旧走设计环，编写环节一个字节都不许动（新增缝只对 case 种类生效）。"""
    kb = _kb_with_three_layers(tmp_path)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json",
           {**_CASE_ONLY_PLAN, "task_kind": "design", "entry_layer": "point",
            "terminal_layer": "point"})        # 点层已维护 → update，设计环正常开跑
    state = {"messages": [HumanMessage(content="只做测试设计")], "case": {}}
    _drive(env, state, ScriptTask(), writer=lambda e: None)
    led = _led(env)
    assert led.cursor["stage"] == "gen"                      # 设计侧生成阶段，不是编写环
    assert led.data["writing"]["status"] == ""
    assert led.data["writing"]["targets"] == []
    assert led.layer("chain")["state"] == "skipped" and led.layer("point")["state"] == "active"


# ---- Step 2：前置不满足时明示边界 ----

def test_case_only_stops_at_boundary_when_layers_missing(tmp_path):
    """三层没维护 ⇒ 明示边界停在设计侧，零生成零写入，不 halted（裁定 40：合法业务状态，不是故障）。"""
    kb = StubKb(layers={"chain": [], "story": [], "point": []}, root=tmp_path / "kb")
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)

    state = drain(env, kb, ScriptTask())                 # 边界即终局：route=end，一次收敛
    assert "用例任务未开工" in _end_text(state["frames"])
    led = _led(env)
    assert led.status == "done"
    assert led.data["writing"]["targets"] == [] and led.data["writing"]["batches"] == []
    assert led.data["writing"]["note"]                    # 拒因入账，交付物侧看得见
    assert list(env.cases_dir().glob("*.json")) == []
    assert kb.upserts == [] and kb.deletes == []          # 裁定 35：第四层零 KB 写


def test_case_only_blocks_on_stale_layer(tmp_path):
    """有失效待重算的层 ⇒ 同样明示边界（先重做设计，再写用例）。

    事实源用 `carried_stale`（`_boot` 归档时留下、跨任务持久）而不是 `layers.*.state`：
    后者会被 `init_task` 按 modes 覆写成 skipped，边界判定若读它就永远读不到。
    """
    kb = _kb_with_three_layers(tmp_path)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    led = Ledger.fresh(env.design)
    led.data["carried_stale"] = ["story"]
    led.save()

    frames: list[dict] = []
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    turn = _drive(env, state, ScriptTask(), writer=frames.append)
    assert turn["case"]["route"] == "end"
    assert "失效待重算" in _end_text(frames)
    assert _led(env).data["writing"]["targets"] == []


def test_case_only_blocks_when_no_points_in_scope(tmp_path):
    """三层齐备但范围内点数为 0 ⇒ 分母为空，明示边界（不许产「零用例也算完成」的交付物）。"""
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "name": "下单链路", "business_scope": "下单", "priority": "P1"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"], "actor": "客户",
                  "trigger": "提交下单", "expected": "下单成功", "name": "正常下单",
                  "priority": "P1"}],
        "point": [],
    }, root=tmp_path / "kb")
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    state = drain(env, kb, ScriptTask())
    assert "没有" in _end_text(state["frames"]) and "测试点" in _end_text(state["frames"])
    led = _led(env)
    assert led.status == "done" and led.data["writing"]["batches"] == []


def test_case_only_blocks_on_zero_denominator_with_maintained_layers(tmp_path):
    """执行点补钉：三层 probe 全 maintained、唯独范围内链路 0 点 ⇒ 走「分母点数 0」拒因，
    不是「层未维护」拒因——门禁「零点数绿过」正卡在这条分支上。"""
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "name": "下单链路", "business_scope": "下单", "priority": "P1"},
                  {"id": "ch-0002", "type": "chain", "level": 1, "parent": "",
                   "name": "退款链路", "business_scope": "退款", "priority": "P1"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"], "actor": "客户",
                   "trigger": "提交下单", "expected": "下单成功", "name": "正常下单",
                   "priority": "P1"},
                  {"id": "st-0002", "type": "story", "chains": ["ch-0002"], "actor": "客户",
                   "trigger": "申请退款", "expected": "退款受理", "name": "退款受理",
                   "priority": "P1"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0002", "name": "超额退款",
                   "scenario": "退款金额超过订单金额", "entities": ["退款单"],
                   "directions": ["负向"], "priority": "P1"}],
    }, root=tmp_path / "kb")
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json",
           {**_CASE_ONLY_PLAN, "target_subtree": "ch-0001"})
    state = drain(env, kb, ScriptTask())
    end = _end_text(state["frames"])
    assert "范围内没有任何测试点可作分母" in end          # 分母 0 拒因的执行点
    assert "缺少" not in end                              # 不是「层未维护」那条出口
    led = _led(env)
    assert led.status == "done"                           # 明示边界，不 halted
    assert led.data["writing"]["targets"] == [] and led.data["writing"]["batches"] == []
    assert led.data["writing"]["note"] == (
        "本次范围内没有任何测试点可作分母（链路/故事/点齐备但点数为 0），用例任务无从开工")
    assert list(env.cases_dir().glob("*.json")) == []
    assert kb.upserts == [] and kb.deletes == []
