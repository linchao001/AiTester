# backend/tests/test_case_design_driver.py
"""T8 驱动节点单元测试：模块级直调 drive_turn（writer 显式传入），drain 循环仿真主智能体。

driver 是一个可测试的纯状态机——env/task_tool/writer 都是显式入参；「主智能体」由
simulate() 承担：按账本 cursor 判定当前该产出哪个制品，写文件后把控制权还给 driver，
等价于真实图里 agent 节点跑完一个回合。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import ToolException

from aitester.case_design.driver import case_env_of
from aitester.case_design.env import CaseDesignEnv
from aitester.case_design.kb import KbClientError
from aitester.case_design.ledger import Ledger
from aitester.case_design.stages import (
    Ctx, _collect_writeback_items, _drafts_errors, _drop_out_of_window_hards,
    _patch_ids, drive_turn, h_writeback,
)

REV_CLEAN = '```json\n{"opinions": [], "resolutions": []}\n```'


def _j(obj) -> str:
    return "```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"


def _resp(metadata=None, success=True, answer="ok"):
    return type("R", (), {"success": success, "answer": answer, "metadata": metadata or {}})()


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


class StubKb:
    """吃 KbClient 面（run_job_sync）的替身；upserts/deletes 记账供断言。

    kb_root_dir 不能留空串（M-9）：空串会让 KbClient.list_business_files 把
    `Path("")/"business"` 解析成运行目录（= 仓库），仓库里一旦出现 backend/business/
    就会读到真东西。默认 None 表示「未绑定」，由 _env 绑到本用例的 tmp_path 下。
    """

    def __init__(self, layers=None, root=None, fail_upserts=0,
                 unchanged_ids=None, track_content=False, fail_upsert_after=0):
        self.layers = layers or {}
        self.kb_root_dir = str(root) if root is not None else ""
        self.upserts: list = []
        self.deletes: list = []
        self.fail_upserts = fail_upserts
        # 默认不带 unchanged 键：真实 step 只在「内容与库内一致」时才回传 True，
        # 既有终帧逐字断言（基句不许动）一律走「没带」这条形状。
        # unchanged_ids：只给这批 id 报未触碰（混编轮次——尾句只该数未触碰那部分）。
        self.unchanged_ids = unchanged_ids
        # track_content：像真实 step 那样记账——同一份内容第二次下发才报未触碰（首轮一律真写）。
        self.track_content = track_content
        self._seen: set = set()
        # fail_upsert_after：第 N+1 个 upsert 失败一次（模拟整轮中途崩、重试轮才写全）。
        self.fail_upsert_after = fail_upsert_after
        self._after_tripped = False
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
            if (self.fail_upsert_after and not self._after_tripped
                    and len(self.upserts) >= self.fail_upsert_after):
                self._after_tripped = True         # 只炸一次：重试轮就该写全
                return _resp(success=False, answer="disk full")
            node = dict(kwargs["node"])
            self.upserts.append((kwargs["layer"], node))
            meta = {"layer": kwargs["layer"], "id": node.get("id", "?"), "path": "p"}
            # 按内容记账：先查再记——这份内容已经在库里就是「未触碰」，第一次出现必须算真写。
            seen = json.dumps(node, sort_keys=True, ensure_ascii=False)
            if (node.get("id") in (self.unchanged_ids or ())
                    or (self.track_content and seen in self._seen)):
                meta["unchanged"] = True              # 库内已有同内容节点：step 报未触碰
            self._seen.add(seen)
            return _resp(meta)
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
    if not kb.kb_root_dir:
        # M-9：未显式给 root 的替身一律钉到本用例的 tmp_path 下——空串会让 KbClient
        # 把 `Path("")/"business"` 解析成运行目录（= 仓库），制品面绝不能读真仓库。
        kb.kb_root_dir = str(tmp_path / "kb_root_never_in_repo")
    env = CaseDesignEnv(project_dir=str(tmp_path), kb=kb)
    env.ensure_dirs()
    return env


def _drive(env, state, task, *, config=None, writer=None):
    return drive_turn(state, config or {"configurable": {"thread_id": "t1"}},
                      env=env, task_tool=task, writer=writer or (lambda e: None))


def _append(state, turn) -> None:
    state["messages"] = state["messages"] + list(turn["messages"])
    state["case"] = turn["case"]


def simulate(env: CaseDesignEnv, *, plan=None, gen_nodes=None, gate_fix=None,
             opt_fix=None) -> None:
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
        rows = (opt_fix(layer, block, r, refs) if opt_fix is not None else
                [{"ref": item["ref"], "status": "fixed", "note": "已改"} for item in refs])
        _write(env.reviews_dir / f"{prefix}-fix-r{r}.json", {"dispositions": rows})
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
          opt_fix=None, config=None, max_steps=120):
    """驱动↔agent 仿真交替推进，直到 route=end；终局帧存 state["frames"]。"""

    state = state or {"messages": [HumanMessage("按业务信息生成测试设计")], "case": {}}
    for _ in range(max_steps):
        frames: list[dict] = []
        turn = _drive(env, state, task, config=config, writer=frames.append)
        _append(state, turn)
        if turn["case"]["route"] == "end":
            state["frames"] = frames
            return state
        simulate(env, plan=plan, gen_nodes=gen_nodes, gate_fix=gate_fix, opt_fix=opt_fix)
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
    # R-18(a)：只链层任务的 empty_chain 是被「下游层未进本 run 窗口」豁免掉的，
    # 大纲必须把这层豁免显式报出来——「hard 全部为 0」不许冒充「没有可豁免的」。
    assert "- hard：全部为 0" in outline
    assert "- hard 豁免（下游层未进本 run 窗口）：empty_chain 1／empty_story 0" in outline


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
    """B-F3：修复环预算单点是 round_cap——终帧必须是「修复环用尽」，下发次数 = ROUND_CAP。

    旧实现里 ROUND_CAP 分支是死码：ask() 的默认 NUDGE_CAP=3 先在「gate」游标上抛
    「gate/-/- 重试超限」，有效修复指令只下发 4 次（< spec 的 5），而旧断言只钉
    round==5 + halted——两种 halt 都满足，没拦住预算被 NUDGE 代管的漂移。
    """
    kb = StubKb()
    env = _env(tmp_path, kb)

    def broken_story(layer, block, mode):
        if layer == "story":
            return {"nodes": [{"op": "upsert", "type": "story", "name": "一直断",
                               "chains": ["ch-9999"], "actor": "a", "preconditions": "p",
                               "trigger": "t", "expected": "e", "priority": "P0"}]}
        return None

    state = drain(env, kb, ScriptTask(), gen_nodes=broken_story, gate_fix=lambda envx: None)
    led = Ledger.load(env.design)
    assert led.status == "halted"
    assert led.data["gate"]["round"] == 5                        # 修复环用尽
    assert "大纲门结构检查连续未清零（修复环用尽）" in _end_text(state["frames"])
    assert "重试超限" not in _end_text(state["frames"])          # NUDGE 不得再代管收口
    fixes = [m for m in state["messages"] if "大纲门修复" in str(m.content)]
    assert len(fixes) == 5                                       # 有效修复指令下发 = ROUND_CAP


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


def test_writeback_counts_untouched_nodes_and_says_it(tmp_path):
    """第二次整轮回写：内容未变的节点 step 报 unchanged，驱动不谎报「全部写入」。

    裁定 30 的呈递面：等值判定单点在 step，驱动只累计 written/untouched 进账本、把尾句说给人看
    （驱动内不加第二次比较）。裁定 31 的底线：基句一字不改，`untouched == 0` 时终帧与现状逐字节相同。
    """
    kb = StubKb(track_content=True)                             # 像真实 step：同内容重复下发才报未触碰
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    frames: list[dict] = []
    _drive(env, {"messages": [HumanMessage("通过，回写")], "case": {}}, ScriptTask(),
           writer=frames.append)
    led = Ledger.load(env.design)
    n = len(kb.upserts)
    assert n == 5 and led.status == "done"                        # 整轮首写：五个节点全落库
    assert led.data["writeback"]["written"] == n
    assert led.data["writeback"]["untouched"] == 0
    assert _end_text(frames) == "回写完成：本次过审节点已写入知识库。"   # 无未触碰时逐字节不变

    # 同一批节点再过一次门（库内内容已一致）：把账本复位成「刚过审、游标在回写」。
    led.cursor.update({"stage": "writeback"})
    for layer in ("chain", "story", "point"):
        led.layer(layer)["state"] = "audited"
    led.status = "awaiting_review"
    led.save()
    frames2: list[dict] = []
    ctx = Ctx(env=env, task_tool=ScriptTask(), writer=frames2.append, config={},
              state_messages=[HumanMessage("通过，回写")], led=led)
    assert h_writeback(ctx)["case"]["route"] == "end"
    led = Ledger.load(env.design)
    assert len(kb.upserts) == 2 * n                                # 下发次数照旧，只是零重写
    assert led.data["writeback"]["written"] == 0
    assert led.data["writeback"]["untouched"] == n
    assert _end_text(frames2) == ("回写完成：本次过审节点已写入知识库。"
                                  f"（{n} 个节点内容与库内一致，未重写。）")


def test_writeback_mixed_round_says_only_the_untouched_subset(tmp_path):
    """混编轮次（走查三的真实形状）：一部分节点改了、一部分没动，尾句只数没动那部分。"""
    kb = StubKb(unchanged_ids={"st-0002", "pt-0002"})
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    frames: list[dict] = []
    _drive(env, {"messages": [HumanMessage("通过，回写")], "case": {}}, ScriptTask(),
           writer=frames.append)
    led = Ledger.load(env.design)
    assert led.data["writeback"]["written"] == 3
    assert led.data["writeback"]["untouched"] == 2
    assert _end_text(frames) == ("回写完成：本次过审节点已写入知识库。"
                                 "（2 个节点内容与库内一致，未重写。）")


def test_writeback_retry_does_not_call_previous_attempt_writes_untouched(tmp_path):
    """I-1（T14 复审）：中途崩的那次尝试真写过的文件，重试轮报 unchanged 也不许说成「未重写」。

    「未重写」对人说的是这一整轮没动过那个文件；把上一尝试的写入算进未触碰，就是拿
    裁定 31 的尾句在唯一那道门上谎报——计数按整轮记，不按最后一次尝试记。
    """
    kb = StubKb(track_content=True, fail_upsert_after=2)         # 前 2 个真落库，第 3 个炸一次
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    frames: list[dict] = []
    _drive(env, {"messages": [HumanMessage("通过，回写")], "case": {}}, ScriptTask(),
           writer=frames.append)
    led = Ledger.load(env.design)
    assert led.status == "done"
    assert len(kb.upserts) == 7                                  # 2 + 整轮 5（重试轮重发全批）
    assert led.data["writeback"]["log"]                           # 失败留痕仍在
    assert led.data["writeback"]["written"] == 5                  # 上一尝试写过的 2 个仍算写入
    assert led.data["writeback"]["untouched"] == 0                # 整轮里五个文件全被动过
    assert _end_text(frames) == "回写完成：本次过审节点已写入知识库。"   # 基句逐字节，无尾句


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
    assert led.data["no_change"] == [{"layer": "chain", "block": "ALL", "reason": ""},
                                     {"layer": "story", "block": "ch-0001", "reason": ""},
                                     {"layer": "point", "block": "st-0001", "reason": ""}]
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
    assert led.data["no_change"] == [{"layer": "point", "block": "st-0001", "reason": ""}]
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


def test_unparseable_reviewer_output_halts_one_line_with_evidence(tmp_path):
    """B-F7 驱动面：围栏解析双败的终帧只有一行中文摘要，原文进 design/reviews 归档证据链。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    task = ScriptTask({"blk-chain-ALL-r0": "这不是围栏",
                       "blk-chain-ALL-r0-r2": "还不是围栏"})
    state = drain(env, kb, task)
    assert Ledger.load(env.design).status == "halted"
    end = _end_text(state["frames"])
    assert "评审子 case_review 两次输出都无法按围栏 JSON 约定解析" in end
    assert "内部错误" in end                                   # 汇入既有 A3 人话收口格式
    assert "validation" not in end.lower() and "Input should be" not in end
    archived = (env.reviews_dir / "blk-chain-ALL-r0.review.md").read_text(encoding="utf-8")
    assert "这不是围栏" in archived and "还不是围栏" in archived
    assert "解析错误" in archived


# ---- 评审修复轮（fix round 1）新增用例 ----

_SIX_HARDS = [
    {"code": "broken_parent", "layer": "story", "where": "st-0001", "detail": "父引用落空"},
    {"code": "cross_level", "layer": "chain", "where": "ch-0002", "detail": "父子层级不连续"},
    {"code": "priority_violation", "layer": "point", "where": "pt-0001", "detail": "子比父更重要"},
    {"code": "unapproved_ref", "layer": "story", "where": "st-0002", "detail": "引用未过审节点"},
    {"code": "empty_chain", "layer": "story", "where": "ch-0001", "detail": "范围内链路无故事认领"},
    {"code": "empty_story", "layer": "point", "where": "st-0001", "detail": "范围内故事无测试点"},
]


def _filter_ctx(tmp_path, states) -> Ctx:
    """直调过滤器用的最小账本：_live_window 的唯一读点就是 layers[layer]["state"]。"""
    env = _env(tmp_path, StubKb())
    led = Ledger.fresh(env.design)
    for layer, state in states.items():
        led.layer(layer)["state"] = state
    led.save()
    return Ctx(env=env, task_tool=ScriptTask(), writer=lambda e: None, config={},
               state_messages=[], led=led)


def test_window_filter_is_identity_when_every_layer_is_live(tmp_path):
    """R-18(c) 边界 1：三层都进了本 run 有效宇宙且点层块非空 → 过滤器必须是恒等映射。

    钉住「过滤器只会更宽」这条漂移：把它换成「empty_chain/empty_story 无条件全丢」
    或「谁都不丢」，本用例都会红。
    """
    ctx = _filter_ctx(tmp_path, {"chain": "audited", "story": "audited", "point": "audited"})
    scope = {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}
    kept, exempted = _drop_out_of_window_hards(ctx, [dict(f) for f in _SIX_HARDS], scope)
    assert [f["code"] for f in kept] == [f["code"] for f in _SIX_HARDS]
    assert exempted == []


def test_window_filter_drops_only_empty_story_when_point_layer_dead(tmp_path):
    """R-18(c) 边界 2：点层被跳过/失效 → 只丢 empty_story，其余五项（含 empty_chain）全留。"""
    for dead in ("skipped", "stale_pending"):
        ctx = _filter_ctx(tmp_path / dead, {"chain": "audited", "story": "audited",
                                            "point": dead})
        scope = {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}
        kept, exempted = _drop_out_of_window_hards(ctx, [dict(f) for f in _SIX_HARDS], scope)
        assert [f["code"] for f in exempted] == ["empty_story"], dead
        assert [f["code"] for f in kept] == ["broken_parent", "cross_level", "priority_violation",
                                             "unapproved_ref", "empty_chain"], dead


def test_window_filter_drops_empty_story_outside_point_blocks(tmp_path):
    """R-18(c) 边界 3：同为 empty_story，只有「不在点层计划块里」的那条被豁免。

    点层活着但某故事本 run 没有点块 = 没要求它出点（与 _scope_for_checks 同源判据）。
    """
    ctx = _filter_ctx(tmp_path, {"chain": "audited", "story": "audited", "point": "audited"})
    scope = {"chains": {"ch-0001"}, "stories": {"st-0001"}}
    hard = [{"code": "empty_story", "layer": "point", "where": "st-0001", "detail": "有点块"},
            {"code": "empty_story", "layer": "point", "where": "st-0002", "detail": "无点块"}]
    kept, exempted = _drop_out_of_window_hards(ctx, hard, scope)
    assert [f["where"] for f in kept] == ["st-0001"]
    assert [f["where"] for f in exempted] == ["st-0002"]


def test_phantom_target_subtree_halts_before_any_work(tmp_path):
    """契约 §7（I-4/M-1）：幻影 target_subtree 必须在下发任何生成/评审之前终止并报因。

    原实现只在 _enter_layer 守卫，而 owner 层被 R5 置 skipped 时入口层 chains=["ALL"] 绕过守卫，
    整层真实评审调用与草稿先被烧掉才 halt。
    """
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "name": "根链", "parent": "", "level": 1,
                   "priority": "P0"}],
        "story": [{"id": "st-0001", "type": "story", "name": "老故事", "chains": ["ch-0001"],
                   "priority": "P0"}],
        "point": [{"id": "pt-0001", "type": "point", "name": "老点", "story": "st-0001",
                   "entities": ["实体甲"], "directions": ["正向"], "priority": "P0"}]})
    env = _env(tmp_path, kb)
    task = ScriptTask()
    frames: list[dict] = []
    state = {"messages": [HumanMessage("针对故事 st-9999 生成测试设计")], "case": {}}
    turn = _drive(env, state, task)
    _append(state, turn)
    simulate(env, plan={"task_kind": "design", "entry_layer": "chain", "terminal_layer": "point",
                        "target_subtree": "st-9999", "source_files": [], "note": "窄范围"})
    turn = _drive(env, state, task, writer=frames.append)
    assert turn["case"]["route"] == "end"
    assert "目标子树「st-9999」必须是链路（ch-）id" in _end_text(frames)   # B-F5 新口径
    assert Ledger.load(env.design).status == "halted"
    assert task.calls == []                                    # 零评审/LLM 调用
    for layer in ("chain", "story", "point"):                  # 零草稿制品
        assert list(env.drafts_dir(layer).glob("*.json")) == []


def test_plan_instruction_limits_target_subtree_to_chain_ids(tmp_path):
    """B-F5：提示词侧要与预检侧同一口径——target_subtree 只收链路 id，指故事/点要改填所属链。"""
    env = _env(tmp_path, StubKb())
    turn = _drive(env, {"messages": [HumanMessage("按业务信息生成测试设计")], "case": {}},
                  ScriptTask())
    text = str(turn["messages"][0].content)
    assert "target_subtree 只接受链路（ch-）id" in text
    assert "填其所属链路 id" in text


def test_phantom_subtree_halt_text_distinguishes_two_cases(tmp_path):
    """B-F5：预检终帧分两种情形——非链路形状 id 给改填指引（对真实存在的故事 id 说
    「链路树里不存在」是误导）；链路形状且真查无此节点才保持现文。时机与零烧钱性质不动。
    """
    cases = (("st-0002", "目标子树「st-0002」必须是链路（ch-）id"),
             ("ch-9999", "目标子树「ch-9999」在链路树里不存在"))
    for sub, expect in cases:
        kb = StubKb(layers={
            "chain": [{"id": "ch-0001", "type": "chain", "name": "根链", "parent": "",
                       "level": 1, "priority": "P0"}],
            "story": [{"id": "st-0002", "type": "story", "name": "老故事",
                       "chains": ["ch-0001"], "priority": "P0"}],
            "point": []})
        env = _env(tmp_path / f"phantom-{sub}", kb)
        task = ScriptTask()
        frames: list[dict] = []
        state = {"messages": [HumanMessage("针对某节点生成测试设计")], "case": {}}
        turn = _drive(env, state, task)
        _append(state, turn)
        simulate(env, plan={"task_kind": "design", "entry_layer": "chain",
                            "terminal_layer": "point", "target_subtree": sub,
                            "source_files": [], "note": "窄范围"})
        turn = _drive(env, state, task, writer=frames.append)
        assert turn["case"]["route"] == "end", sub
        assert expect in _end_text(frames), sub
        assert Ledger.load(env.design).status == "halted", sub
        assert task.calls == [], sub                           # 零烧钱不变


def test_narrow_subtree_closure_keeps_sibling_chain_but_not_its_subtree(tmp_path):
    """契约 §3（M-3）：影响闭包带「同父兄弟（接缝）」，但不带兄弟的整棵子树。

    窄任务的宇宙必须仍然窄——兄弟子树被拖进来就等于全 KB，与「审按范围缩」相悖；
    大纲里可见的接缝只有兄弟节点本身。
    """
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "name": "根链", "parent": "", "level": 1,
                   "priority": "P0"},
                  {"id": "ch-0002", "type": "chain", "name": "目标链", "parent": "ch-0001",
                   "level": 2, "priority": "P0"},
                  {"id": "ch-0003", "type": "chain", "name": "兄弟链", "parent": "ch-0001",
                   "level": 2, "priority": "P0"},
                  {"id": "ch-0004", "type": "chain", "name": "兄弟子链", "parent": "ch-0003",
                   "level": 3, "priority": "P0"}],
        "story": [{"id": "st-0001", "type": "story", "name": "目标故事", "chains": ["ch-0002"],
                   "priority": "P0"},
                  {"id": "st-0003", "type": "story", "name": "兄弟故事", "chains": ["ch-0003"],
                   "priority": "P0"},
                  {"id": "st-0009", "type": "story", "name": "兄弟子树故事", "chains": ["ch-0004"],
                   "priority": "P0"}],
        "point": [{"id": "pt-0001", "type": "point", "name": "目标点", "story": "st-0001",
                   "entities": ["实体甲"], "directions": ["正向"], "priority": "P0"},
                  {"id": "pt-0003", "type": "point", "name": "兄弟点", "story": "st-0003",
                   "entities": ["实体甲"], "directions": ["正向"], "priority": "P0"},
                  {"id": "pt-0009", "type": "point", "name": "兄弟子树点", "story": "st-0009",
                   "entities": ["实体甲"], "directions": ["正向"], "priority": "P0"}]})
    env = _env(tmp_path, kb)
    task = ScriptTask({"claims-story-r*": _j({"claims": [
        {"ref": "st-0010-a1", "verdict": "covered", "owner": "st-0011", "note": "另一故事认领"}],
        "opinions": []})})
    drain(env, kb, task, plan={"task_kind": "design", "entry_layer": "chain",
                               "terminal_layer": "point", "target_subtree": "ch-0002",
                               "source_files": [], "note": "窄范围"})
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"
    outline = (env.design / "outline.md").read_text(encoding="utf-8")
    assert "目标链" in outline and "根链" in outline          # 草稿 + 上游祖先链
    assert "兄弟链" in outline                               # 同父兄弟（接缝）可见
    for gone in ("兄弟子链", "ch-0004", "兄弟子树故事", "st-0009", "兄弟子树点", "pt-0009"):
        assert gone not in outline
    assert "hard 豁免" not in outline                        # 无豁免时那行不出（只在非空时渲染）


def test_writeback_dispatches_each_delete_once(tmp_path):
    """M-4/M-5：delete 分支（唯一具破坏性的 KB 写）+ 同一节点跨块重复只下发一次。"""
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "name": "根链", "parent": "", "level": 1,
                   "priority": "P0"}],
        "story": [{"id": "st-0001", "type": "story", "name": "老故事", "chains": ["ch-0001"],
                   "priority": "P0"}],
        "point": [{"id": "pt-0001", "type": "point", "name": "老点", "story": "st-0001",
                   "entities": ["实体甲"], "directions": ["正向"], "priority": "P0"}]})
    env = _env(tmp_path, kb)

    def gen(layer, block, mode):
        if layer == "chain":
            return {"nodes": [{"op": "upsert", "type": "chain", "name": "新增链路甲", "level": 1,
                               "parent": "", "business_scope": "范围甲", "excluded": "",
                               "priority": "P0"}]}
        if layer == "story":
            return {"nodes": [{"op": "upsert", "type": "story", "name": f"故事 {block}",
                               "chains": [block], "actor": "使用者", "preconditions": "已登录",
                               "trigger": "提交", "expected": "成功", "priority": "P0"}]}
        # 每个点块都重复表达同一条删除（同层多块重复是合法输入形状）
        return {"nodes": [
            {"op": "upsert", "type": "point", "name": f"{block} 正向", "story": block,
             "scenario": "已登录时提交", "entities": ["实体甲"], "directions": ["正向"],
             "priority": "P0"},
            {"op": "delete", "type": "point", "id": "pt-0001", "name": "重复点",
             "reason": "与新增点重复"}]}

    drain(env, kb, ScriptTask(), gen_nodes=gen)
    assert Ledger.load(env.design).status == "awaiting_review"
    assert kb.upserts == [] and kb.deletes == []             # 契约 §14：人审之前零 KB 写
    outline = (env.design / "outline.md").read_text(encoding="utf-8")
    assert sum(1 for line in outline.splitlines()
               if "（删除：" in line) == 1                    # 人类门只看到一条删除项
    turn = _drive(env, {"messages": [HumanMessage("通过")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert turn["case"]["route"] == "end" and led.status == "done"
    assert kb.deletes == [("point", "pt-0001")]              # 只下发一次
    assert len(kb.upserts) == 6                               # 1 链 + 2 故事 + 3 点


def test_no_change_record_withdrawn_when_block_gains_nodes(tmp_path):
    """M-6：块先以 no_change 过账、之后被优化/回溯写进真实节点时，「本次无变化块」要撤回。

    不撤回 = 大纲说这块没动、回写却在推它，两处自相矛盾（人审据大纲把关）。
    """
    env = _env(tmp_path, StubKb())
    led = Ledger.fresh(env.design)
    led.data["no_change"] = [{"layer": "story", "block": "ch-0001"},
                             {"layer": "story", "block": "ch-0002"}]
    led.layer("story")["blocks"] = [{"id": "ch-0001", "state": "done", "round": 0},
                                    {"id": "ch-0002", "state": "done", "round": 0}]
    led.save()
    ctx = Ctx(env=env, task_tool=ScriptTask(), writer=lambda e: None, config={},
              state_messages=[], led=led)
    _write(env.drafts_dir("story") / "ch-0001.json",
           {"layer": "story", "block": "ch-0001", "nodes": [
               {"op": "upsert", "type": "story", "name": "补回的故事", "chains": ["ch-0001"],
                "actor": "使用者", "preconditions": "已登录", "trigger": "提交",
                "expected": "成功", "priority": "P1", "id": ""}]})
    _write(env.drafts_dir("story") / "ch-0002.json",
           {"layer": "story", "block": "ch-0002", "nodes": [], "note": "no_change"})
    _patch_ids(ctx, "story", "")                              # h_opt / h_writeback 的收口钩子
    # 人手造账本条目（不经 h_gen，无 reason 键）：按键比较整 dict，撤回判据仍只看 (layer, block)
    assert [{k: e[k] for k in ("layer", "block")}
            for e in ctx.led.data["no_change"]] == [{"layer": "story", "block": "ch-0002"}]
    draft = json.loads((env.drafts_dir("story") / "ch-0001.json").read_text(encoding="utf-8"))
    assert draft["nodes"][0]["id"] == "st-0001"               # 顺带钉新节点补 id


def test_human_gate_requires_explicit_approval(tmp_path):
    """I-5：回写不可逆，批准必须明示——「解读子没有意见」不等于「人说了通过」。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    frames: list[dict] = []
    turn = _drive(env, {"messages": [HumanMessage("先别动，我再看看")], "case": {}},
                  ScriptTask(), writer=frames.append)
    assert turn["case"]["route"] == "agent"                  # 待决：指令交回主智能体转述
    text = str(turn["messages"][0].content)
    assert "不回写知识库" in text and "不要代替人给出批准" in text
    assert frames == []                                      # 待决不冒充终帧
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"                   # 本轮不做任何决定
    assert kb.upserts == [] and kb.deletes == []
    assert led.data["gate"]["unclear"] == 1
    turn = _drive(env, {"messages": [HumanMessage("通过")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert turn["case"]["route"] == "end" and led.status == "done"
    assert len(kb.upserts) == 5 and kb.deletes == []         # 明示批准才回写


def test_human_gate_negated_words_are_not_approval(tmp_path):
    """I-5 裁定补刀：批准措辞落在否定/延后句里不是授权——「不通过」里也含着「通过」。

    失败方向只能偏保守（待决：零写、状态不动），因为另一侧的代价是一次不可逆的回写。
    """
    for i, text in enumerate(("不通过", "先别回写", "暂不批准", "没确认")):
        kb = StubKb()
        env = _env(tmp_path / f"neg-{i}", kb)
        drain(env, kb, ScriptTask())
        frames: list[dict] = []
        turn = _drive(env, {"messages": [HumanMessage(text)], "case": {}},
                      ScriptTask(), writer=frames.append)
        assert turn["case"]["route"] == "agent", text
        assert frames == [], text                             # 待决不冒充终帧
        led = Ledger.load(env.design)
        assert led.status == "awaiting_review", text
        assert led.data["gate"]["unclear"] == 1, text
        assert kb.upserts == [] and kb.deletes == [], text


def test_human_gate_approves_only_this_turn_message(tmp_path):
    """I-5 裁定补刀：批准只认本轮那条人话——上一轮已被处置过的措辞不授权本轮的不可逆写。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    frames: list[dict] = []
    turn = _drive(env, {"messages": [HumanMessage("通过"),
                                     AIMessage(content="大纲已生成，等待人工评审。"),
                                     HumanMessage("稍等")],
                        "case": {}}, ScriptTask(), writer=frames.append)
    assert turn["case"]["route"] == "agent"
    assert frames == []
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review" and led.data["gate"]["unclear"] == 1
    assert kb.upserts == [] and kb.deletes == []


def test_human_gate_undecided_beyond_nudge_cap_halts(tmp_path):
    """I-5 失败模式收口：连续待决问到 NUDGE_CAP 之上即 halted，绝不猜批准。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    frames: list[dict] = []
    for _ in range(4):                                       # NUDGE_CAP=3 → 第 4 次待决
        turn = _drive(env, {"messages": [HumanMessage("我再想想")], "case": {}},
                      ScriptTask(), writer=frames.append)
    assert turn["case"]["route"] == "end"
    assert "人审门连续未给出可执行意见也未明示批准" in _end_text(frames)
    led = Ledger.load(env.design)
    assert led.status == "halted"
    assert kb.upserts == [] and kb.deletes == []


# ---- 裁定 25（复审回执不覆盖处置说明）----

def test_re_review_note_keeps_disposition_note_in_order(tmp_path):
    """双文保留：处置说明（h_opt）在前、复审回执（_apply_resolutions）以「复审：」缀在后。

    意见落点对照表是人审门的主要读物，承载「改了什么」；评审子的一句回执不得把它顶掉。
    旧实现 `op["note"] = r.note or op["note"]` 会把「已改」覆盖成「已补 ch-0002」。
    """
    kb = StubKb()
    env = _env(tmp_path, kb)
    task = ScriptTask({
        "blk-chain-ALL-r0": _j({"opinions": [{"target": {"type": "node", "value": "ch-0001"},
            "kind": "颗粒度", "ask": "补充退款子链路", "evidence": "design/drafts/chain/ALL.json"}],
            "resolutions": []}),
        "blk-chain-ALL-r1": _j({"opinions": [], "resolutions": [
            {"ref": "op-01", "resolved": True, "note": "已补 ch-0002"}]}),
    })
    drain(env, kb, task, plan={"task_kind": "design", "entry_layer": "chain",
                               "terminal_layer": "chain", "target_subtree": "",
                               "source_files": [], "note": "只链层"})
    op = Ledger.load(env.design).layer("chain")["opinions"][0]
    assert op["ref"] == "op-01" and op["resolved"] is True
    assert op["note"] == "已改；复审：已补 ch-0002"              # simulate 的处置说明仍在最前
    assert "已改；复审：已补 ch-0002" in (env.design / "outline.md").read_text(encoding="utf-8")


# ---- 裁定 28（处置侧回执轨迹对称保真）----

_FIRST_NOTE = "已增补 ch-0002 退款链路"
_SECOND_NOTE = "口径已落到 ch-0002 的 business_scope"


def _opt_rows(layer, block, round_no, refs):
    """两轮处置表给**不同**的说明，且第二轮把上一轮的 ref 一并重申（主智能体常见写法）。

    op-01 首轮 fixed 后已销账，复审再犯同一 key 即另登 op-02；第二轮处置表覆盖 op-02，
    并顺带把 op-01 的处置说明又写了一遍——旧实现会把 op-01 已累积的轨迹整串顶掉。
    """
    assert layer == "chain" and block == "ALL"
    if round_no == 0:
        assert [r["ref"] for r in refs] == ["op-01"]
        return [{"ref": "op-01", "status": "fixed", "note": _FIRST_NOTE}]
    assert round_no == 1 and [r["ref"] for r in refs] == ["op-02"]
    return [{"ref": "op-02", "status": "fixed", "note": _SECOND_NOTE},
            {"ref": "op-01", "status": "covered", "note": _SECOND_NOTE}]


def test_disposition_note_keeps_re_review_trail_in_order(tmp_path):
    """裁定 28（与裁定 25 对称）：新一轮处置说明缀在既有轨迹之后，不整串顶掉「处置＋复审」轨迹。

    旧实现 `op["note"] = note or op["note"]` 将
    「已增补 ch-0002 退款链路；复审：口径已对齐」整串换成第二轮的处置说明——唯一人审门
    就此看不见这条意见第一轮改了什么。
    """
    kb = StubKb()
    env = _env(tmp_path, kb)
    task = ScriptTask({
        "blk-chain-ALL-r0": _j({"opinions": [{"target": {"type": "node", "value": "ch-0001"},
            "kind": "颗粒度", "ask": "补充退款子链路", "evidence": "design/drafts/chain/ALL.json"}],
            "resolutions": []}),
        "blk-chain-ALL-r1": _j({"opinions": [{"target": {"type": "node", "value": "ch-0001"},
            "kind": "颗粒度", "ask": "补充退款子链路", "evidence": "design/drafts/chain/ALL.json"}],
            "resolutions": [{"ref": "op-01", "resolved": False, "note": "口径已对齐"}]}),
    })
    drain(env, kb, task, plan={"task_kind": "design", "entry_layer": "chain",
                               "terminal_layer": "chain", "target_subtree": "",
                               "source_files": [], "note": "只链层"}, opt_fix=_opt_rows)
    ops = {o["ref"]: o for o in Ledger.load(env.design).layer("chain")["opinions"]}
    assert ops["op-01"]["note"] == f"{_FIRST_NOTE}；复审：口径已对齐；处置：{_SECOND_NOTE}"
    assert ops["op-02"]["note"] == _SECOND_NOTE                  # 首轮无轨迹：直接用新说明
    assert f"{_FIRST_NOTE}；复审：口径已对齐" in (env.design / "outline.md").read_text(
        encoding="utf-8")                                         # 轨迹整条落在人读的那张表里


# ---- 裁定 27 + B-F1（回写续跑：先解读本轮人话，再决定回写还是退回大纲门） ----

# 纵深防御闸门的拒答文案（B-F1 收口）：承诺的转场如今真实存在——_boot 会把未授权
# 消息分流进 gate_interpret，「把意见写在消息里」确实会退回大纲门重做。
_RETRY_REFUSAL = ("回写未执行：本轮没有重试授权——回复「重试」继续回写；"
                  "把意见写在消息里即可退回大纲门重做。")


def _to_writeback_failed(tmp_path: Path):
    """复现「人已明示批准、回写整体失败」的账本（fail_upserts=3：1+2 次尝试全落空）。"""
    kb = StubKb(fail_upserts=3)
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    _drive(env, {"messages": [HumanMessage("通过")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert led.status == "writeback_failed" and led.data["writeback"]["log"]
    assert kb.upserts == [] and kb.deletes == []               # 失败轮自己一条也没写进去
    return env, kb


def test_writeback_resume_mixed_approval_and_opinion_goes_through_interpret(tmp_path):
    """B-F1 窄钉①：批准+意见混写绝不许被当成纯授权直接不可逆回写。

    「同意，把 st-0002 拆成两条」旧实现下 _boot 从不解读本轮人话，h_writeback 的闸门
    只认「同意」措辞 ⇒ 仿真实测：status done、写库 0→5 次、human-* 制品 0 个、意见静默
    丢弃。修复后必须走 gate_interpret：意见登记、目标层及下游失效、零 KB 写。
    """
    env, kb = _to_writeback_failed(tmp_path)
    # int_round 已在「通过」那次解读用掉 r1：本轮人审解读的 call_id 是 gate-int-r2。
    task = ScriptTask({"gate-int-r2": _j({"opinions": [{
        "target": {"type": "node", "value": "st-0002"}, "kind": "颗粒度",
        "ask": "拆成两条", "evidence": "同意，把 st-0002 拆成两条"}],
        "resolutions": []})})
    turn = _drive(env, {"messages": [HumanMessage("同意，把 st-0002 拆成两条")],
                         "case": {}}, task)
    assert turn["case"]["route"] == "agent"
    assert "human-story-in-r2.json" in turn["messages"][0].content
    assert any("把 st-0002 拆成两条" in c["brief"] for c in task.calls)  # 本轮人话被解读
    assert "gate-int-r2" in task.call_ids()
    assert kb.upserts == [] and kb.deletes == []               # 不可逆写零次
    assert list(env.reviews_dir.glob("human-*"))              # 意见登记制品 ≥1
    led = Ledger.load(env.design)
    assert led.status != "done"
    assert led.layer("point")["state"] == "stale_pending"      # 目标层下游失效


def test_writeback_resume_bare_retry_skips_interpret(tmp_path):
    """B-F1 窄钉②：裸「重试」直回写、gate_interpret 零调用——不白烧一次评审子调用。"""
    env, kb = _to_writeback_failed(tmp_path)
    task = ScriptTask()
    turn = _drive(env, {"messages": [HumanMessage("重试")], "case": {}}, task)
    led = Ledger.load(env.design)
    assert turn["case"]["route"] == "end" and led.status == "done"
    assert len(kb.upserts) == 5 and kb.deletes == []
    assert [c for c in task.call_ids() if c.startswith("gate-int")] == []


def test_writeback_resume_negation_never_writes_and_goes_to_interpret(tmp_path):
    """B-F1 窄钉③（承接 R-27）：否定句零写入的底线不许动；去向改为 gate_interpret。

    old：route end + 「回写未执行…回复意见则回到大纲门」——但旧 _boot 根本不存在
    writeback_failed→gate 的转场，那句承诺是假的（人反复回意见就反复撞同一句话）。
    new：未授权消息分流进 gate_interpret；本轮无意见也无明示批准 ⇒ 待决转述
    （route agent、零终帧、零写入），承诺由转场事实背书。
    """
    env, kb = _to_writeback_failed(tmp_path)
    frames: list[dict] = []
    turn = _drive(env, {"messages": [HumanMessage("别写了，先停下")], "case": {}},
                  ScriptTask(), writer=frames.append)
    assert turn["case"]["route"] == "agent"
    assert frames == []                                       # 待决不冒充终帧
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"                     # 不再钉死在 writeback_failed
    assert kb.upserts == [] and kb.deletes == []               # R-27 底线：绝不写库
    assert [led.layer(x)["state"] for x in ("chain", "story", "point")] == ["audited"] * 3


def test_h_writeback_entry_gate_refuses_without_authorization(tmp_path):
    """纵深防御：绕过 _boot 直接把游标按在 writeback 上，入口闸门依旧零写入、原样退回。

    h_writeback 的闸门是回写不可逆写的最后防线（唯一真正下笔处），R-27 原样保留——
    _boot 分流只是不再让未授权消息走到这里，不是拆掉这道闸。
    """
    env, kb = _to_writeback_failed(tmp_path)
    led = Ledger.load(env.design)
    led.cursor.update({"stage": "writeback"})
    frames: list[dict] = []
    ctx = Ctx(env=env, task_tool=ScriptTask(), writer=frames.append, config={},
              state_messages=[HumanMessage("先别写")], led=led)
    result = h_writeback(ctx)
    assert result["case"]["route"] == "end"
    assert _end_text(frames) == _RETRY_REFUSAL
    assert kb.upserts == [] and kb.deletes == []
    assert Ledger.load(env.design).status == "writeback_failed"
    assert [led.layer(x)["state"] for x in ("chain", "story", "point")] == ["audited"] * 3


def test_writeback_resume_accepts_retry_word(tmp_path):
    """正对照：同一个入口换「重试」就该写全——不然上一条测试是假绿。"""
    env, kb = _to_writeback_failed(tmp_path)
    turn = _drive(env, {"messages": [HumanMessage("重试")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert turn["case"]["route"] == "end" and led.status == "done"
    assert len(kb.upserts) == 5 and kb.deletes == []
    assert [led.layer(x)["state"] for x in ("chain", "story", "point")] == ["done"] * 3


def test_writeback_resume_negated_words_are_not_authorization(tmp_path):
    """R-27 补刀：否定/延后标记先判——「先不用重试」里含着「重试」也不是授权（零写入）。

    old：route end + 拒答终帧（writeback_failed 原地不动）；new：_boot 分流后未授权
    消息进 gate_interpret，待决转述（route agent）——零写入底线不变。
    """
    for i, text in enumerate(("先不用重试", "别重试", "不重试", "暂不回写")):
        env, kb = _to_writeback_failed(tmp_path / f"wb-neg-{i}")
        frames: list[dict] = []
        turn = _drive(env, {"messages": [HumanMessage(text)], "case": {}},
                      ScriptTask(), writer=frames.append)
        assert turn["case"]["route"] == "agent", text          # 未授权 → gate_interpret 待决
        assert frames == [], text
        assert kb.upserts == [] and kb.deletes == [], text


# ---- R-31 / B-F2（interrupted 假闭环清偿）----

def test_interrupted_resume_appends_history_trace(tmp_path):
    """上一回合未跑完的续跑必须在磁盘账本留痕：死赋值「interrupted」从未落盘是假闭环。

    裁定最小形态：删死赋值，改往账本既有 history 追加带时间戳痕迹，仍由既有 save 单点
    落盘；不新增状态词、不新增字段。全新首启（无既有账本）不是「被打断」，不得留痕。
    """
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    turn = _drive(env, state, ScriptTask())                    # 首启：下发 plan 指令后回合结束
    assert turn["case"]["route"] == "agent"
    raw = json.loads((env.design / "ledger.json").read_text(encoding="utf-8"))
    assert raw["status"] == "active" and raw["history"] == []  # 全新首启：零痕迹
    _drive(env, {"messages": [HumanMessage("继续")], "case": {}}, ScriptTask())
    raw = json.loads((env.design / "ledger.json").read_text(encoding="utf-8"))
    assert raw["status"] == "active"                           # 终态 active（可观测六态不再造假）
    assert len(raw["history"]) == 1
    assert raw["history"][0].startswith("resumed-from-interrupted@")
    assert re.match(r"^resumed-from-interrupted@\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$",
                    raw["history"][0])


# ---- B-F6（ledger.json 损坏自愈）----

def test_corrupt_ledger_self_heals_with_evidence(tmp_path):
    """损坏账本不再把每一轮都掀成「内部错误」死循环：改名留证 + 按无账本走 + 新账本可用。

    留证是硬要求——_ARCHIVE_ITEMS 不含 ledger.json，坏文件若被删或被改写就等于连
    现场都没了；原字节必须一字不改地躺在 design/ledger.corrupt-<ts>.json 里。
    """
    kb = StubKb()
    env = _env(tmp_path, kb)
    (env.design / "ledger.json").write_text("{corrupt", encoding="utf-8")
    frames: list[dict] = []
    turn = _drive(env, {"messages": [HumanMessage("生成测试设计")], "case": {}},
                  ScriptTask(), writer=frames.append)
    assert turn["case"]["route"] == "agent"                    # 一轮内即恢复：下发 plan 指令
    assert "design/plan.json" in str(turn["messages"][0].content)
    assert "内部错误" not in str(frames)                        # 不再抛 JSON 语法错收口
    corrupts = sorted(env.design.glob("ledger.corrupt-*.json"))
    assert len(corrupts) == 1
    assert corrupts[0].read_text(encoding="utf-8") == "{corrupt"  # 原字节留证、不删
    led = Ledger.load(env.design)
    assert led is not None and led.status == "active"          # 新账本可用


# ---- B-F4（待决转述轮不得二次呈递「大纲已生成」终帧）----

def test_gate_undecided_relay_turn_does_not_reannounce(tmp_path):
    """人审待决转述后智能体无工具调用回到 driver：游标仍是 gate，h_gate 再跑会重写
    大纲并再发一条「大纲已生成」终帧——人本轮的话没被答复却收到第二条呈递。

    守卫在 h_gate 入口（awaiting_review 且本轮重算 hard 为空 ⇒ 静默交回等待态）；
    不许改成回 gate_interpret——那会重复解读、重复烧评审子调用并可能重复登记意见。
    """
    kb = StubKb()
    env = _env(tmp_path, kb)
    task = ScriptTask()
    drain(env, kb, task)                                       # 呈递一次 → awaiting_review
    relay = {"messages": [HumanMessage("我再想想")], "case": {}}
    frames_a: list[dict] = []
    turn = _drive(env, relay, task, writer=frames_a.append)    # 待决轮：下发转述指令
    assert turn["case"]["route"] == "agent" and frames_a == []
    _append(relay, turn)
    relay["messages"].append(AIMessage(content="已向人转述：等待明确批准或意见"))
    frames_b: list[dict] = []
    turn2 = _drive(env, relay, task, writer=frames_b.append)   # 无工具调用重入 driver
    assert turn2["case"]["route"] == "end"
    assert frames_b == []                                      # 零终帧：不再呈递「大纲已生成」
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"
    assert led.data["gate"]["int_round"] == 1                  # 解读轮不被二次推进
    assert [c for c in task.call_ids() if c.startswith("gate-int")] == ["gate-int-r1"]
    assert kb.upserts == [] and kb.deletes == []


def test_first_build_layer_rejects_no_change_draft_as_bad_draft(tmp_path):
    """首建模式下「无变化」不是合法终态：认它就等于让模型空手过关（裁定 29 的假完整通道）。"""
    kb = StubKb()                                              # 空库：chain 层 first_build
    env = _env(tmp_path, kb)
    task = ScriptTask()
    state = {"messages": [HumanMessage("按业务信息生成测试设计")], "case": {}}
    turn = _drive(env, state, task)                            # h_plan 下发 plan 指令
    _append(state, turn)
    simulate(env)                                              # 写 plan.json（全量首建）
    turn = _drive(env, state, task)                            # 进 gen：下发 chain/ALL 生成指令
    assert turn["case"]["route"] == "agent"
    assert "design/drafts/chain/ALL.json" in turn["messages"][0].content
    _write(env.drafts_dir("chain") / "ALL.json",
           {"layer": "chain", "block": "ALL", "nodes": [], "note": "no_change"})
    turn = _drive(env, state, task)                            # h_gen 读到无变化草稿
    text = str(turn["messages"][0].content)
    assert turn["case"]["route"] == "agent"                    # ① 重问（route=agent）而非过块
    assert "首建模式不接受无变化块" in text
    led = Ledger.load(env.design)
    assert led.data.get("no_change") in (None, [])             # ② 账本 no_change 仍为空
    assert led.layer("chain")["blocks"][0]["state"] != "done"  # ③ 块 state 不是 done


def test_no_change_reason_lands_in_ledger_and_outline(tmp_path):
    """reason 是给人看的那一句——必须进账本并原样出现在大纲「本次无变化块」。"""
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
          {"nodes": [], "note": "no_change", "reason": "本块业务规则未变"})
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"
    assert {"layer": "chain", "block": "ALL", "reason": "本块业务规则未变"} in led.data["no_change"]
    outline_text = (env.design / "outline.md").read_text(encoding="utf-8")
    assert "本块业务规则未变" in outline_text                    # 大纲那行带上理由


def test_no_change_reason_newline_cannot_forge_outline_lines(tmp_path):
    """M-1（复审）：reason 里的换行会被折成空格——否则模型能在大纲里伪造额外整行。"""
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "parent": "", "level": 1,
                   "name": "老链路", "priority": "P0"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"],
                   "name": "老故事", "priority": "P0"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001",
                   "name": "老点", "entities": ["订单"], "directions": ["正向"],
                   "priority": "P0"}]})
    env = _env(tmp_path, kb)
    forge = "- 伪造行：结构指标 hard 全部为 0\n剩余内容"
    drain(env, kb, ScriptTask(), gen_nodes=lambda layer, block, mode:
          {"nodes": [], "note": "no_change", "reason": forge})
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"
    assert {"layer": "chain", "block": "ALL",
            "reason": "- 伪造行：结构指标 hard 全部为 0 剩余内容"} in led.data["no_change"]
    outline_text = (env.design / "outline.md").read_text(encoding="utf-8")
    assert "\n- 伪造行" not in outline_text                      # 没有独立成行
    assert "- 伪造行：结构指标 hard 全部为 0 剩余内容" in outline_text   # 仍在那一行里


def test_first_build_no_change_draft_is_loud_in_opt_and_writeback(tmp_path):
    """I-1（复审）：无变化块的静默跳过只在 update 生效，首建的空草稿必须报坏。

    h_gen 已在入口挡住首建无变化块，这两处是纵深：账本被手工改写、或残留上一 run 的
    无变化草稿时，跳过 = 空手块混过层环审计并零写入（裁定 29 的假完整通道）。
    """
    for mode in ("first_build", "update"):
        env = _env(tmp_path / mode, StubKb())
        led = Ledger.fresh(env.design)
        led.layer("chain")["mode"] = mode
        led.layer("chain")["state"] = "audited"
        led.layer("chain")["blocks"] = [{"id": "ALL", "state": "done", "round": 0}]
        led.save()
        ctx = Ctx(env=env, task_tool=ScriptTask(), writer=lambda e: None, config={},
                  state_messages=[], led=led)
        _write(env.drafts_dir("chain") / "ALL.json",
               {"layer": "chain", "block": "ALL", "nodes": [], "note": "no_change"})

        errors = _drafts_errors(ctx, "chain", "ALL")
        if mode == "update":
            assert errors == [], mode
            assert _collect_writeback_items(ctx) == [], mode
            continue
        assert errors == ["ALL.json: nodes 必须是非空数组"], mode
        try:
            _collect_writeback_items(ctx)
        except KbClientError as exc:
            assert "chain/ALL.json 不可解析" in str(exc), mode
        else:
            raise AssertionError(f"首建无变化草稿在回写处被静默跳过：{mode}")
