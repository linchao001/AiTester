# backend/tests/test_case_design_driver.py
"""T8 驱动节点单元测试：模块级直调 drive_turn（writer 显式传入），drain 循环仿真主智能体。

driver 是一个可测试的纯状态机——env/task_tool/writer 都是显式入参；「主智能体」由
simulate() 承担：按账本 cursor 判定当前该产出哪个制品，写文件后把控制权还给 driver，
等价于真实图里 agent 节点跑完一个回合。
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import ToolException

from aitester.case_design.driver import case_env_of
from aitester.case_design.env import CaseDesignEnv
from aitester.case_design.ledger import Ledger
from aitester.case_design.stages import drive_turn

REV_CLEAN = '```json\n{"opinions": [], "resolutions": []}\n```'


def _j(obj) -> str:
    return "```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"


def _resp(metadata=None, success=True, answer="ok"):
    return type("R", (), {"success": success, "answer": answer, "metadata": metadata or {}})()


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


class StubKb:
    """吃 KbClient 面（run_job_sync）的替身；upserts/deletes 记账供断言。"""

    def __init__(self, layers=None, root="", fail_upserts=0):
        self.layers = layers or {}
        self.kb_root_dir = root
        self.upserts: list = []
        self.deletes: list = []
        self.fail_upserts = fail_upserts
        self.job_names: list = []

    def run_job_sync(self, name, *, project_id="default", agent_id="console",
                     timeout=60.0, **kwargs):
        self.job_names.append(name)
        if name == "case_nodes_list":
            layer = kwargs["layer"]
            rows = list(self.layers.get(layer, []))
            return _resp({"layer": layer, "count": len(rows), "nodes": rows})
        if name == "case_node_upsert":
            if self.fail_upserts > 0:
                self.fail_upserts -= 1
                return _resp(success=False, answer="disk full")
            self.upserts.append((kwargs["layer"], dict(kwargs["node"])))
            return _resp({"layer": kwargs["layer"], "id": kwargs["node"].get("id", "?"),
                          "path": "p"})
        if name == "case_node_delete":
            self.deletes.append((kwargs["layer"], kwargs["id"]))
            return _resp({"layer": kwargs["layer"], "id": kwargs["id"], "deleted": True})
        raise AssertionError(f"未知 job: {name}")


class ScriptTask:
    """吃 run_reviewer 需要的四面；call_id 精确命中 script，否则按前缀给结构化默认值。"""

    def __init__(self, script=None):
        self.script = dict(script or {})
        self.parallel = {"case_review": True, "case_review_blind": True}
        self.roster = {}
        self.calls: list = []

    def build_child(self, agent_id):
        return {"child_of": agent_id}

    def drive(self, child, brief, *, call_id, name, title, config, isolated=False):
        self.calls.append({"agent": child["child_of"], "call_id": call_id,
                           "brief": brief, "title": title})
        out = self.script.get(call_id)
        if out is None:
            import fnmatch
            for key, value in self.script.items():
                if fnmatch.fnmatch(call_id, key):
                    out = value
                    break
        if out is None:
            out = self._default(call_id)
        if isinstance(out, Exception):
            raise out
        return out

    @staticmethod
    def _default(call_id: str) -> str:
        if call_id.startswith("enum"):
            return _j({"items": []})
        if call_id.startswith("cmp"):
            return _j({"items": []})
        if call_id.startswith("claims"):
            return _j({"claims": [], "opinions": []})
        if call_id.startswith("matrix"):
            return _j({"cells": []})
        return REV_CLEAN

    def call_ids(self) -> list[str]:
        return [c["call_id"] for c in self.calls]


def _env(tmp_path: Path, kb) -> CaseDesignEnv:
    env = CaseDesignEnv(project_dir=str(tmp_path), kb=kb)
    env.ensure_dirs()
    return env


def _drive(env, state, task, *, config=None, writer=None):
    return drive_turn(state, config or {"configurable": {"thread_id": "t1"}},
                      env=env, task_tool=task, writer=writer or (lambda e: None))


def _append(state, turn) -> None:
    state["messages"] = state["messages"] + list(turn["messages"])
    state["case"] = turn["case"]


def simulate(env: CaseDesignEnv, *, plan=None, gen_nodes=None, gate_fix=None) -> None:
    """按账本 cursor 仿真主智能体写制品（drain 循环里承担 agent 节点角色）。"""

    led = Ledger.load(env.design)
    cur = led.cursor
    stage, layer, block = cur["stage"], cur["layer"], cur["block"]
    if stage == "plan":
        _write(env.design / "plan.json", plan or {
            "task_kind": "design", "entry_layer": "chain", "terminal_layer": "point",
            "target_subtree": "", "source_files": [], "note": "全量"})
        return
    if stage == "gen":
        content = None
        if gen_nodes is not None:
            content = gen_nodes(layer, block, led.layer(layer)["mode"])
        if content is None:
            if layer == "chain":
                content = {"nodes": [{
                    "op": "upsert", "type": "chain", "name": "下单链路", "level": 1,
                    "parent": "", "business_scope": "下单主流程", "excluded": "支付失败回滚",
                    "priority": "P0"}]}
            elif layer == "story":
                content = {"nodes": [
                    {"op": "upsert", "type": "story", "name": "下单成功", "chains": [block],
                     "actor": "买家", "preconditions": "已登录", "trigger": "提交订单",
                     "expected": "订单创建", "assumptions": ["取消场景由另一故事覆盖"],
                     "priority": "P0"},
                    {"op": "upsert", "type": "story", "name": "下单取消", "chains": [block],
                     "actor": "买家", "preconditions": "已登录", "trigger": "取消订单",
                     "expected": "订单关闭", "priority": "P0"}]}
            else:
                content = {"nodes": [{
                    "op": "upsert", "type": "point", "name": f"{block} 正向", "story": block,
                    "scenario": "已登录且库存充足时提交",
                    "entities": ["订单"] if str(block).endswith("0001") else ["库存"],
                    "directions": ["正向"], "priority": "P0"}]}
        _write(env.drafts_dir(layer) / f"{block}.json", {"layer": layer, "block": block, **content})
        return
    if stage == "opt":
        r = cur["round"]
        src = cur["source"]
        prefix = {"block": f"blk-{layer}-{block}", "audit": f"aud-{layer}",
                  "human": f"human-{layer}"}[src]
        refs = json.loads((env.reviews_dir / f"{prefix}-in-r{r}.json").read_text(
            encoding="utf-8"))["refs"]
        _write(env.reviews_dir / f"{prefix}-fix-r{r}.json", {
            "dispositions": [{"ref": item["ref"], "status": "fixed", "note": "已改"}
                             for item in refs]})
        return
    if stage == "attribute":
        name = f"attr-{layer}-{block or 'layer'}-r{cur['round']}.json"
        _write(env.attribution_dir / name, {"cause": "评审分歧", "note": "反复意见不收敛"})
        return
    if stage == "gate":
        if gate_fix is None:
            raise AssertionError("仿真缺少 gate 修复动作（gate_fix=None）")
        gate_fix(env)
        return
    raise AssertionError(f"仿真无法处理的 stage：{stage}")


def _end_text(frames) -> str:
    turns = [f for f in frames if f.get("type") == "turn"]
    return str(turns[-1].get("text") or "") if turns else ""


def drain(env, kb, task, *, state=None, plan=None, gen_nodes=None, gate_fix=None,
          config=None, max_steps=120):
    """驱动↔agent 仿真交替推进，直到 route=end；终局帧存 state["frames"]。"""

    state = state or {"messages": [HumanMessage("按业务信息生成测试设计")], "case": {}}
    for _ in range(max_steps):
        frames: list[dict] = []
        turn = _drive(env, state, task, config=config, writer=frames.append)
        _append(state, turn)
        if turn["case"]["route"] == "end":
            state["frames"] = frames
            return state
        simulate(env, plan=plan, gen_nodes=gen_nodes, gate_fix=gate_fix)
    raise AssertionError("drain 未在步数上限内收敛")


# ---- 以下追加到同一文件末尾 ----

def test_case_env_of_reads_configurable(tmp_path):
    env = CaseDesignEnv(project_dir=str(tmp_path), kb=None)
    assert case_env_of({"configurable": {"case_design_env": env}}) is env
    assert case_env_of({"configurable": {}}) is None and case_env_of(None) is None


def test_env_none_passthrough_without_side_effects(tmp_path):
    env = CaseDesignEnv(project_dir=str(tmp_path), kb=None)      # 直通：不 ensure_dirs
    state = {"messages": [HumanMessage("你好")], "case": {}}
    t1 = drive_turn(state, {}, env=None, task_tool=None, writer=lambda e: None)
    assert t1["case"]["route"] == "agent" and t1["messages"] == []
    _append(state, t1)
    state["messages"].append(AIMessage("直接回答"))
    t2 = drive_turn(state, {}, env=None, task_tool=None, writer=lambda e: None)
    assert t2["case"]["route"] == "end" and t2["messages"] == []
    assert not env.design.exists()                               # 不建账本、不发指令


def test_plan_collects_probe_seeds_and_dispatches_gen(tmp_path):
    kb = StubKb(layers={
        "chain": [{"id": "ch-0002", "type": "chain", "parent": "ch-0001", "level": 2},
                  {"id": "ch-0001", "type": "chain", "parent": "", "level": 1}],
        "story": [], "point": []})
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("按业务信息生成测试设计")], "case": {}}
    task = ScriptTask()
    t = _drive(env, state, task)
    assert t["case"]["route"] == "agent"
    assert "design/plan.json" in t["messages"][0].content
    _append(state, t)
    simulate(env)
    t = _drive(env, state, task)
    assert t["case"]["route"] == "agent"
    assert "design/drafts/chain/ALL.json" in t["messages"][0].content
    led = Ledger.load(env.design)
    assert led.cursor["stage"] == "gen" and led.cursor["layer"] == "chain"
    assert led.cursor["block"] == "ALL" and led.cursor["asked"] is True
    assert led.data["task"]["probe"]["chain"]["maintained"] is True
    assert led.data["counters"]["chain"] == 2                    # update 防撞库种子
    assert (env.manifests_dir / "kb-chain.json").is_file()
    assert (env.manifests_dir / "sources.json").is_file()


def test_narrow_target_skips_target_layer_and_scopes_blocks(tmp_path):
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "parent": "", "level": 1},
                  {"id": "ch-0002", "type": "chain", "parent": "ch-0001", "level": 2}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0002"]}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001",
                   "entities": ["订单"], "directions": ["正向"]}]})
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("针对链路 ch-0002 生成测试设计")], "case": {}}
    task = ScriptTask()
    t = _drive(env, state, task)
    _append(state, t)
    simulate(env, plan={"task_kind": "design", "entry_layer": "chain",
                        "terminal_layer": "point", "target_subtree": "ch-0002",
                        "source_files": [], "note": "窄范围"})
    t = _drive(env, state, task)
    assert t["case"]["route"] == "agent"
    assert "design/drafts/story/ch-0002.json" in t["messages"][0].content   # 直接进故事层
    led = Ledger.load(env.design)
    assert led.layer("chain")["state"] == "skipped"              # 目标层自身只做只读上下文
    assert led.layer("story")["mode"] == "update"
    assert led.data["task"]["plan"]["blocks"]["story"] == ["ch-0002"]


def test_first_build_walks_to_awaiting_review(tmp_path):
    kb = StubKb()
    env = _env(tmp_path, kb)
    task = ScriptTask({
        "blk-chain-ALL-r0": _j({"opinions": [{"target": {"type": "node", "value": "ch-0001"},
            "kind": "颗粒度", "ask": "补充退款子链路", "evidence": "design/drafts/chain/ALL.json"}],
            "resolutions": []}),
        "blk-chain-ALL-r1": _j({"opinions": [], "resolutions": [
            {"ref": "op-01", "resolved": True, "note": "已补 ch-0002"}]}),
        "claims-story-r0-ch-0001": _j({"claims": [{"ref": "st-0001-a1", "verdict": "unclaimed",
            "owner": "", "note": "无认领"}], "opinions": []}),
        "claims-story-r1-ch-0001": _j({"claims": [{"ref": "st-0001-a1", "verdict": "covered",
            "owner": "st-0002", "note": ""}], "opinions": []}),
        "matrix-point-r0-ch-0001": _j({"cells": [
            {"entity": "订单", "story": "st-0002", "verdict": "not_needed",
             "reason": "取消流不触碰订单实体"},
            {"entity": "库存", "story": "st-0001", "verdict": "not_needed",
             "reason": "下单不扣减库存"}]}),
    })
    state = drain(env, kb, task)
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"
    assert led.cursor["stage"] == "gate"
    # 块环：一次意见→优化→复审清零（ref 由驱动分配）
    assert "blk-chain-ALL-r0" in task.call_ids() and "blk-chain-ALL-r1" in task.call_ids()
    chain_draft = json.loads((env.drafts_dir("chain") / "ALL.json").read_text(encoding="utf-8"))
    assert chain_draft["nodes"][0]["id"] == "ch-0001"            # ids 补丁已回写
    story_draft = json.loads((env.drafts_dir("story") / "ch-0001.json").read_text(encoding="utf-8"))
    assert [n["id"] for n in story_draft["nodes"]] == ["st-0001", "st-0002"]
    # ①②③ 各出一次判决
    assert any(c.startswith("enum-chain-") for c in task.call_ids())
    assert "cmp-chain-r0" in task.call_ids()
    assert "claims-story-r1-ch-0001" in task.call_ids()
    assert "matrix-point-r0-ch-0001" in task.call_ids()
    outline = (env.design / "outline.md").read_text(encoding="utf-8")
    assert "意见落点对照表" in outline and "op-01" in outline
    assert "取消流不触碰订单实体" in outline                       # ③「不需要」的业务理由
    assert "增量测试大纲" in outline
    # 未消化项为空：st-0001 的声称已被 r1 复审判 covered
    assert led.layer("story")["claims"][0]["verdict"] == "covered"
    assert state["frames"] and "等待人工评审" in _end_text(state["frames"])


def test_block_round_cap_attributes_and_continues(tmp_path):
    kb = StubKb()
    env = _env(tmp_path, kb)
    task = ScriptTask({"blk-chain-ALL-r*": _j({"opinions": [{
        "target": {"type": "node", "value": "ch-0001"}, "kind": "边界归属",
        "ask": "继续扩", "evidence": "x"}], "resolutions": []})})
    drain(env, kb, task, plan={"task_kind": "design", "entry_layer": "chain",
                               "terminal_layer": "chain", "source_files": [],
                               "note": "只链层", "target_subtree": ""})
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"                       # 归因后带未消化项继续走到大纲门
    rounds = [c for c in task.call_ids() if c.startswith("blk-chain-ALL-r")]
    assert len(rounds) == 6                                      # r0..r5，5 轮优化用尽
    unresolved = led.layer("chain")["unresolved"]
    assert unresolved and unresolved[0]["cause"] == "评审分歧"
    outline = (env.design / "outline.md").read_text(encoding="utf-8")
    assert "评审分歧" in outline


def test_gate_hard_issue_fixed_in_repair_loop(tmp_path):
    kb = StubKb()
    env = _env(tmp_path, kb)

    def broken_story(layer, block, mode):
        if layer == "story":
            return {"nodes": [{"op": "upsert", "type": "story", "name": "断爹故事",
                               "chains": ["ch-9999"], "actor": "a", "preconditions": "p",
                               "trigger": "t", "expected": "e", "priority": "P0"}]}
        return None

    def fix_gate(envx):
        path = envx.drafts_dir("story") / "ch-0001.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["nodes"][0]["chains"] = ["ch-0001"]
        _write(path, raw)

    drain(env, kb, ScriptTask(), gen_nodes=broken_story, gate_fix=fix_gate)
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"
    assert led.data["gate"]["round"] == 1
    assert (env.reviews_dir / "gate-issues-r1.json").is_file()


def test_gate_hard_unfixed_halts_at_cap(tmp_path):
    kb = StubKb()
    env = _env(tmp_path, kb)

    def broken_story(layer, block, mode):
        if layer == "story":
            return {"nodes": [{"op": "upsert", "type": "story", "name": "一直断",
                               "chains": ["ch-9999"], "actor": "a", "preconditions": "p",
                               "trigger": "t", "expected": "e", "priority": "P0"}]}
        return None

    drain(env, kb, ScriptTask(), gen_nodes=broken_story, gate_fix=lambda envx: None)
    led = Ledger.load(env.design)
    assert led.status == "halted"
    assert led.data["gate"]["round"] == 5                        # 修复环用尽


def test_writeback_success_strips_internal_fields(tmp_path):
    kb = StubKb()
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    state = {"messages": [HumanMessage("通过，回写")], "case": {}}
    turn = _drive(env, state, ScriptTask())                      # 新消息=新 run：boot 进 gate_interpret
    led = Ledger.load(env.design)
    assert turn["case"]["route"] == "end" and led.status == "done"
    layers_written = [layer for layer, _ in kb.upserts]
    assert layers_written.count("chain") == 1
    assert layers_written.count("story") == 2 and layers_written.count("point") == 2
    payload = kb.upserts[0][1]
    for banned in ("op", "block", "state", "in_scope", "round", "reason"):
        assert banned not in payload
    assert all(led.layer(l)["state"] == "done" for l in ("chain", "story", "point"))


def test_writeback_failure_retries_then_next_message_recovers(tmp_path):
    kb = StubKb(fail_upserts=3)                                  # 3 次尝试（1+2 重试）全失败
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    _drive(env, {"messages": [HumanMessage("通过")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert led.status == "writeback_failed"
    assert led.data["writeback"]["log"]
    turn = _drive(env, {"messages": [HumanMessage("重试")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert turn["case"]["route"] == "end" and led.status == "done"
    assert len(kb.upserts) == 5                                  # 恢复后一次写全（幂等重试）


def test_update_no_change_blocks_flow_and_mark_outline(tmp_path):
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "parent": "", "level": 1,
                   "name": "老链路", "priority": "P0"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"],
                   "name": "老故事", "priority": "P0"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001",
                   "name": "老点", "entities": ["订单"], "directions": ["正向"],
                   "priority": "P0"}]})
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask(), gen_nodes=lambda layer, block, mode:
          {"nodes": [], "note": "no_change"})
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"
    assert led.layer("chain")["mode"] == "update"
    assert led.data["no_change"] == [{"layer": "chain", "block": "ALL"},
                                     {"layer": "story", "block": "ch-0001"},
                                     {"layer": "point", "block": "st-0001"}]
    assert kb.upserts == []                                      # 人审前零回写
    outline = (env.design / "outline.md").read_text(encoding="utf-8")
    assert "本次无变化块" in outline
    # 人审通过 → 回写跳过 no_change 块（空草稿是合法终态，不是"不可解析"）
    turn = _drive(env, {"messages": [HumanMessage("通过")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert turn["case"]["route"] == "end" and led.status == "done"
    assert kb.upserts == [] and kb.deletes == []


def test_audit_opt_layer_skips_no_change_blocks(tmp_path):
    """层环优化在「无变化块 + 有意见块」混合层不被空草稿绊住（no_change 是合法终态）。

    场景取只点层窗口：st-0001 块无变化、st-0002 块出点；③ 矩阵 r0 判一个 needed 空格
    → 层环优化 → 若 _drafts_errors 把 st-0001 的空草稿判坏，重问 FIX_CAP 次后 halted。
    """
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "parent": "", "level": 1,
                   "name": "老链路", "priority": "P0"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"],
                   "name": "老故事", "priority": "P0"},
                  {"id": "st-0002", "type": "story", "chains": ["ch-0001"],
                   "name": "老故事二", "priority": "P0"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001",
                   "name": "老点", "entities": ["订单"], "directions": ["正向"],
                   "priority": "P0"}]})
    env = _env(tmp_path, kb)
    task = ScriptTask({
        "matrix-point-r0-ch-0001": _j({"cells": [
            {"entity": "库存", "story": "st-0002", "verdict": "needed", "reason": ""}]}),
        "matrix-point-r1-ch-0001": _j({"cells": [
            {"entity": "库存", "story": "st-0002", "verdict": "covered", "reason": ""}]}),
    })
    drain(env, kb, task, plan={"task_kind": "design", "entry_layer": "point",
                               "terminal_layer": "point", "target_subtree": "",
                               "source_files": [], "note": "只点层"},
          gen_nodes=lambda layer, block, mode:
          {"nodes": [], "note": "no_change"} if block == "st-0001" else None)
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"                       # 没被空草稿绊成 halted
    assert led.layer("point")["audit_round"] == 1               # 优化后重审推进了一轮
    assert led.data["no_change"] == [{"layer": "point", "block": "st-0001"}]
    assert "matrix-point-r0-ch-0001" in task.call_ids()
    assert "matrix-point-r1-ch-0001" in task.call_ids()


def test_human_revision_marks_downstream_stale_and_writeback_skips(tmp_path):
    kb = StubKb()
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    task2 = ScriptTask({"gate-int-r1": _j({"opinions": [{
        "target": {"type": "node", "value": "ch-0001"}, "kind": "颗粒度",
        "ask": "拆分链路", "evidence": "design/outline.md"}], "resolutions": []})})
    state = {"messages": [HumanMessage("链路要拆开")], "case": {}}
    turn = _drive(env, state, task2)                             # 回溯：结构化→opt 下发
    assert turn["case"]["route"] == "agent"
    assert "human-chain-in-r1.json" in turn["messages"][0].content
    simulate(env)                                                # 主智能体交回处置表
    turn = _drive(env, state, task2)                             # 重审→大纲门重出→awaiting_review
    assert turn["case"]["route"] == "end"
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"
    assert led.layer("story")["state"] == "stale_pending"
    assert led.layer("point")["state"] == "stale_pending"
    outline = (env.design / "outline.md").read_text(encoding="utf-8")
    assert "失效待重算" in outline
    # 通过 → 只回写链路层；下游保持 stale（A8：下次任务全块重跑）
    turn = _drive(env, {"messages": [HumanMessage("通过")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert turn["case"]["route"] == "end" and led.status == "done"
    assert [layer for layer, _ in kb.upserts] == ["chain"]
    assert led.layer("story")["state"] == "stale_pending"
    # 下一条任务：旧账本的 stale 标记随新账本携带
    _drive(env, {"messages": [HumanMessage("继续更新")], "case": {}}, ScriptTask())
    fresh = Ledger.load(env.design)
    assert fresh.data["carried_stale"] == ["story", "point"]
    assert fresh.data["task"] == {}                              # 新账本，等待 h_plan 填充


def test_plan_validation_nudge_then_halt(tmp_path):
    kb = StubKb()
    env = _env(tmp_path, kb)
    task = ScriptTask()
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    for _ in range(4):                                           # 首问 + 3 次携错重问
        turn = _drive(env, state, task)
        assert turn["case"]["route"] == "agent"
        _append(state, turn)
        simulate(env, plan=bad)
    turn = _drive(env, state, task)                              # 第 5 次：nudge 用尽
    assert turn["case"]["route"] == "end"
    led = Ledger.load(env.design)
    assert led.status == "halted"
    plan_asks = [m for m in state["messages"] if "design/plan.json" in str(m.content)]
    assert len(plan_asks) == 4


def test_reviewer_failure_converges_to_halted(tmp_path):
    kb = StubKb()
    env = _env(tmp_path, kb)
    task = ScriptTask({"blk-chain-ALL-r0": ToolException("Subagent 'case_review' failed: boom")})
    drain(env, kb, task)
    led = Ledger.load(env.design)
    assert led.status == "halted"
