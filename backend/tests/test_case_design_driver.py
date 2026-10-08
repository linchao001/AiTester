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
from aitester.case_design.outline import compose_outline
from aitester.case_design.reviewers import _RETRY_HINT
from aitester.case_design.schema import ClaimsOut, parse_json_fence
from aitester.case_design.stages import (
    Ctx, _claims_brief, _collect_writeback_items, _drafts_errors, _drop_out_of_window_hards,
    _explicit_approval, _layer_audited, _next_step_text, _open_of, _outline_extras,
    _patch_ids, _rescan_claims, _writeback_authorized, drive_turn, h_writeback,
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


def _cases_payload(chain: str, batch: str, points: list[str]) -> dict:
    """一条点一条用例（1:1 是合法比例之一，裁定 38）；id 留空串交给驱动补号。

    编写环挂具的默认正文生成器与用例现场必须是同一份代码（抄第二份就是「重复逻辑」红线：
    两份一旦漂开，仿真写的批与断言吃的批就不是同一形状），故定义在挂具文件里。
    """
    return {"chain": chain, "batch": batch, "cases": [
        {"case_id": "", "title": f"用例·{pid}", "covers": [pid],
         "preconditions": "账号已登录且购物车有一件可售商品",
         "steps": ["登录并进入下单页", "提交订单"],
         "expected": ["订单金额按该点场景的规则计算"],
         "priority": "P1", "note": ""} for pid in points]}


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
    # ---- 第四层编写环：与三层同名分支逐条对齐；批 id 在游标 block、链路在 layer（挂具不猜，读账本）。
    # case_opt 的文件名前缀走 `_case_opt_prefix` 的同一套规则（case- / human-case-），
    # 坏文件、坏处置表一律由用例自己直接落盘复现——挂具只负责「顺从的主智能体」。----
    if stage == "case_gen":
        raw = json.loads((env.manifests_dir / f"case-{block}.json").read_text(encoding="utf-8"))
        _write(env.cases_dir() / f"{block}.json",
               _cases_payload(cur["layer"], block, [str(p["id"]) for p in raw["points"]]))
        return
    if stage == "case_opt":
        r = cur["round"]
        prefix = "human-case" if cur["source"] == "case_human" else "case"
        refs = json.loads((env.reviews_dir / f"{prefix}-{block}-in-r{r}.json")
                          .read_text(encoding="utf-8"))["refs"]
        _write(env.reviews_dir / f"{prefix}-{block}-fix-r{r}.json",
               {"dispositions": [{"ref": item["ref"], "status": "fixed", "note": "已改"}
                                 for item in refs]})
        return
    if stage == "case_attribute":
        _write(env.attribution_dir / f"attr-case-{block}-r{cur['round']}.json",
               {"cause": "评审分歧", "note": "反复意见不收敛"})
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
            # F-3（R-52）翻转契约：update 采信 no_change 必须双条件——update 模式 **且**
            # 本 run 的 h_gen 记过账；无账 ⇒ 响亮（与首建同方向不静默）。
            assert errors == ["ALL.json: 本块草稿标记为无变化但账本无本轮判定记录："
                              "请重新判定本块（不许静默跳过）"], mode
            try:
                _collect_writeback_items(ctx)
            except KbClientError as exc:
                assert "本块草稿标记为无变化但账本无本轮判定记录" in str(exc), mode
            else:
                raise AssertionError(f"update 无账 no_change 草稿被静默跳过：{mode}")
            continue
        assert errors == ["ALL.json: nodes 必须是非空数组"], mode
        try:
            _collect_writeback_items(ctx)
        except KbClientError as exc:
            assert "chain/ALL.json 不可解析" in str(exc), mode
        else:
            raise AssertionError(f"首建无变化草稿在回写处被静默跳过：{mode}")


# ---- T15（D-3）：点层收口后回扫声称核对（裁定 32/33 + R-46） ----

def _unclaimed(ref: str, claim: str) -> dict:
    """② 落账形状的最小残余行：verdict=unclaimed，等回扫翻转或原样进大纲。"""
    return {"ref": ref, "claimant": "st-0001", "claim": claim,
            "verdict": "unclaimed", "owner": "", "note": "无认领"}


def _rescan_ctx(tmp_path, claims, *, story_nodes=None, point_nodes=None, script=None):
    """最小账本：故事层已过审且 claims 留有 unclaimed 行，手动直调回扫（不经整驱）。"""
    env = _env(tmp_path, StubKb())
    led = Ledger.fresh(env.design)
    led.layer("story")["state"] = "audited"
    led.layer("story")["claims"] = claims
    led.layer("point")["state"] = "audited"
    if story_nodes:
        _write(env.drafts_dir("story") / "ch-0001.json",
               {"layer": "story", "block": "ch-0001", "nodes": story_nodes})
    if point_nodes:
        _write(env.drafts_dir("point") / "st-0001.json",
               {"layer": "point", "block": "st-0001", "nodes": point_nodes})
    task = ScriptTask(script)
    ctx = Ctx(env=env, task_tool=task, writer=lambda e: None, config={},
              state_messages=[], led=led)
    return env, task, ctx


def test_rescan_flips_id_naming_claim_without_any_reviewer_call(tmp_path):
    """声称里点名了 pt-0002 且点层草稿已有该节点 ⇒ 确定性翻转，零评审调用（裁定 32①）。"""
    _, task, ctx = _rescan_ctx(
        tmp_path, [_unclaimed("st-0001-a1", "该场景由 pt-0002 覆盖")],
        point_nodes=[{"op": "upsert", "type": "point", "id": "pt-0002",
                      "story": "st-0001", "directions": ["正向"]}])
    calls_before = len(task.calls)
    _rescan_claims(ctx)
    row = ctx.led.layer("story")["claims"][0]
    assert row["verdict"] == "covered" and row["owner"] == "pt-0002"
    assert row["rescanned"] == "deterministic"
    assert len(task.calls) == calls_before                  # 一条模型调用都不许花
    assert ctx.led.layer("story")["claims_rescan"]["reviewer"] == 0
    assert ctx.led.layer("story")["claims_rescan"]["deterministic"] == 1


def test_rescan_sends_only_residual_rows_to_one_bounded_pass(tmp_path):
    """翻不动的残余行最多一次有界复核；复跑也不登记意见、不重开任何环。"""
    _, task, ctx = _rescan_ctx(
        tmp_path, [_unclaimed("st-0001-a1", "取消场景由 st-0002 覆盖"),
                   _unclaimed("st-0001-a2", "异常回滚由兄弟机制承接")],
        story_nodes=[{"op": "upsert", "type": "story", "id": "st-0002",
                      "chains": ["ch-0001"]}])
    _rescan_claims(ctx)
    rescan_calls = [c for c in task.calls if c["call_id"].startswith("claims-rescan")]
    assert len(rescan_calls) == 1                           # 有界：绝不逐行、绝不逐轮
    # 只喂残余行：确定性可翻的 a1 不许出现在复核简报里
    assert "st-0001-a2" in rescan_calls[0]["brief"]
    assert "st-0001-a1" not in rescan_calls[0]["brief"]
    # F-5：回扫语境下不登记任何意见——简报尾句不许再说「会被登记为接缝漏测意见」
    assert "会被登记为接缝漏测意见" not in rescan_calls[0]["brief"]
    assert "不登记意见" in rescan_calls[0]["brief"]
    assert _open_of(ctx, "story", source="audit") == []     # 不登记意见
    assert ctx.led.layer("story")["state"] == "audited"     # 没被回扫重开
    rows = {r["ref"]: r for r in ctx.led.layer("story")["claims"]}
    assert rows["st-0001-a1"]["verdict"] == "covered"
    assert rows["st-0001-a1"]["rescanned"] == "deterministic"
    assert rows["st-0001-a2"]["verdict"] == "unclaimed"     # 复核器没认领时原样带进大纲
    stats = ctx.led.layer("story")["claims_rescan"]
    assert stats["deterministic"] == 1 and stats["reviewer"] == 0


def test_rescan_reviewer_pass_spends_no_second_call_on_reclose(tmp_path):
    """R-46①：复核每 run 至多一次。二次收口确定性可重跑（零调用），reviewer 分支跳过，
    已有 rescanned 标记不丢、claims_rescan 计数不重复增长。"""
    _, task, ctx = _rescan_ctx(
        tmp_path, [_unclaimed("st-0001-a1", "场景由兄弟故事承接"),
                   _unclaimed("st-0001-a2", "异常路径由存量机制承接")],
        script={"claims-rescan*": _j({"claims": [
            {"ref": "st-0001-a1", "verdict": "covered", "owner": "st-0002",
             "note": "兄弟故事认领"}], "opinions": []})})
    _rescan_claims(ctx)
    first = [c for c in task.calls if c["call_id"].startswith("claims-rescan")]
    assert len(first) == 1
    rows = {r["ref"]: r for r in ctx.led.layer("story")["claims"]}
    assert rows["st-0001-a1"]["rescanned"] == "reviewer"
    assert ctx.led.layer("story")["claims_rescan"]["reviewer"] == 1
    calls_before = len(task.calls)
    _rescan_claims(ctx)                                     # 第二次收口（gate 退回重做）
    rescan_calls = [c for c in task.calls if c["call_id"].startswith("claims-rescan")]
    assert len(rescan_calls) == 1                           # 一次都不增
    assert len(task.calls) == calls_before                  # 第二次收口零调用
    rows = {r["ref"]: r for r in ctx.led.layer("story")["claims"]}
    assert rows["st-0001-a1"]["rescanned"] == "reviewer"    # 标记不丢
    assert rows["st-0001-a2"]["verdict"] == "unclaimed"
    stats = ctx.led.layer("story")["claims_rescan"]
    assert stats["reviewer"] == 1 and stats["deterministic"] == 0


def test_rescan_marks_and_stats_are_visible_in_outline(tmp_path):
    """裁定 18 同族：回扫翻转必须在接缝归属表可见，claims_rescan 必须经 extras 落成计数行；
    report 行的空归属必须标注「呈递项，不卡关」口径。"""
    _, _, ctx = _rescan_ctx(
        tmp_path, [_unclaimed("st-0001-a1", "该场景由 pt-0002 覆盖"),
                   _unclaimed("st-0001-a2", "取消场景由兄弟故事承接"),
                   _unclaimed("st-0001-a3", "异常回滚由存量机制承接")],
        point_nodes=[{"op": "upsert", "type": "point", "id": "pt-0002",
                      "story": "st-0001", "directions": ["正向"]}],
        script={"claims-rescan*": _j({"claims": [
            {"ref": "st-0001-a2", "verdict": "covered", "owner": "pt-0003",
             "note": "点层已认领"}], "opinions": []})})
    _rescan_claims(ctx)                                     # a1 确定性；a2 复核；a3 残余
    report = {"hard": [], "report": {"empty_seam": 1, "matrix_unreasoned": 0,
                                     "unresolved": 0, "point_missing_directions": 0}}
    extras = _outline_extras(ctx, {})
    assert extras["claims_rescan"] == ctx.led.layer("story")["claims_rescan"]
    md = compose_outline(ctx.led.data, {}, report, extras)
    assert "- report：剩余空归属 1（呈递项，不卡关）" in md
    assert "（owner=pt-0002）（回扫补认·确定性）" in md
    assert "（owner=pt-0003）（回扫补认·复核）" in md
    stats = ctx.led.layer("story")["claims_rescan"]
    assert f"- 回扫补认：确定性 1 条／复核 1 条（{stats['at']}）" in md


def test_point_layer_closeout_hook_fires_the_rescan(tmp_path):
    """挂点证据（唯一）：经 `_layer_audited(ctx, "point")` 收口就必须触发回扫——
    删掉 `if layer == POINT: _rescan_claims(ctx)` 这条用例必红，直调用例做不到这件事。"""
    _, task, ctx = _rescan_ctx(
        tmp_path, [_unclaimed("st-0001-a1", "该场景由 pt-0002 覆盖")],
        point_nodes=[{"op": "upsert", "type": "point", "id": "pt-0002",
                      "story": "st-0001", "directions": ["正向"]}])
    # 收口必经 `_after_layer`（读 task.descriptor 划窗口）；按经驱动用例的 plan 形状补齐。
    ctx.led.data["task"]["descriptor"] = {
        "task_kind": "design", "entry_layer": "chain", "terminal_layer": "point",
        "target_subtree": "", "source_files": [], "note": "全量"}
    calls_before = len(task.calls)
    _layer_audited(ctx, "point")                            # 经挂点收口，不直调 `_rescan_claims`
    row = ctx.led.layer("story")["claims"][0]
    assert row["verdict"] == "covered" and row["owner"] == "pt-0002"
    assert row["rescanned"] == "deterministic"              # ① 挂点确实跑到了回扫
    assert len(task.calls) == calls_before                  # ② 收口路径里不许冒模型调用
    assert ctx.led.layer("story")["state"] == "audited"     # ③ 回扫没重开故事层
    assert _open_of(ctx, "story", source="audit") == []     # ③ 也没登记任何意见


# ---- T16（整片评审收口）：F-1(R-50) / F-2(R-51) / F-3(R-52) / F-4(R-53) / F-6 ----

def test_rescan_count_line_recomputes_from_current_claims_table(tmp_path):
    """F-1（R-50）：回扫计数行与逐行尾注同源——从本张 claims 表现算，不读账本旧计数。

    门退回→重走故事层后 `st["claims"]` 整表重建（`_claims_story:920`），表内零行带
    rescanned；此时大纲不许再出「回扫补认：确定性 N 条／复核 M 条」——那行数字无法被
    同一份表反证（裁定 34）。
    """
    _, _, ctx = _rescan_ctx(
        tmp_path, [_unclaimed("st-0001-a1", "该场景由 pt-0002 覆盖")],
        point_nodes=[{"op": "upsert", "type": "point", "id": "pt-0002",
                      "story": "st-0001", "directions": ["正向"]}])
    _rescan_claims(ctx)
    assert ctx.led.layer("story")["claims_rescan"]["deterministic"] == 1   # 账本记过 1
    # 模拟故事层重建：换一批无标记的新行（claims_rescan 不重建，仍留在账本里）
    new_rows = [_unclaimed("st-0009-a1", "该场景由 pt-0009 覆盖")]
    new_rows[0].update({"verdict": "covered", "owner": "pt-0009"})
    ctx.led.layer("story")["claims"] = new_rows
    report = {"hard": [], "report": {"empty_seam": 0, "matrix_unreasoned": 0,
                                     "unresolved": 0, "point_missing_directions": 0}}
    md = compose_outline(ctx.led.data, {}, report, _outline_extras(ctx, {}))
    assert "回扫补认" not in md
    assert "（回扫补认·" not in md


def test_rescan_cross_notes_attributed_claim_opinion_without_settling(tmp_path):
    """F-2（R-51）：补认后的旧「声称未认领」归因项——不销账、不重开，只在呈递上交叉标注。

    接缝归属表说「已核对（回扫补认·确定性）」、未消化项逐字列着「声称未认领」——两处
    口径相反；归因项是人已看过的账（裁定 32 不许回扫登记/重开/销账），只加标注。
    """
    _, _, ctx = _rescan_ctx(
        tmp_path, [_unclaimed("st-0001-a1", "该场景由 pt-0002 覆盖")],
        point_nodes=[{"op": "upsert", "type": "point", "id": "pt-0002",
                      "story": "st-0001", "directions": ["正向"]}])
    story = ctx.led.layer("story")
    story["opinions"] = [{
        "ref": "op-01", "key": "claims:st-0001-a1", "source": "audit", "block": "ch-0001",
        "target": {"type": "seam", "value": "st-0001-a1"}, "kind": "漏测",
        "ask": "声称未认领：该场景由 pt-0002 覆盖", "evidence": "st-0001",
        "resolved": False, "escalated": True, "disposition": "", "note": ""}]
    # unresolved 项形状按 h_attribute:773-775（refs 列表、呈递时逗号拼接）
    story["unresolved"] = [{"block": "", "refs": ["op-01"], "cause": "评审分歧",
                            "note": "反复意见不收敛"}]
    _rescan_claims(ctx)
    report = {"hard": [], "report": {"empty_seam": 0, "matrix_unreasoned": 0,
                                     "unresolved": 1, "point_missing_directions": 0}}
    md = compose_outline(ctx.led.data, {}, report, _outline_extras(ctx, {}))
    # ① 该行仍在「未消化项」段里（没被删）
    assert "[story/op-01] 声称未认领：该场景由 pt-0002 覆盖" in md
    # ② 行尾带上了补认标注（追加在「（归因：…）」之后、不替换它）
    assert "（归因：评审分歧——反复意见不收敛）（已由回扫补认 owner=pt-0002，此处仍带账供人裁决）" in md
    # ③ 没销账、没重开
    assert story["opinions"][0]["resolved"] is False
    assert story["state"] == "audited"


def test_update_booked_no_change_flows_silently_and_lists_in_outline(tmp_path):
    """F-3 双条件的另一半证据 + F-4（R-53）：记过账 ⇒ 静默放行；大纲措辞与文件系统一致。

    update 模式、账本有本轮 h_gen 记账的 no_change 块 ⇒ `_drafts_errors` 不报错、
    `_collect_writeback_items` 零节点零写入；大纲「本次无变化块」列出该块与 reason，
    且说「（本块判定无变化，未下发节点）」——草稿文件确实在，旧串「未产生草稿」相反。
    """
    env = _env(tmp_path, StubKb())
    led = Ledger.fresh(env.design)
    led.layer("chain")["mode"] = "update"
    led.layer("chain")["state"] = "audited"
    led.layer("chain")["blocks"] = [{"id": "ALL", "state": "done", "round": 0}]
    led.data["no_change"] = [{"layer": "chain", "block": "ALL", "reason": "本块业务规则未变"}]
    led.save()
    ctx = Ctx(env=env, task_tool=ScriptTask(), writer=lambda e: None, config={},
              state_messages=[], led=led)
    _write(env.drafts_dir("chain") / "ALL.json",
           {"layer": "chain", "block": "ALL", "nodes": [], "note": "no_change",
            "reason": "本块业务规则未变"})
    assert _drafts_errors(ctx, "chain", "ALL") == []
    assert _collect_writeback_items(ctx) == []                  # 零节点零写入
    extras = _outline_extras(ctx, {})
    assert extras["no_change"] == [{"layer": "chain", "block": "ALL",
                                    "reason": "本块业务规则未变"}]
    report = {"hard": [], "report": {"empty_seam": 0, "matrix_unreasoned": 0,
                                     "unresolved": 0, "point_missing_directions": 0}}
    md = compose_outline(led.data, {}, report, extras)
    assert "（本块判定无变化，未下发节点）：本块业务规则未变" in md   # F-4 新串在场
    assert "未产生草稿" not in md                                     # 旧串与文件系统相反


def test_unparseable_draft_breaks_retry_budget_and_says_writeback_not_run(tmp_path):
    """F-6：确定性坏草稿不占重试预算——终帧如实说「回写未执行：{原始事实}」。

    旧路径把坏草稿吞进 3 次重试并说「知识库暂不可写；回复『重试』可再次尝试」——
    重试必再炸，这句话把人往错方向支。
    """
    kb = StubKb()
    env = _env(tmp_path, kb)
    led = Ledger.fresh(env.design)
    led.layer("chain")["mode"] = "update"
    led.layer("chain")["state"] = "audited"
    led.layer("chain")["blocks"] = [{"id": "ALL", "state": "done", "round": 0}]
    led.save()
    frames: list[dict] = []
    ctx = Ctx(env=env, task_tool=ScriptTask(), writer=frames.append, config={},
              state_messages=[HumanMessage("通过")], led=led)
    (env.drafts_dir("chain") / "ALL.json").write_text("{ 不是合法 JSON", encoding="utf-8")
    turn = h_writeback(ctx)
    assert turn["case"]["route"] == "end"
    # ① 没白花重试：零下发写入
    assert kb.upserts == [] and "case_node_upsert" not in kb.job_names
    # ② 终帧含「回写未执行」与原始「不可解析」事实、不含「知识库暂不可写」
    text = _end_text(frames)
    assert "回写未执行" in text and "不可解析" in text
    assert "知识库暂不可写" not in text
    assert led.status == "writeback_failed"


# ---- 走查三修复（W3-1）：② 声称核对简报补齐 opinions 条目形状 ----
# 真机增量在 _claims_story → run_reviewer 连败两次落 halted：模型写的 opinion 条目缺
# target/ask、kind 自造（ClaimsOut 9 条 ValidationError）。根因在简报——旧串只写
# "opinions": [] 未给条目形状，而评审子提示明写「形状随简报给定」。schema 不放宽，
# 补的是简报；本节前 3 条钉简报形状与 ClaimsOut 对齐，第 4 条钉重试提示的字段纠偏面。

_CLAIMS_BRIEF_ROWS = [{"ref": "st-0001-a1", "claim": "该场景由 pt-0001 覆盖"}]


def _claims_brief_text(register: bool) -> str:
    # ctx 不参与简报文本生成（只吃 block/rows/register），None 直调即可。
    return _claims_brief(None, "ch-0001", _CLAIMS_BRIEF_ROWS, register=register)


def test_claims_brief_advertises_opinion_item_shape():
    """断言①：register=True 简报同时给出 "target"、"ask" 与 kind 五枚举——本次修复的承重面。"""
    brief = _claims_brief_text(True)
    assert '"target"' in brief
    assert '"ask"' in brief
    assert "漏测|颗粒度|边界归属|命名漂移|失效" in brief
    # claims 段逐字不动（原两行拼接后的完整原文）；register=True 尾句逐字不动。
    assert ('输出一个 JSON：{"claims": [{"ref": "...", "verdict": "covered|unclaimed", '
            '"owner": "覆盖它的故事 id 或空", "note": ""}], ') in brief
    assert "unclaimed 表示没有任何节点认领这个声称（会被登记为接缝漏测意见）。" in brief


def test_opinion_built_from_brief_enums_validates_through_claimssout():
    """断言②：从简报串现取 target.type 与 kind 枚举造合法 opinions，ClaimsOut 必须收。

    防「简报与 schema 二次漂移」——简报把枚举改坏（或 schema 收紧到不认简报给的形状）
    时 model_validate 当场红。走查三拒收的正是这条面：简报说的形状≠schema 认的形状。
    """
    brief = _claims_brief_text(True)
    m_types = re.search(r'"type": "([^"]+)"', brief)
    m_kinds = re.search(r'"kind": "([^"]+)"', brief)
    assert m_types, f"简报未给出 target.type 枚举：{brief}"
    assert m_kinds, f"简报未给出 kind 枚举：{brief}"
    types = m_types.group(1).split("|")
    kinds = m_kinds.group(1).split("|")
    # 简报 advertise 的每一个字面量都要能过 schema：只测首尾的话，中间四个枚举打错字仍绿
    payload = {
        "claims": [{"ref": "st-0001-a1", "verdict": "unclaimed", "owner": "", "note": ""}],
        "opinions": [
            {"target": {"type": types[i % len(types)], "value": f"pt-000{i + 1}"},
             "kind": kind, "ask": f"补一个认领该声称的测试点（{kind}）", "evidence": "声称核对 r1"}
            for i, kind in enumerate(kinds)
        ],
    }
    out = ClaimsOut.model_validate(parse_json_fence(_j(payload)))
    assert [o.kind for o in out.opinions] == kinds
    assert {o.target.type for o in out.opinions} == set(types)
    assert out.opinions[0].ask


def test_rescan_claims_brief_keeps_blank_opinions_without_item_shape():
    """断言③（裁定 32）：回扫简报不 advertise 条目形状，保留「不登记意见／必须留空」，
    旧空数组形状串与回扫尾句逐字在场。"""
    brief = _claims_brief_text(False)
    for shape_word in ("颗粒度", "边界归属", "命名漂移", '"target"', '"ask"', '"kind"'):
        assert shape_word not in brief                     # 不出现条目形状字样
    assert "不登记意见" in brief
    assert "必须留空" in brief
    assert '"opinions": []}' in brief                      # 原形状串逐字保留
    assert "本轮回扫只补认结论，不登记意见、不重开任何环。" in brief
    assert "会被登记为接缝漏测意见" not in brief            # 与 T15 节 F-5 负断言同向


def test_retry_hint_corrects_fields_not_only_fence():
    """断言④：重试提示补上字段纠偏面——真机两次栽在同一形状上，只纠围栏第二次白烧。"""
    assert "字段名与枚举值必须与简报给定的形状逐字一致" in _RETRY_HINT
    assert "不得自造字段或自造枚举值" in _RETRY_HINT
    # 围栏那句逐字保留（改动只补字段面，不改围栏口径）。
    assert ("上一轮输出无法按约定解析（需要恰好一个 ```json 代码块、块外无其它内容）。"
            in _RETRY_HINT)


def test_w3_2_approval_negation_scans_the_clause_not_the_whole_sentence():
    """W3-2（R-58 并入本片）：否定标记只在**批准措辞所在的分句**内作废批准，不再全句连坐。

    左侧四例是收窄后仍必须成立的 fail-closed 面；右侧两例是走查三呈报的「长句被误杀」面。
    真值表按分句取意：一个分句里出现批准措辞且**该分句**无否定标记 → 明示批准。
    """
    assert _explicit_approval("通过") is True
    assert _explicit_approval("不通过") is False
    assert _explicit_approval("先别回写") is False
    assert _explicit_approval("这条还不够，先不通过") is False
    assert _explicit_approval("整体看没什么问题，同意通过") is True      # 旧实现：False（「没」连坐）
    assert _explicit_approval("没问题，批准") is True
    # 回写再入授权同源：多认一个「重试」，收窄口径必须一致，否则两条门一个宽一个窄。
    assert _writeback_authorized("有点小疑问，重试") is True
    assert _writeback_authorized("先别重试") is False


def test_w3_2_long_sentence_approval_reaches_writeback(tmp_path):
    """W3-2 门级证据：收窄必须真的把「长句批准」放行进回写——只测纯函数等于没修。

    取走查三被误杀的那句原话。红在 `led.status == "awaiting_review"` 就是本轮要修的缺陷：
    人类明示批准却被判待决，零回写。**不许**把断言改成「待决」了事。
    """
    kb = StubKb()
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    _drive(env, {"messages": [HumanMessage("整体看没什么问题，同意通过")], "case": {}},
           ScriptTask())
    led = Ledger.load(env.design)
    assert led.status == "done" and led.data["gate"]["approved_at"]
    assert kb.upserts and kb.deletes == []                 # 长句批准同样授权回写


def test_c_1_refusal_clause_never_authorizes_writeback():
    """C-1（评审实测、控制方复现）：分句收窄**不许**把拒绝读成授权。

    左侧是「拒绝与批准同分句」族——旧写法（全句扫）挡住、纯分句写法会放行，是本轮的靶子；
    右侧是 W3-2 必须继续放开的连坐族。逐字钉死，谁再改回 `continue` 单边判定就红。
    """
    for refusal in ("不通过，同意", "同意，不通过", "不通过", "先别回写", "这条还不够，先不通过"):
        assert _explicit_approval(refusal) is False, refusal
    for approval in ("通过", "没问题，批准", "整体看没什么问题，同意通过"):
        assert _explicit_approval(approval) is True, approval
    # 回写再入授权同口径：多认一个「重试」，但拒绝照旧一律否决。
    for refusal in ("不重试，重试", "先别重试"):
        assert _writeback_authorized(refusal) is False, refusal
    assert _writeback_authorized("有点小疑问，重试") is True


def test_c_1_contradictory_message_stays_awaiting_review(tmp_path):
    """C-1 门级证据：自相矛盾的话在真门上一行不写、状态退回待决。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    _drive(env, {"messages": [HumanMessage("不通过，同意")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert led.status == "awaiting_review" and not led.data["gate"].get("approved_at")
    assert kb.upserts == [] and kb.deletes == []


def test_halt_books_cursor_snapshot_and_kind(tmp_path):
    """裁定 43：停在哪儿、为什么停、第几次停——不落盘就没有「续跑的依据」。族属只由抛点声明。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    for _ in range(4):                                   # 首问 + 3 次携错重问（NUDGE_CAP=3）
        _append(state, _drive(env, state, ScriptTask()))
        simulate(env, plan=bad)
    _append(state, _drive(env, state, ScriptTask()))     # 第 5 次：nudge 用尽 → halted
    led = Ledger.load(env.design)
    halt = led.data["halt"]
    assert led.status == "halted" and halt["kind"] == "artifact_retry"
    assert halt["stage"] == "plan" and halt["count"] == 1 and halt["round"] == 0
    assert halt["reason"].startswith("plan/-/- 重试超限") and halt["at"]


def test_halt_kind_validation_fails_loud():
    """新抛点漏标族属必须响亮失败：默认值会把未知族属洗成某一族的续跑策略（比不分类更坏）。"""
    from aitester.case_design.stages import _Halt
    import pytest
    with pytest.raises(ValueError):
        _Halt("某句原因", "not_a_kind")
    assert _Halt("某句原因", "transient").kind == "transient"


def test_wants_restart_truth_table():
    """裁定 46：重开＝销毁现场，判据只许偏「少销毁」。分句规则复用批准那一族，不写第二份。"""
    from aitester.case_design.stages import _wants_restart
    for text, expected in (
        ("重开任务", True),
        ("作废这次，重新按新需求来", True),
        ("从头开始跑一遍", True),
        ("换新任务，按 notes.md 来", True),
        ("放弃本次", True),
        ("别重开", False), ("先不重开", False), ("未重开的意思", False),
        ("整体看没什么问题，重开就不必了", False),        # 后半分句自带「不」→ 整条否决
        ("继续", False), ("同意通过", False), ("", False),
        ("这条链路重开了新市场", True),                   # 已登记的误判面（裁定 46）：钉住现状
    ):
        assert _wants_restart(text) is expected, text


def test_wants_restart_negation_only_kills_the_matching_clause():
    """W3-2 的教训反向复用：前一分句的「没」不许连坐后一分句的明示重开。"""
    from aitester.case_design.stages import _wants_restart
    assert _wants_restart("没什么问题，重开任务吧") is True


# ---- 第五片 T30：_boot 把 halted 与 done 分家——默认断点续跑、四族分流（裁定 42/44）----

def _archive_entries(env) -> list[str]:
    """归档目录下的条目集合——「现场有没有被搬走」的差分读数（R-78，模块级助手，T33 从这里 import）。

    `archive/` 本身**第一轮就存在**：`drive_turn` 先 `ensure_dirs()` 造出 drafts/reviews/manifests/
    attribution/cases 五个空子目录，`_boot` 的无账本支 `any(env.design.iterdir())` 随即为真并归档。
    所以「续跑没销毁现场」只能判**零新增条目**，判「目录不存在」是假失败。
    """
    arc = env.design / "archive"
    entries = arc.rglob("*") if arc.is_dir() else []
    return sorted(str(x.relative_to(arc)).replace("\\", "/") for x in entries)


def test_halted_new_turn_resumes_without_archiving(tmp_path):
    """裁定 42 的正身：中断后再发一句不销毁现场。走查四为这条赔了 572 call。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    for _ in range(4):
        _append(state, _drive(env, state, ScriptTask()))
        simulate(env, plan=bad)
    _append(state, _drive(env, state, ScriptTask()))                 # → halted
    assert Ledger.load(env.design).data["counters"] == {
        "chain": 0, "story": 0, "point": 0, "case": 0}

    arc_before = _archive_entries(env)                               # 续跑轮之前
    turn = _drive(env, {"messages": [HumanMessage("换个说法再试")], "case": {}}, ScriptTask())
    assert turn["case"]["route"] == "agent"                          # 续跑：重新下发，不是重开账本
    assert _archive_entries(env) == arc_before                       # 零新增归档条目＝现场一个文件没搬
    assert (env.design / "plan.json").is_file()
    led = Ledger.load(env.design)
    assert led.status == "active" and led.cursor["stage"] == "plan"
    # 预算重开的真读数：本轮是续跑后的**首问**，故 nudge 归 0 而 asked 已置真。
    # 这条同时是「清游标」的差分证据——若 _go 没清游标，本轮 ask 会在 nudge=3≥NUDGE_CAP 当场再 halted。
    assert led.cursor["nudge"] == 0 and led.cursor["asked"] is True
    assert any(h.startswith("resumed-from-halted@") for h in led.data["history"])


def test_halted_new_turn_with_explicit_restart_archives(tmp_path):
    """明示重开＝今天那条路：归档＋fresh＋carried_stale 携带，一并保住既有语义。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    _append(state, _drive(env, state, ScriptTask()))
    simulate(env, plan={"task_kind": "design", "entry_layer": "chain", "terminal_layer": "chain",
                        "target_subtree": "", "source_files": [], "note": "只链层"})
    _append(state, _drive(env, state, ScriptTask()))                 # gen 阶段推进
    led = Ledger.load(env.design)
    led.status = "halted"
    led.data["halt"].update({"kind": "artifact_retry", "stage": led.cursor["stage"],
                             "layer": led.cursor["layer"], "block": led.cursor["block"],
                             "round": int(led.cursor["round"] or 0), "reason": "手搭", "count": 1})
    led.layer("story")["state"] = "stale_pending"
    led.save()

    arc_before = _archive_entries(env)
    _drive(env, {"messages": [HumanMessage("作废这次，重开任务")], "case": {}}, ScriptTask())
    assert (env.design / "archive").is_dir()
    assert len(_archive_entries(env)) > len(arc_before)              # 明示重开才搬现场
    fresh = Ledger.load(env.design)
    assert fresh.data["task"] == {} and fresh.data["carried_stale"] == ["story"]


def test_human_wait_halt_resume_goes_back_to_the_gate(tmp_path):
    """门连续待决而 halted 后，一句「同意通过」必须从 gate_interpret 接上放行，不是重烧设计环。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())                                     # 跑到 awaiting_review
    for _ in range(4):                                               # NUDGE_CAP=3 → 第 4 次待决 halted
        _drive(env, {"messages": [HumanMessage("我再想想")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert led.status == "halted" and led.data["halt"]["kind"] == "human_wait"
    assert led.cursor["stage"] == "gate_interpret" and led.data["gate"]["unclear"] == 4
    assert kb.upserts == []

    arc_before = _archive_entries(env)
    turn = _drive(env, {"messages": [HumanMessage("同意通过")], "case": {}}, ScriptTask())
    assert turn["case"]["route"] == "end"
    assert _archive_entries(env) == arc_before                       # 门接上放行，不搬现场
    led = Ledger.load(env.design)
    assert led.status == "done" and kb.upserts                        # 解读 1 call → 回写，零重烧
    assert led.data["gate"]["unclear"] == 0


def test_needs_input_halt_refuses_and_costs_nothing(tmp_path):
    """裁定 44 的反面：同游标重放必再炸的那族，续跑一步都不许走——零转场、零子调用、现场不动。"""
    kb = StubKb(layers={"chain": [{"id": "ch-0001", "type": "chain", "parent": "", "level": 1}],
                        "story": [], "point": []})
    env = _env(tmp_path, kb)
    task = ScriptTask()
    state = {"messages": [HumanMessage("只更新 ch-9999")], "case": {}}
    _append(state, _drive(env, state, task))
    simulate(env, plan={"task_kind": "design", "entry_layer": "story", "terminal_layer": "point",
                        "target_subtree": "ch-9999", "source_files": [], "note": "窄任务"})
    frames: list[dict] = []
    _append(state, _drive(env, state, task, writer=frames.append))
    led = Ledger.load(env.design)
    assert led.status == "halted" and led.data["halt"]["kind"] == "needs_input"
    assert "在链路树里不存在" in _end_text(frames)          # halted 终帧带 reason（T28 已落）

    calls, upserts = len(task.calls), len(kb.upserts)
    arc_before = _archive_entries(env)
    frames2: list[dict] = []
    _drive(env, {"messages": [HumanMessage("继续")], "case": {}}, task, writer=frames2.append)
    assert len(task.calls) == calls and len(kb.upserts) == upserts     # 判据 ① 的离线同型
    refusal = _end_text(frames2)
    assert "重开任务" in refusal and "本轮未做任何生成" in refusal   # 「怎么出去」由拒绝帧说（T32 只补 halted 帧尾巴）
    led2 = Ledger.load(env.design)
    assert led2.status == "halted" and led2.data["halt"]["count"] == 2  # 拒绝不改状态、只说实话
    assert led2.data["cursor"] == led.data["cursor"]                   # 游标原样：断点没被清，人修完仍接得上
    assert _archive_entries(env) == arc_before                       # 拒绝轮一个文件没搬


def test_halt_count_tracks_same_spot(tmp_path):
    """同一处停两次，count 必须说真话——终帧那句「第 N 次」是给人的成本读数（裁定 18 可见性同族）。
    本条从 T28 移来：只有续跑真的保住账本（T30），第二次 halt 才会落在同一处（红证归属见 T28 注）。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    for _ in range(2):                                   # 两整轮「重问到超限」
        for _ in range(4):
            _append(state, _drive(env, state, ScriptTask()))
            simulate(env, plan=bad)
        _append(state, _drive(env, state, ScriptTask()))    # → halted
        state = {"messages": [HumanMessage("再试一次")], "case": {}}   # 新回合＝续跑（本任务前是归档）
    halt = Ledger.load(env.design).data["halt"]
    assert halt["kind"] == "artifact_retry" and halt["count"] == 2


# ---- 第五片 T31：Ctx.instr 续跑注入单点——【人工补充】用后即清（裁定 45／R-81）----

def test_resume_note_attaches_to_first_ask_then_clears(tmp_path):
    """裁定 45：_gen_text 一类指令纯确定性拼装、根本不读人话——不带这句就是同一指令的确定性重放。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    for _ in range(4):
        _append(state, _drive(env, state, ScriptTask()))
        simulate(env, plan=bad)
    _append(state, _drive(env, state, ScriptTask()))                    # halted

    note = "链路按下单／售后拆成两条再试"
    turn = _drive(env, {"messages": [HumanMessage(note)], "case": {}}, ScriptTask())
    assert f"【人工补充】{note}" in turn["messages"][0].content
    assert Ledger.load(env.design).data["halt"]["resume_note"] == ""    # 用后即清
    _append(state, turn)
    turn2 = _drive(env, state, ScriptTask())                            # 同一回合的后续重问
    assert "【人工补充】" not in turn2["messages"][0].content


def test_resume_note_does_not_cross_turns(tmp_path):
    """补充语只活一个回合：下一回合即便还停在同一处，也不许把上一轮的人话再附一遍。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    for _ in range(4):
        _append(state, _drive(env, state, ScriptTask()))
        simulate(env, plan=bad)
    _append(state, _drive(env, state, ScriptTask()))
    _append(state, _drive(env, {"messages": [HumanMessage("换个说法")], "case": {}}, ScriptTask()))
    led = Ledger.load(env.design)
    assert led.data["halt"]["resume_note"] == ""                        # 首问已消费
    turn = _drive(env, {"messages": [HumanMessage("再想想")], "case": {}}, ScriptTask())
    assert "【人工补充】" not in turn["messages"][0].content
    assert len([m for m in turn["messages"] if "换个说法" in str(m.content)]) == 0


def test_instr_is_the_single_injection_point(tmp_path):
    """R-81：注入点必须在下发原语 `instr`，不在 `ask`——`h_case_plan` 的首批下发绕开重试预算、
    不经 `ask`，写进 `ask` 就漏那一条。前两条钉行为，本条钉「两条口共用一处」的位置。"""
    env = _env(tmp_path, StubKb())
    led = Ledger.fresh(env.design)
    led.data["halt"]["resume_note"] = "按下单／售后拆两条"
    ctx = Ctx(env=env, task_tool=ScriptTask(), writer=lambda e: None,
              config={"configurable": {"thread_id": "t1"}},
              state_messages=[HumanMessage("换个说法")], led=led, case={}, ticks=1)
    assert ctx.instr("生成计划").content.endswith("\n【人工补充】按下单／售后拆两条")
    assert led.data["halt"]["resume_note"] == ""              # 用后即清
    assert ctx.instr("生成计划").content == "生成计划"          # 第二条起不再带


def test_halt_frame_tells_where_it_stopped_and_what_next(tmp_path):
    """W4-2 的同族要求：终帧是用户唯一读到的话，不许只说「中止」而不说下一步的代价。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    frames: list[dict] = []
    for _ in range(4):
        _append(state, _drive(env, state, ScriptTask()))
        simulate(env, plan=bad)
    _append(state, _drive(env, state, ScriptTask(), writer=frames.append))
    text = _end_text(frames)
    assert text.startswith("测试设计任务中止：plan/-/- 重试超限")        # 基句逐字不动
    assert "现场已保留" in text and "制品反复不合法" in text
    assert "再发一句将从这里续跑" in text and "重开任务" in text


def test_halt_frame_next_step_matches_family(tmp_path):
    """裁定 44/47 的文案同伴：mid-drive 抛的 needs_input 也走 _halt_frame，
    那一族根本不许被承诺「再发一句就续跑」——相邻两句自相矛盾就是终帧说谎。"""
    kb = StubKb(layers={"chain": [{"id": "ch-0001", "type": "chain", "parent": "", "level": 1}],
                        "story": [], "point": []})
    env = _env(tmp_path, kb)
    task = ScriptTask()
    state = {"messages": [HumanMessage("只更新 ch-9999")], "case": {}}
    _append(state, _drive(env, state, task))
    simulate(env, plan={"task_kind": "design", "entry_layer": "story", "terminal_layer": "point",
                        "target_subtree": "ch-9999", "source_files": [], "note": "窄任务"})
    frames: list[dict] = []
    _append(state, _drive(env, state, task, writer=frames.append))
    text = _end_text(frames)
    assert text.startswith("测试设计任务中止：目标子树「ch-9999」")   # 基句仍逐字
    assert "现场已保留" in text and "输入或账本对不上" in text        # 位置读数与中文族名在
    assert "续跑也无解" in text and "从这里续跑" not in text          # 这句对本族是谎
    assert _next_step_text("artifact_retry") == \
        "再发一句将从这里续跑；要放弃这次工作请明说「重开任务」。"      # 另一族照旧承诺续跑
