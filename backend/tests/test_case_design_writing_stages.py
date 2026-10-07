# backend/tests/test_case_design_writing_stages.py
"""第四层编写环的阶段处理器与转场（挂具复用 test_case_design_driver.py，零联网零真模型）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from aitester.case_design import stages
from aitester.case_design.constants import CASE_BATCH_CAP, LAYER_CN, ROUND_CAP
from aitester.case_design.instructions import case_attribute_instruction
from aitester.case_design.ledger import Ledger
from aitester.case_design.schema import ReviewOut
from aitester.case_design.stages import _CAUSES, _writing_enabled

from test_case_design_driver import (  # 复用既有挂具，不抄第二份
    ScriptTask, StubKb, _append, _drive, _end_text, _env, _j, _write, drain,
)


def _three_layer_rows() -> dict:
    """三层齐备的点/故事/链路行（点层带完整标记）——编写环前置成立的世界状态。"""
    return {
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "name": "下单链路", "business_scope": "下单", "priority": "P1"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"], "actor": "客户",
                   "trigger": "提交下单", "expected": "下单成功", "name": "正常下单",
                   "priority": "P1"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001", "name": "券抵扣",
                   "scenario": "用满足门槛的券下单", "entities": ["订单", "优惠券"],
                   "directions": ["正向"], "priority": "P1"}],
    }


def _kb_with_three_layers(tmp_path: Path) -> StubKb:
    """三层都已维护（点层带完整标记），编写环前置成立。"""
    return StubKb(layers=_three_layer_rows(), root=tmp_path / "kb")


_CASE_ONLY_PLAN = {"task_kind": "case_only", "entry_layer": "chain", "terminal_layer": "point",
                   "target_subtree": "", "source_files": [], "note": "给下单链路生成用例"}

_MIXED_PLAN = {"task_kind": "mixed", "entry_layer": "chain", "terminal_layer": "point",
               "target_subtree": "", "source_files": [], "note": "先设计再写用例"}


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
    frames: list[dict] = []
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}

    turn = _drive(env, state, ScriptTask(), writer=lambda e: None)   # h_plan 索要计划、落账本
    assert turn["case"]["route"] == "agent"
    _append(state, turn)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)                # 账本已在盘上，第二次驱动不归档
    turn = _drive(env, state, ScriptTask(), writer=frames.append)

    assert turn["case"]["route"] == "agent"                          # 下发指令，不空转
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
    assert manifest["points_cap"] == CASE_BATCH_CAP
    assert len(manifest["points"]) <= CASE_BATCH_CAP                 # 每批点数上限（非用例条数）
    assert manifest["points"][0]["scenario"] == "用满足门槛的券下单"
    assert manifest["points"][0]["trigger"] == "提交下单"             # 故事上下文随清单到位
    assert manifest["points"][0]["actor"] == "客户"
    text = turn["messages"][-1].content
    assert "design/cases/ch-0001-b1.json" in text and "pt-0001" in text
    assert kb.upserts == [] and kb.deletes == []                     # 裁定 35：第四层零 KB 写


def test_design_task_never_enters_writing(tmp_path):
    """design 任务：照旧走设计环，编写环节一个字节都不许动（新增缝只对 case 种类生效）。"""
    kb = _kb_with_three_layers(tmp_path)
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage(content="只做测试设计")], "case": {}}
    _append(state, _drive(env, state, ScriptTask(), writer=lambda e: None))   # h_plan 索要计划
    _write(env.design / "plan.json",
           {**_CASE_ONLY_PLAN, "task_kind": "design", "entry_layer": "point",
            "terminal_layer": "point"})        # 点层已维护 → update，设计环正常开跑
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

    state = drain(env, kb, ScriptTask(), plan=_CASE_ONLY_PLAN)   # 边界即终局：route=end，一次收敛
    assert "用例任务未开工" in _end_text(state["frames"])
    led = _led(env)
    assert led.status == "done"
    assert led.data["writing"]["targets"] == [] and led.data["writing"]["batches"] == []
    assert led.data["writing"]["note"]                    # 拒因入账，交付物侧看得见
    assert list(env.cases_dir().glob("*.json")) == []
    assert list(env.manifests_dir.glob("case-*.json")) == []   # M-3：拒因路径不留半张清单
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
    reason = LAYER_CN["story"] + "层标了失效待重算，请先跑一次测试设计任务把它们重做"
    assert _end_text(frames) == "用例任务未开工：" + reason + "。本轮不生成用例、不写知识库。"
    led = _led(env)
    assert led.data["writing"]["targets"] == []
    assert list(env.cases_dir().glob("*.json")) == []
    assert list(env.manifests_dir.glob("case-*.json")) == []   # M-3：拒因路径不留半张清单


def test_case_only_blocks_when_point_layer_unmaintained(tmp_path):
    """点层未维护 ⇒ 命中「知识库缺少测试点」拒因（I-1 后走活宇宙判定）。

    分母点数为 0 的真分支（三层齐备、圈内 0 点）由
    test_case_only_blocks_on_zero_denominator_with_maintained_layers 守着，不在此条。
    """
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "name": "下单链路", "business_scope": "下单", "priority": "P1"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"], "actor": "客户",
                  "trigger": "提交下单", "expected": "下单成功", "name": "正常下单",
                  "priority": "P1"}],
        "point": [],
    }, root=tmp_path / "kb")
    env = _env(tmp_path, kb)
    state = drain(env, kb, ScriptTask(), plan=_CASE_ONLY_PLAN)
    end = _end_text(state["frames"])
    assert end == ("用例任务未开工：知识库缺少测试点，用例没有可落实的设计分母，"
                   "请先跑一次测试设计任务。本轮不生成用例、不写知识库。")
    assert "范围内没有任何测试点可作分母" not in end      # 命中的是「缺少」而非「分母 0」出口
    led = _led(env)
    assert led.status == "done" and led.data["writing"]["batches"] == []
    assert list(env.cases_dir().glob("*.json")) == []
    assert list(env.manifests_dir.glob("case-*.json")) == []   # M-3：拒因路径不留半张清单


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
    state = drain(env, kb, ScriptTask(),
                  plan={**_CASE_ONLY_PLAN, "target_subtree": "ch-0001"})
    end = _end_text(state["frames"])
    assert "范围内没有任何测试点可作分母" in end          # 分母 0 拒因的执行点
    assert "缺少" not in end                              # 不是「层未维护」那条出口
    led = _led(env)
    assert led.status == "done"                           # 明示边界，不 halted
    assert led.data["writing"]["targets"] == [] and led.data["writing"]["batches"] == []
    assert led.data["writing"]["note"] == (
        "本次范围内没有任何测试点可作分母（链路/故事/点齐备但点数为 0），用例任务无从开工")
    assert list(env.cases_dir().glob("*.json")) == []
    assert list(env.manifests_dir.glob("case-*.json")) == []   # M-3：拒因路径不留半张清单
    assert kb.upserts == [] and kb.deletes == []


# ---- I-1：mixed 旗舰路径——回写后编写环必须开账（前置读活宇宙，非计划时快照）----

def _advance_to_design_ring(env):
    """mixed：首驱索要计划并落账本 → 落 mixed 计划 → 再驱开设计环（此时三层 KB 仍空，
    plan-time probe 冻结为全未维护）。返回累加了两次回合的 state。"""
    state = {"messages": [HumanMessage(content="先设计再写用例")], "case": {}}
    turn = _drive(env, state, ScriptTask(), writer=lambda e: None)
    assert turn["case"]["route"] == "agent"                  # h_plan 索要计划
    _append(state, turn)
    _write(env.design / "plan.json", _MIXED_PLAN)
    _append(state, _drive(env, state, ScriptTask(), writer=lambda e: None))  # 开设计环
    return state


def _stage_post_writeback_world(env, kb, *, point_rows):
    """把「设计侧已完成、已回写」的世界状态摆好：KB 三层现稿、账本层 done、编写环游标就位。"""
    rows = _three_layer_rows()
    rows["point"] = point_rows
    kb.layers = rows                                         # StubKb.layers 是普通可变 dict
    led = _led(env)
    for layer in ("chain", "story", "point"):
        led.layer(layer)["state"] = "done"
    led.data["writing"]["note"] = "设计侧回写完成 @fixture"
    led.data["cursor"] = {"stage": "case_plan", "layer": "", "block": "", "round": 0,
                          "source": "block", "nudge": 0, "asked": False}
    led.save()
    return led


def test_mixed_opens_writing_after_design_writeback(tmp_path):
    """mixed 回写后同轮开编写环：前置判定读活宇宙（回写后的现稿），不再被计划时空快照挡死。"""
    kb = StubKb(layers={"chain": [], "story": [], "point": []}, root=tmp_path / "kb")
    env = _env(tmp_path, kb)
    state = _advance_to_design_ring(env)
    _stage_post_writeback_world(env, kb, point_rows=_three_layer_rows()["point"])

    frames: list[dict] = []
    turn = _drive(env, state, ScriptTask(), writer=frames.append)

    assert turn["case"]["route"] == "agent"
    led = _led(env)
    assert (led.cursor["stage"], led.cursor["layer"], led.cursor["block"]) \
        == ("case_gen", "ch-0001", "ch-0001-b1")
    assert "用例任务未开工" not in _end_text(frames)
    assert led.data["writing"]["note"].startswith("设计侧回写完成")   # 成功路径不写 note


def test_mixed_refusal_keeps_writeback_trace_in_note(tmp_path):
    """mixed 回写后前置仍不满足（点层空）：拒因追加、不覆盖回写留痕，且零写入零 KB 触达。"""
    kb = StubKb(layers={"chain": [], "story": [], "point": []}, root=tmp_path / "kb")
    env = _env(tmp_path, kb)
    state = _advance_to_design_ring(env)
    _stage_post_writeback_world(env, kb, point_rows=[])          # 唯独点层清空 → 活宇宙点层未维护

    frames: list[dict] = []
    turn = _drive(env, state, ScriptTask(), writer=frames.append)

    assert turn["case"]["route"] == "end"
    end = _end_text(frames)
    assert "用例任务未开工" in end and "测试点" in end
    note = _led(env).data["writing"]["note"]
    assert note.startswith("设计侧回写完成")                       # 留痕不被拒因覆盖
    assert "；" in note and "知识库缺少" in note                   # 拒因用 ； 追加
    assert list(env.cases_dir().glob("*.json")) == []
    assert list(env.manifests_dir.glob("case-*.json")) == []
    assert kb.upserts == [] and kb.deletes == []


# ---- I-3：归档面带走编写环两件制品（批文件名不随任务 id 变，只能靠归档隔离）----

def test_archive_moves_case_surface_with_the_rest(tmp_path):
    """_archive 必须把 design/cases/*.json、design/case-delivery.md 连同 manifests 一起搬走，
    否则同项目第二个用例任务会把上一条残留当本轮产出、旧 case-delivery.md 冒充本轮交付。"""
    kb = _kb_with_three_layers(tmp_path)
    env = _env(tmp_path, kb)
    _write(env.cases_dir() / "ch-0001-b1.json",
           {"chain": "ch-0001", "batch": "ch-0001-b1", "cases": []})
    _write(env.delivery_path, {"note": "旧任务交付物"})
    _write(env.manifests_dir / "case-ch-0001-b1.json", {"batch": "ch-0001-b1"})

    stages._archive(env)

    assert not (env.cases_dir() / "ch-0001-b1.json").exists()   # 原址已不存在
    assert not env.delivery_path.exists()
    assert list(env.manifests_dir.glob("case-*.json")) == []    # manifests 一并搬走
    archive = env.design / "archive"
    assert len(list(archive.rglob("case-delivery.md"))) == 1
    assert len(list(archive.rglob("ch-0001-b1.json"))) == 1
    assert len(list(archive.rglob("case-ch-0001-b1.json"))) == 1


def test_second_task_does_not_see_prior_case_surface(tmp_path):
    """行为级：上一条 done 任务残留的 design/cases/ch-0001-b1.json 在新任务开账前被归档带走——
    本轮看不见旧用例正文（与直调版互补，钉住跨任务隔离）。"""
    kb = _kb_with_three_layers(tmp_path)
    env = _env(tmp_path, kb)
    led = Ledger.fresh(env.design)
    led.status = "done"
    led.save()
    _write(env.cases_dir() / "ch-0001-b1.json",
           {"chain": "ch-0001", "batch": "ch-0001-b1", "cases": [{"title": "旧任务用例"}]})

    frames: list[dict] = []
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    turn = _drive(env, state, ScriptTask(), writer=frames.append)

    assert (env.cases_dir() / "ch-0001-b1.json").exists() is False   # 被归档搬走
    assert turn["case"]["route"] == "agent"                           # 新任务向用户索要计划
    assert len(list((env.design / "archive").rglob("ch-0001-b1.json"))) == 1


# ---- T21 批环：收草稿、补号、评审、转下一批（裁定 36②/39）----

def _kb_with_many_points(tmp_path: Path, n: int = 11) -> StubKb:
    """一条链路 + 一个故事 + n 个测试点：n>CASE_BATCH_CAP ⇒ 天然切成两批。

    夹具一律留第二批（11 点 ⇒ 两批）：case_gate 要到 T22 才注册，最后一个批次收口会
    _go("case_gate") 撞 KeyError 收敛成 halted——本任务不许为此加防御分支。
    """
    return StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "name": "下单链路", "business_scope": "下单", "priority": "P1"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"], "actor": "客户",
                   "preconditions": "账号已注册", "trigger": "提交下单", "expected": "下单成功",
                   "name": "正常下单", "priority": "P1"}],
        "point": [{"id": f"pt-{i:04d}", "type": "point", "story": "st-0001",
                   "name": f"点{i:02d}", "scenario": f"场景{i:02d}", "entities": ["订单"],
                   "directions": ["正向"], "priority": "P1"} for i in range(1, n + 1)],
    }, root=tmp_path / "kb")


def _manifest_points(env, batch: str) -> list[str]:
    raw = json.loads((env.manifests_dir / f"case-{batch}.json").read_text(encoding="utf-8"))
    return [str(p["id"]) for p in raw["points"]]


def _cases_payload(chain: str, batch: str, points: list[str]) -> dict:
    """一条点一条用例（1:1 是合法比例之一，裁定 38）；id 留空串交给驱动补号。"""
    return {"chain": chain, "batch": batch, "cases": [
        {"case_id": "", "title": f"用例·{pid}", "covers": [pid],
         "preconditions": "账号已登录且购物车有一件可售商品",
         "steps": ["登录并进入下单页", "提交订单"],
         "expected": ["订单金额按该点场景的规则计算"],
         "priority": "P1", "note": ""} for pid in points]}


def _opinion(value: str, kind: str = "颗粒度", ask: str = "拆成两步再断言") -> dict:
    return {"target": {"type": "node", "value": value}, "kind": kind,
            "ask": ask, "evidence": "步骤合并，失败难定位"}


_HUMAN_ASK = "人类反馈：断言要写成可核对的金额"


def _book_human_op(env, batch: str) -> None:
    """往编写环意见簿手工记一条「门后回溯」来源的未销账意见（T22 的形状，本文件先当护栏）。

    与批评审意见同 `block`：批环三处在途判定（评审简报 / 「无在途即收口」/ 归因盖账）都必须按
    `source="case_block"` 把它滤掉——三层侧就是这么在构造上免疫跨来源串味的。
    """
    led = _led(env)
    led.data["writing"]["opinions"].append(
        {"ref": "op-90", "key": "node:cc-0001:颗粒度", "source": "case_human", "block": batch,
         "target": {"type": "node", "value": "cc-0001"}, "kind": "颗粒度",
         "ask": _HUMAN_ASK, "evidence": "人工反馈",
         "resolved": False, "escalated": False, "disposition": "", "note": ""})
    led.save()


def _op_by_ref(led, ref: str) -> dict:
    return next(op for op in led.data["writing"]["opinions"] if op["ref"] == ref)


def _boot_to_first_batch(env, state, task, frames):
    """两段开环：驱动①索要计划（此时账本已在盘上，plan 落盘不会被归档）→ 落 plan →
    驱动②（h_plan→case_plan→首批指令）。返回累加后的现场供各批环用例复用。"""
    _append(state, _drive(env, state, task, writer=frames.append))      # ① 索要计划
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    turn = _drive(env, state, task, writer=frames.append)               # ② 首批指令
    _append(state, turn)
    assert _end_text(frames) == ""                    # 开环不许半路终局（halted 也落在这里）
    return turn


def test_case_batch_r0_closes_and_dispatches_next_batch(tmp_path):
    kb = _kb_with_many_points(tmp_path, n=11)          # 11 点 ⇒ b1 十点 + b2 一点
    env = _env(tmp_path, kb)
    task = ScriptTask()                                # 默认判决 REV_CLEAN：无意见
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []

    turn = _boot_to_first_batch(env, state, task, frames)
    assert "ch-0001-b1" in turn["messages"][-1].content
    assert task.calls == []                            # 还没读过草稿，评审子一次都不许派

    b1 = _manifest_points(env, "ch-0001-b1")
    assert len(b1) == CASE_BATCH_CAP
    _write(env.cases_dir() / "ch-0001-b1.json", _cases_payload("ch-0001", "ch-0001-b1", b1))

    turn = _drive(env, state, task, writer=frames.append)     # ③ 收草稿→r0→下一批
    assert task.call_ids() == ["case-ch-0001-b1-r0"]
    assert task.calls[0]["agent"] == "case_review"            # 裁定 36②：批内评审必是子智能体
    led = _led(env)
    assert led.data["writing"]["batches"][0]["state"] == "done"
    assert led.data["writing"]["batches"][1]["state"] == "todo"
    assert (led.cursor["stage"], led.cursor["layer"], led.cursor["block"]) \
        == ("case_gen", "ch-0001", "ch-0001-b2")
    assert "第 2/2 批" in turn["messages"][-1].content
    saved = json.loads((env.cases_dir() / "ch-0001-b1.json").read_text(encoding="utf-8"))
    assert [c["case_id"] for c in saved["cases"]] == [f"cc-{i:04d}" for i in range(1, 11)]
    assert led.data["counters"]["case"] == 10                 # 序号只由账本推进
    assert kb.upserts == [] and kb.deletes == []              # 裁定 35：第四层零 KB 写


def test_uncovered_batch_is_reasked_before_paying_for_review(tmp_path):
    """批内自检（确定性）在评审之前：漏点当场重问，一次评审子调用都不烧（裁定 39 的省钱面）。"""
    kb = _kb_with_many_points(tmp_path, n=11)
    env = _env(tmp_path, kb)
    task = ScriptTask()
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []
    _boot_to_first_batch(env, state, task, frames)

    b1 = _manifest_points(env, "ch-0001-b1")
    _write(env.cases_dir() / "ch-0001-b1.json",
           _cases_payload("ch-0001", "ch-0001-b1", b1[: len(b1) - 1]))   # 少写最后一条
    turn = _drive(env, state, task, writer=frames.append)
    assert task.calls == []                            # ← 硬防线：坏批不进付费评审
    text = turn["messages"][-1].content
    assert "没有任何用例认领" in text and b1[-1] in text
    assert _led(env).cursor["block"] == "ch-0001-b1"   # 原地重问，不转场


def test_case_gen_mismatched_batch_selfreport_is_reasked(tmp_path):
    """批文件自报批次与游标不符 ⇒ 原地重问：交付表按账本归属，文件头按自报——
    两处分裂即裁定 38 的保护前提，坏归属的批不得进补号与付费评审。"""
    kb = _kb_with_many_points(tmp_path, n=11)
    env = _env(tmp_path, kb)
    task = ScriptTask()
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []
    _boot_to_first_batch(env, state, task, frames)

    b1 = _manifest_points(env, "ch-0001-b1")
    _write(env.cases_dir() / "ch-0001-b1.json",
           _cases_payload("ch-0001", "ch-0001-b2", b1))     # b1 文件头写着 b2
    turn = _drive(env, state, task, writer=frames.append)
    text = turn["messages"][-1].content
    assert "ch-0001-b2" in text and "ch-0001-b1" in text    # 期望值与实际值都要给人看
    assert task.calls == []                                 # 未达评审
    assert not (env.reviews_dir / "case-ch-0001-b1-r0.review.md").exists()
    led = _led(env)
    assert led.data["writing"]["batches"][0]["state"] == "todo"    # 没进 "drafted"
    assert led.data["counters"]["case"] == 0                       # 没烧任何 cc- 序号
    saved = json.loads((env.cases_dir() / "ch-0001-b1.json").read_text(encoding="utf-8"))
    assert [c["case_id"] for c in saved["cases"]] == [""] * len(b1)  # 文件原样未被补号改写


def test_case_gen_mismatched_chain_selfreport_is_reasked(tmp_path):
    """批文件自报链路归属错（写错链）⇒ 同样原地重问，链路腿与批次腿共用同一条归属核对。"""
    kb = _kb_with_many_points(tmp_path, n=11)
    env = _env(tmp_path, kb)
    task = ScriptTask()
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []
    _boot_to_first_batch(env, state, task, frames)

    b1 = _manifest_points(env, "ch-0001-b1")
    _write(env.cases_dir() / "ch-0001-b1.json",
           _cases_payload("ch-0002", "ch-0001-b1", b1))    # 链路写成另一条
    turn = _drive(env, state, task, writer=frames.append)
    text = turn["messages"][-1].content
    assert "ch-0002" in text and "ch-0001" in text
    assert task.calls == []
    assert not (env.reviews_dir / "case-ch-0001-b1-r0.review.md").exists()
    led = _led(env)
    assert led.data["writing"]["batches"][0]["state"] == "todo"
    assert led.cursor["block"] == "ch-0001-b1"
    assert led.data["counters"]["case"] == 0                       # 与 batch 腿同款：没烧任何 cc- 序号
    saved = json.loads((env.cases_dir() / "ch-0001-b1.json").read_text(encoding="utf-8"))
    assert [c["case_id"] for c in saved["cases"]] == [""] * len(b1)   # 文件原样未被补号改写


# ---- T21 意见环：开环 / 处置销账 / unresolved / 归因 / 简报形状纪律 ----

def _to_case_opt(tmp_path: Path):
    """把一条 11 点链路推到「b1 出意见、驱动正在等处置表」的现场。"""
    kb = _kb_with_many_points(tmp_path, n=11)
    env = _env(tmp_path, kb)
    task = ScriptTask(script={"case-*-r0": _j({"opinions": [_opinion("cc-0001")],
                                               "resolutions": []})})
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []
    _boot_to_first_batch(env, state, task, frames)                       # 首批指令就位
    b1 = _manifest_points(env, "ch-0001-b1")
    _write(env.cases_dir() / "ch-0001-b1.json", _cases_payload("ch-0001", "ch-0001-b1", b1))
    turn = _drive(env, state, task, writer=frames.append)                # r0 出意见 → 优化指令
    assert _end_text(frames) == ""                     # 现场开环干净：没终局、没 halted
    return env, task, state, frames, turn


def test_batch_opinion_opens_opt_ring(tmp_path):
    env, task, state, frames, turn = _to_case_opt(tmp_path)
    led = _led(env)
    assert (led.cursor["stage"], int(led.cursor["round"]), led.cursor["source"]) \
        == ("case_opt", 0, "case_block")
    assert led.data["writing"]["batches"][0]["state"] == "drafted"       # 没收口
    op = led.data["writing"]["opinions"][0]
    assert (op["ref"], op["source"], op["block"], op["resolved"], op["disposition"]) \
        == ("op-01", "case_block", "ch-0001-b1", False, "")
    assert task.calls[0]["call_id"] == "case-ch-0001-b1-r0"
    text = turn["messages"][-1].content
    assert "case-ch-0001-b1-in-r0.json" in text and "case-ch-0001-b1-fix-r0.json" in text
    assert "fixed|covered|unresolved" in text                            # 枚举逐字下发


def test_disposition_then_clean_re_review_closes_batch(tmp_path):
    env, task, state, frames, _ = _to_case_opt(tmp_path)
    _write(env.reviews_dir / "case-ch-0001-b1-fix-r0.json",
           {"dispositions": [{"ref": "op-01", "status": "fixed", "note": "已拆两步并补断言"}]})
    _drive(env, state, task, writer=frames.append)
    led = _led(env)
    assert task.call_ids() == ["case-ch-0001-b1-r0", "case-ch-0001-b1-r1"]
    op = led.data["writing"]["opinions"][0]
    assert op["resolved"] is True and op["disposition"] == "fixed"
    assert "已拆两步并补断言" in op["note"]
    assert led.data["writing"]["batches"][0]["state"] == "done"
    assert led.cursor["block"] == "ch-0001-b2"          # 串行：下一批（第二批刻意留着，见夹具说明）


def test_unresolved_disposition_books_writing_unresolved(tmp_path):
    """主智能体判定消化不了 → 带账离开在途集（escalated），未消化项进 `writing["unresolved"]`。"""
    env, task, state, frames, _ = _to_case_opt(tmp_path)
    _write(env.reviews_dir / "case-ch-0001-b1-fix-r0.json",
           {"dispositions": [{"ref": "op-01", "status": "unresolved", "note": "本轮造不出数据"}]})
    _drive(env, state, task, writer=frames.append)
    led = _led(env)
    assert led.data["writing"]["opinions"][0]["escalated"] is True
    assert led.data["writing"]["unresolved"] == [
        {"block": "ch-0001-b1", "refs": ["op-01"], "cause": "评审分歧", "note": "本轮造不出数据"}]
    assert led.data["writing"]["batches"][0]["state"] == "done"


def test_bad_disposition_is_reasked_not_booked(tmp_path):
    """处置表形状错（ref 不在簿 / status 越枚举）⇒ 重问，绝不计入轮次、绝不销账。"""
    env, task, state, frames, _ = _to_case_opt(tmp_path)
    _write(env.reviews_dir / "case-ch-0001-b1-fix-r0.json",
           {"dispositions": [{"ref": "op-99", "status": "fixed", "note": "x"},
                             {"ref": "op-01", "status": "done", "note": "y"}]})
    turn = _drive(env, state, task, writer=frames.append)
    text = turn["messages"][-1].content
    assert "ref 不在意见簿中" in text and "status 须为 fixed|covered|unresolved" in text
    assert task.call_ids() == ["case-ch-0001-b1-r0"]    # 一次也没复审
    led = _led(env)
    assert led.data["writing"]["opinions"][0]["resolved"] is False
    assert led.cursor["stage"] == "case_opt"            # 原地重问，不转场


def test_case_attribute_books_cause_and_closes(tmp_path):
    env, task, state, frames, _ = _to_case_opt(tmp_path)
    _book_human_op(env, "ch-0001-b1")         # 同 block 的门后回溯意见：归因不许一并盖账
    led = _led(env)
    led.cursor.update({"stage": "case_attribute", "round": 5})   # 复现「轮次用尽」现场
    led.save()
    turn = _drive(env, state, task, writer=frames.append)        # 首派归因
    assert "attr-case-ch-0001-b1-r5.json" in turn["messages"][-1].content
    assert (env.attribution_dir / "open-case-ch-0001-b1-r5.json").is_file()
    open_raw = (env.attribution_dir / "open-case-ch-0001-b1-r5.json").read_text(encoding="utf-8")
    assert _HUMAN_ASK not in open_raw                            # 在途清单只摊本来源的意见

    _write(env.attribution_dir / "attr-case-ch-0001-b1-r5.json",
           {"cause": "成本超限", "note": "评审与生成反复不一致，再跑只烧钱"})
    _drive(env, state, task, writer=frames.append)
    led = _led(env)
    assert led.data["writing"]["unresolved"][0]["cause"] == "成本超限"
    assert led.data["writing"]["unresolved"][0]["refs"] == ["op-01"]
    assert _op_by_ref(led, "op-90")["escalated"] is False         # 人类意见留给门后回溯环处置
    assert led.data["writing"]["batches"][0]["state"] == "done"
    assert led.cursor["block"] == "ch-0001-b2"


def test_batch_review_brief_literals_pass_review_schema(tmp_path):
    """走查三 W3-1 教训制度化：简报 advertise 的每个字面量都必须真能过 ReviewOut。"""
    kb = _kb_with_many_points(tmp_path, n=11)              # 留第二批：末批收口会撞未注册的 case_gate
    env = _env(tmp_path, kb)
    task = ScriptTask()
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []
    _boot_to_first_batch(env, state, task, frames)
    _write(env.cases_dir() / "ch-0001-b1.json",
           _cases_payload("ch-0001", "ch-0001-b1", _manifest_points(env, "ch-0001-b1")))
    _drive(env, state, task, writer=frames.append)

    brief = task.calls[0]["brief"]
    kinds = re.search(r'"kind": "([^"]+)"', brief).group(1).split("|")
    types = re.search(r'"type": "([^"]+)"', brief).group(1).split("|")
    assert len(kinds) == 5 and types == ["node"]
    for kind in kinds:
        ReviewOut.model_validate({"opinions": [
            {"target": {"type": types[0], "value": "cc-0001"}, "kind": kind,
             "ask": "怎么改", "evidence": "依据"}], "resolutions": []})
    assert '"resolutions"' in brief and '"ref": "op-01"' in brief and '"resolved": true' in brief
    assert _end_text(frames) == ""                       # 收帧有断言：整条开环没终局


def test_batch_review_brief_rejects_out_of_enum_kind(tmp_path):
    """打错字必须即红：简报给出的枚举之外任何值都过不了 schema（否则真机会 halted）。"""
    kb = _kb_with_many_points(tmp_path, n=11)              # 同上一条：刻意留第二批
    env = _env(tmp_path, kb)
    task = ScriptTask()
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []
    _boot_to_first_batch(env, state, task, frames)
    _write(env.cases_dir() / "ch-0001-b1.json",
           _cases_payload("ch-0001", "ch-0001-b1", _manifest_points(env, "ch-0001-b1")))
    _append(state, _drive(env, state, task, writer=frames.append))
    kinds = re.search(r'"kind": "([^"]+)"', task.calls[0]["brief"]).group(1).split("|")
    from pydantic import ValidationError
    for kind in kinds:
        with pytest.raises(ValidationError):
            ReviewOut.model_validate({"opinions": [
                {"target": {"type": "node", "value": "cc-0001"}, "kind": kind + "X",
                 "ask": "a", "evidence": "b"}], "resolutions": []})
    assert _end_text(frames) == ""                       # 收帧有断言：整条开环没终局


# ---- T21 修复轮 1：I-1 优化轮同口径自检 / I-2 在途意见按 source 过滤 / M-3 轮次用尽转场 ----

def test_opt_round_uncovering_a_point_is_reasked(tmp_path):
    """优化轮交回的正文与首轮同口径复核：机器可证的漏点当场重问，不补号、不落处置表、不派复审。

    `case_opt_instruction` 明文授权「删掉无用例」，而 `instructions.py` 又向模型承诺「否则会被判漏测」——
    这条判定必须在优化路径上真存在，否则一轮优化就能把某点唯一认领的那条删掉还照样收口复审（裁定 39）。
    """
    env, task, state, frames, _ = _to_case_opt(tmp_path)
    path = env.cases_dir() / "ch-0001-b1.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    covered_before = [c["covers"][0] for c in saved["cases"]]
    assert saved["cases"][-1]["case_id"] == "cc-0010" and covered_before[-1] == "pt-0010"
    # 删掉 pt-0010 的唯一认领，并新增一条空号用例（只压已有点）：自检若被跳过，补号就会白烧序号。
    saved["cases"] = saved["cases"][:-1] + [
        _cases_payload("ch-0001", "ch-0001-b1", ["pt-0001"])["cases"][0]]
    _write(path, saved)
    _write(env.reviews_dir / "case-ch-0001-b1-fix-r0.json",
           {"dispositions": [{"ref": "op-01", "status": "fixed", "note": "已拆两步并补断言"}]})

    turn = _drive(env, state, task, writer=frames.append)
    assert turn["case"]["route"] == "agent"
    text = turn["messages"][-1].content
    assert "没有任何用例认领" in text and "pt-0010" in text     # hard detail 文案原样进重问
    assert task.call_ids() == ["case-ch-0001-b1-r0"]            # 零复审：坏批不烧付费评审
    assert not (env.reviews_dir / "case-ch-0001-b1-r1.review.md").exists()
    led = _led(env)
    assert led.data["writing"]["batches"][0]["state"] != "done"  # 没收口
    assert led.data["counters"]["case"] == 10                    # 没为新正文补号
    assert led.cursor["stage"] == "case_opt"                     # 原地重问，不转场
    op = led.data["writing"]["opinions"][0]
    assert op["resolved"] is False and op["escalated"] is False  # 处置表没被落账
    assert _end_text(frames) == ""
    assert led.data["writing"]["unresolved"] == []


def test_opt_round_mismatched_root_batch_is_reasked(tmp_path):
    """优化轮把根 batch 写成别批 ⇒ 归属核对同样挡下：整个文件被重写，根归属也可能被写坏，
    自报归属护栏（裁定 38）必须在优化环也跑一次，不然交付表与正文又分成两张皮。"""
    env, task, state, frames, _ = _to_case_opt(tmp_path)
    path = env.cases_dir() / "ch-0001-b1.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["batch"] = "ch-0001-b2"                          # 正文留在 b1，文件头自称 b2
    _write(path, saved)
    _write(env.reviews_dir / "case-ch-0001-b1-fix-r0.json",
           {"dispositions": [{"ref": "op-01", "status": "fixed", "note": "已拆两步并补断言"}]})

    turn = _drive(env, state, task, writer=frames.append)
    assert turn["case"]["route"] == "agent"
    text = turn["messages"][-1].content
    assert "ch-0001-b2" in text and "ch-0001-b1" in text   # 期望值与实际值都要给人看
    assert task.call_ids() == ["case-ch-0001-b1-r0"]       # 零复审
    led = _led(env)
    assert led.data["writing"]["batches"][0]["state"] != "done"
    assert led.data["counters"]["case"] == 10
    assert led.data["writing"]["opinions"][0]["resolved"] is False
    assert led.cursor["stage"] == "case_opt"
    assert _end_text(frames) == ""


def test_case_review_ignores_human_sourced_ops_in_same_batch(tmp_path):
    """批评审只认 `case_block` 来源的在途意见：同一 block 里门后回溯（`case_human`）的意见
    既不喂给评审子，也不把批卡在「仍有在途」。三层靠 `_open_of(source=...)` 在构造上免疫，
    第四层必须同款——这条断言与来源是否只有一种无关，T22 落地后仍是护栏。"""
    kb = _kb_with_many_points(tmp_path, n=11)
    env = _env(tmp_path, kb)
    task = ScriptTask()                                    # 默认 REV_CLEAN：评审子给干净判决
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []
    _boot_to_first_batch(env, state, task, frames)

    _book_human_op(env, "ch-0001-b1")

    b1 = _manifest_points(env, "ch-0001-b1")
    _write(env.cases_dir() / "ch-0001-b1.json", _cases_payload("ch-0001", "ch-0001-b1", b1))
    _append(state, _drive(env, state, task, writer=frames.append))

    led = _led(env)
    assert task.call_ids() == ["case-ch-0001-b1-r0"]
    assert _HUMAN_ASK not in task.calls[0]["brief"]           # 简报里不出现跨来源意见
    assert "上一轮尚未销账的意见" not in task.calls[0]["brief"]
    assert led.data["writing"]["batches"][0]["state"] == "done"   # 批照样收口
    assert led.cursor["block"] == "ch-0001-b2"                    # 并推进到下一批
    assert _op_by_ref(led, "op-90")["resolved"] is False           # 人类意见不由批环销账
    assert _op_by_ref(led, "op-90")["escalated"] is False          # 也不由批环盖账
    assert _end_text(frames) == ""


def test_case_round_cap_attributes_with_the_round_it_ended_on(tmp_path):
    """轮次用尽的转场是 `case_attribute` 的唯一生产入口：游标必须带上轮号与来源，
    归因两套文件按该轮号渲染（照三层 test_block_round_cap_attributes_and_continues 的先例）。"""
    kb = _kb_with_many_points(tmp_path, n=11)
    env = _env(tmp_path, kb)
    task = ScriptTask({"case-ch-0001-b1-r*": _j({"opinions": [_opinion("cc-0001")],
                                                 "resolutions": []})})   # 同一批恒定出一条意见
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []
    _boot_to_first_batch(env, state, task, frames)
    b1 = _manifest_points(env, "ch-0001-b1")
    _write(env.cases_dir() / "ch-0001-b1.json", _cases_payload("ch-0001", "ch-0001-b1", b1))
    turn = _drive(env, state, task, writer=frames.append)                # r0 出意见 → case_opt
    _append(state, turn)

    steps = 0
    while _led(env).cursor["stage"] == "case_opt":
        led = _led(env)
        round_no = int(led.cursor["round"])
        refs = [op["ref"] for op in led.data["writing"]["opinions"]
                if op["block"] == "ch-0001-b1" and not (op["resolved"] or op["escalated"])]
        _write(env.reviews_dir / f"case-ch-0001-b1-fix-r{round_no}.json",
               {"dispositions": [{"ref": ref, "status": "fixed", "note": "已按意见拆分"}
                                 for ref in refs]})
        turn = _drive(env, state, task, writer=frames.append)
        _append(state, turn)
        steps += 1
        assert steps <= ROUND_CAP                                  # 防死循环：轮数有界

    led = _led(env)
    assert (led.cursor["stage"], int(led.cursor["round"]), led.cursor["source"]) \
        == ("case_attribute", ROUND_CAP, "case_block")              # 转场带上轮号与来源
    assert (led.cursor["layer"], led.cursor["block"]) == ("ch-0001", "ch-0001-b1")
    assert len([c for c in task.call_ids() if c.startswith("case-ch-0001-b1-r")]) \
        == ROUND_CAP + 1                                            # r0..r5：5 轮优化用尽
    assert f"attr-case-ch-0001-b1-r{ROUND_CAP}.json" in turn["messages"][-1].content
    assert (env.attribution_dir / f"open-case-ch-0001-b1-r{ROUND_CAP}.json").is_file()
    assert led.data["writing"]["batches"][0]["state"] == "drafted"  # 归因未交回，批还没收口
    assert _end_text(frames) == ""


def test_case_attribute_instruction_enums_share_the_stage_causes():
    """枚举同源钉桩：`case_attribute_instruction` 硬写的四选一必须与 `_CAUSES` 逐字一致
    （重问文案从 `_CAUSES` 渲染，两份漂移就是 W3-1 那一类真机 halted）。"""
    text = case_attribute_instruction("ch-0001-b1", opinions_path="a-in.json",
                                      out_path="a-out.json")
    assert "|".join(_CAUSES) in text
    assert "cause 只能取上面四个值之一" in text
