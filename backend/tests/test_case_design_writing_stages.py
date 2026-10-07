# backend/tests/test_case_design_writing_stages.py
"""第四层编写环的阶段处理器与转场（挂具复用 test_case_design_driver.py，零联网零真模型）。"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage

from aitester.case_design import stages
from aitester.case_design.constants import CASE_BATCH_CAP, LAYER_CN
from aitester.case_design.ledger import Ledger
from aitester.case_design.stages import _writing_enabled

from test_case_design_driver import (  # 复用既有挂具，不抄第二份
    ScriptTask, StubKb, _append, _drive, _end_text, _env, _write, drain,
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
