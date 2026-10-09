# backend/tests/test_case_design_e2e.py
"""T11 离线端到端：整图跑通「首建」与「更新」两分支（真文件工具 + 真 TaskTool + 桩驱动），
外加 P4 记录项（盲枚举的结构盲面）的 drain 级证据。

不联网、不调真模型：主智能体由 ScriptedProvider 演，评审子由 ScriptTask 桩 drive 演，
其余全是真件（图、驱动、阶段处理器、任务工具、文件工具、账本、大纲）。
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from streaming_fakes import ScriptedProvider, local_tools
from test_case_design_driver import (
    ScriptTask, StubKb, _append, _archive_entries, _drive, _end_text, _env, _j, drain, simulate
)

from aitester.adapters.tools.subagent_tools import build_task_tool
from aitester.case_design.constants import CASE_REVIEW_AGENT_ID, CASE_REVIEW_BLIND_AGENT_ID
from aitester.case_design.graph import build_case_design_graph
from aitester.case_design.ledger import Ledger
from aitester.orchestration import stream_graph

ROSTER = {
    CASE_REVIEW_AGENT_ID: {"name": "用例评审子智能体", "desc": "reviewer", "tools": ["read"]},
    CASE_REVIEW_BLIND_AGENT_ID: {"name": "盲枚举子智能体", "desc": "blind", "tools": ["read"]},
}


def _tc(call_id: str, name: str, path: str, obj=None) -> dict:
    args: dict = {"file_path": path}
    if obj is not None:
        args["content"] = json.dumps(obj, ensure_ascii=False)
    return {"id": call_id, "name": name, "args": args, "type": "tool_call"}


def _ai(*parts) -> AIMessage:
    calls = [p for p in parts if isinstance(p, dict)]
    text = "".join(p for p in parts if isinstance(p, str))
    return AIMessage(content=text, tool_calls=calls)


def _tools_with_task(project: Path, task: ScriptTask) -> list:
    task_tool = build_task_tool(ROSTER, build_child=task.build_child, drive=task.drive,
                                parallel=task.parallel)
    return local_tools(project) + [task_tool]


def _first_build_script() -> list[AIMessage]:
    plan = {"task_kind": "design", "entry_layer": "chain", "terminal_layer": "point",
            "target_subtree": "", "source_files": ["requirements.md"], "note": "全量首建"}
    chain_v1 = {"layer": "chain", "block": "ALL", "nodes": [
        {"op": "upsert", "type": "chain", "name": "下单链路", "level": 1, "parent": "",
         "business_scope": "下单主流程", "excluded": "支付失败回滚", "priority": "P0"}]}
    chain_v2 = {"layer": "chain", "block": "ALL", "nodes": [
        {"op": "upsert", "type": "chain", "id": "ch-0001", "name": "下单链路", "level": 1,
         "parent": "", "business_scope": "下单主流程（补充退款约定）",
         "excluded": "支付失败回滚", "priority": "P0"},
        {"op": "upsert", "type": "chain", "name": "退款链路", "level": 2, "parent": "ch-0001",
         "business_scope": "退款全流程", "excluded": "", "priority": "P0"}]}
    fix = {"dispositions": [{"ref": "op-01", "status": "fixed", "note": "已增补 ch-0002 退款链路"}]}
    story_1 = {"layer": "story", "block": "ch-0001", "nodes": [
        {"op": "upsert", "type": "story", "name": "下单成功", "chains": ["ch-0001"],
         "actor": "买家", "preconditions": "已登录", "trigger": "提交订单", "expected": "订单创建",
         "assumptions": ["退款接缝由退款链路覆盖"], "priority": "P0"}]}
    story_2 = {"layer": "story", "block": "ch-0002", "nodes": [
        {"op": "upsert", "type": "story", "name": "退款受理", "chains": ["ch-0002"],
         "actor": "买家", "preconditions": "订单已支付", "trigger": "申请退款",
         "expected": "退款受理成功", "priority": "P0"}]}
    point_1 = {"layer": "point", "block": "st-0001", "nodes": [
        {"op": "upsert", "type": "point", "name": "下单正向", "story": "st-0001",
         "scenario": "已登录且库存充足时提交订单", "entities": ["订单"],
         "directions": ["正向"], "priority": "P0"}]}
    point_2 = {"layer": "point", "block": "st-0002", "nodes": [
        {"op": "upsert", "type": "point", "name": "退款负向", "story": "st-0002",
         "scenario": "退款金额超过订单金额时提交", "entities": ["退款"],
         "directions": ["负向"], "priority": "P0"}]}
    return [
        _ai(_tc("w0", "write", "design/intent.json", {"intent": "task"})),     # 0 意向判定
        _ai("判定为测试设计任务。"),                                             # 0
        _ai(_tc("w1", "write", "design/plan.json", plan)),                    # 1
        _ai("计划已写好。"),                                                   # 2
        _ai(_tc("w2", "write", "design/drafts/chain/ALL.json", chain_v1)),    # 3
        _ai("链路草稿已交。"),                                                 # 4
        _ai(_tc("r1", "read", "design/drafts/chain/ALL.json")),               # 5
        _ai(_tc("w3", "write", "design/drafts/chain/ALL.json", chain_v2),     # 6
            _tc("w4", "write", "design/reviews/blk-chain-ALL-fix-r0.json", fix)),
        _ai("优化完成。"),                                                     # 7
        _ai(_tc("w5", "write", "design/drafts/story/ch-0001.json", story_1)), # 8
        _ai("故事块一已交。"),                                                  # 9
        _ai(_tc("w6", "write", "design/drafts/story/ch-0002.json", story_2)), # 10
        _ai("故事块二已交。"),                                                  # 11
        _ai(_tc("w7", "write", "design/drafts/point/st-0001.json", point_1)), # 12
        _ai("测试点块一已交。"),                                                # 13
        _ai(_tc("w8", "write", "design/drafts/point/st-0002.json", point_2)), # 14
        _ai("测试点块二已交。"),                                                # 15
    ]


def _update_script() -> list[AIMessage]:
    plan = {"task_kind": "design", "entry_layer": "chain", "terminal_layer": "point",
            "target_subtree": "", "source_files": [], "note": "按新业务信息做增量"}
    chain = {"layer": "chain", "block": "ALL", "nodes": [
        {"op": "upsert", "type": "chain", "id": "ch-0001", "name": "下单链路", "level": 1,
         "parent": "", "business_scope": "下单主流程（补充退款约定）",
         "excluded": "支付失败回滚", "priority": "P0"},
        {"op": "upsert", "type": "chain", "name": "退款链路", "level": 2, "parent": "ch-0001",
         "business_scope": "退款全流程", "excluded": "", "priority": "P0"}]}
    story_nc = {"layer": "story", "block": "ch-0001", "nodes": [], "note": "no_change"}
    story_new = {"layer": "story", "block": "ch-0002", "nodes": [
        {"op": "upsert", "type": "story", "name": "退款受理", "chains": ["ch-0002"],
         "actor": "买家", "preconditions": "订单已支付", "trigger": "申请退款",
         "expected": "退款受理成功", "assumptions": ["退款接缝由存量下单故事覆盖"],
         "priority": "P0"}]}
    point_nc = {"layer": "point", "block": "st-0001", "nodes": [], "note": "no_change"}
    point_new = {"layer": "point", "block": "st-0002", "nodes": [
        {"op": "upsert", "type": "point", "name": "退款负向", "story": "st-0002",
         "scenario": "退款金额超过订单金额时提交", "entities": ["退款"],
         "directions": ["负向"], "priority": "P0"}]}
    return [
        _ai(_tc("w0", "write", "design/intent.json", {"intent": "task"})),         # 0 意向判定
        _ai("判定为测试设计任务。"),                                                  # 0
        _ai(_tc("w1", "write", "design/plan.json", plan)),                        # 1
        _ai("计划已写好。"),                                                       # 2
        _ai(_tc("w2", "write", "design/drafts/chain/ALL.json", chain)),           # 3
        _ai("链路增量已交。"),                                                     # 4
        _ai(_tc("w3", "write", "design/drafts/story/ch-0001.json", story_nc)),    # 5
        _ai("故事块一判定无变化。"),                                                # 6
        _ai(_tc("w4", "write", "design/drafts/story/ch-0002.json", story_new)),   # 7
        _ai("故事块二已交。"),                                                     # 8
        _ai(_tc("w5", "write", "design/drafts/point/st-0001.json", point_nc)),    # 9
        _ai("测试点块一判定无变化。"),                                              # 10
        _ai(_tc("w6", "write", "design/drafts/point/st-0002.json", point_new)),   # 11
        _ai("测试点块二已交。"),                                                   # 12
    ]


def test_first_build_end_to_end(tmp_path: Path) -> None:
    """首建整图：一次块环（意见→优化→复审）+ ①②③ 判决 + 大纲人审门 + 审批回写。"""
    (tmp_path / "requirements.md").write_text(
        "# 需求：订单与退款\n- 下单主流程\n- 退款流程\n", encoding="utf-8")
    kb = StubKb()                                    # 空库：三层 first_build
    env = _env(tmp_path, kb)
    task = ScriptTask({
        "blk-chain-ALL-r0": _j({"opinions": [
            {"target": {"type": "node", "value": "ch-0001"}, "kind": "颗粒度",
             "ask": "退款约定请落成独立链路", "evidence": "business_scope 未含退款"}],
            "resolutions": []}),
        "blk-chain-ALL-r1": _j({"opinions": [],
                                "resolutions": [{"ref": "op-01", "resolved": True,
                                                 "note": "已按意见调整"}]}),
        "enum-chain-*": _j({"items": [
            {"kind": "业务对象", "name": "订单", "evidence": "requirements.md"},
            {"kind": "业务对象", "name": "退款", "evidence": "requirements.md"}]}),
        "cmp-chain-r0": _j({"items": [
            {"name": "订单", "kind": "业务对象", "landing": "ch-0001", "note": ""},
            {"name": "退款", "kind": "业务对象", "landing": "ch-0002", "note": ""}]}),
        "claims-story-r0-ch-0001": _j({"claims": [
            {"ref": "st-0001-a1", "verdict": "covered", "owner": "st-0002",
             "note": "退款链路故事认领"}], "opinions": []}),
        "matrix-point-r0-ch-0001": _j({"cells": [
            {"entity": "订单", "story": "st-0001", "verdict": "covered", "reason": ""},
            {"entity": "退款", "story": "st-0001", "verdict": "not_needed",
             "reason": "退款由退款链路覆盖"}]}),
        "matrix-point-r0-ch-0002": _j({"cells": [
            {"entity": "退款", "story": "st-0002", "verdict": "covered", "reason": ""}]}),
    })
    tools = _tools_with_task(tmp_path, task)
    provider = ScriptedProvider(_first_build_script())

    frames = list(stream_graph(build_case_design_graph, provider, tools,
                               [HumanMessage(content="按业务信息生成测试设计")], case_env=env))
    finish = frames[-1]
    assert finish["type"] == "finish"
    assert finish["reply"] == "大纲已生成（design/outline.md），等待人工评审。"
    assert len(provider.calls) == 17                        # 剧本一条不剩、一条不欠

    # 主智能体这一轮真的只动了这十次工具（9 写 + 1 重读；含开局的意向判定一写）
    calls = [e["tool"] for e in frames if e["type"] == "call"]
    assert calls == ["write", "write", "write", "read", "write", "write",
                     "write", "write", "write", "write"]

    # 评审判决：块环走满两轮（意见 → 复审回执）；①②③ 各判过；无人审前零 KB 写
    ids = task.call_ids()
    assert ids[:2] == ["blk-chain-ALL-r0", "blk-chain-ALL-r1"]
    assert any(i.startswith("enum-chain-") for i in ids) and "cmp-chain-r0" in ids
    assert "claims-story-r0-ch-0001" in ids
    assert "claims-story-r0-ch-0002" not in ids             # 无声称的块不驱动核对器
    assert "matrix-point-r0-ch-0001" in ids and "matrix-point-r0-ch-0002" in ids
    assert not any(i.endswith("-r2") for i in ids)          # 判决全是一次过（无解析重试）
    enum_call = next(c for c in task.calls if c["call_id"].startswith("enum-chain-"))
    assert enum_call["agent"] == CASE_REVIEW_BLIND_AGENT_ID
    assert kb.upserts == [] and kb.deletes == []            # 人审前零回写

    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"
    assert [led.layer(x)["state"] for x in ("chain", "story", "point")] == ["audited"] * 3
    outline = (env.design / "outline.md").read_text(encoding="utf-8")
    assert "增量测试大纲" in outline and "（新增，P0）" in outline
    assert "op-01" in outline and "已销账" in outline and "已增补 ch-0002 退款链路" in outline
    assert "owner=st-0002" in outline
    assert "[业务对象] 订单：落点 ch-0001" in outline
    assert "（退款, st-0001）不需要：退款由退款链路覆盖" in outline

    # 人审通过（新回合、新 thread）：gate-int-r1 判批准 → 回写 → done
    provider2 = ScriptedProvider([])
    frames2 = list(stream_graph(build_case_design_graph, provider2, tools,
                                [HumanMessage(content="通过")], case_env=env))
    assert frames2[-1]["reply"] == "回写完成：本次过审节点已写入知识库。"
    assert "gate-int-r1" in task.call_ids()
    led = Ledger.load(env.design)
    assert led.status == "done"
    assert [led.layer(x)["state"] for x in ("chain", "story", "point")] == ["done"] * 3
    written = {(layer, node["id"]) for layer, node in kb.upserts}
    assert written == {("chain", "ch-0001"), ("chain", "ch-0002"),
                       ("story", "st-0001"), ("story", "st-0002"),
                       ("point", "pt-0001"), ("point", "pt-0002")}
    assert kb.deletes == []
    # 计数呈递：替身没报 unchanged（= 真实首写语义）⇒ 六个全记 written、untouched 归零，
    # 上面那条终帧逐字断言（裁定 31 基句）就是 untouched==0 不加尾句的证据。
    assert led.data["writeback"]["written"] == 6 and led.data["writeback"]["untouched"] == 0
    banned = {"op", "block", "state", "in_scope", "round", "reason"}
    for _, node in kb.upserts:
        assert not (banned & set(node))                     # 工作稿痕迹不许进 KB 载荷


def test_update_branch_end_to_end(tmp_path: Path) -> None:
    """存量三层 + 新业务信息：层内混合（no_change/更新/新增）→ 增量大纲 → 只回写增量。"""
    (tmp_path / "kb_root" / "business" / "wiki").mkdir(parents=True)
    (tmp_path / "kb_root" / "business" / "wiki" / "orders.md").write_text(
        "# 订单\n退款约定：全额退款走退款链路。\n", encoding="utf-8")
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "parent": "", "level": 1,
                   "name": "下单链路", "business_scope": "下单主流程", "priority": "P0"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"],
                   "name": "下单成功", "priority": "P0"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001",
                   "name": "下单正向", "entities": ["订单"], "directions": ["正向"],
                   "priority": "P0"}],
    }, root=str(tmp_path / "kb_root"))
    env = _env(tmp_path, kb)
    task = ScriptTask({
        "claims-story-r0-ch-0002": _j({"claims": [
            {"ref": "st-0002-a1", "verdict": "covered", "owner": "st-0001",
             "note": "存量下单故事已覆盖退款接缝"}], "opinions": []}),
    })
    tools = _tools_with_task(tmp_path, task)
    provider = ScriptedProvider(_update_script())

    frames = list(stream_graph(build_case_design_graph, provider, tools,
                               [HumanMessage(content="按业务信息生成测试设计")], case_env=env))
    assert frames[-1]["reply"] == "大纲已生成（design/outline.md），等待人工评审。"
    assert len(provider.calls) == 14

    led = Ledger.load(env.design)
    assert led.status == "awaiting_review"
    assert [led.layer(x)["mode"] for x in ("chain", "story", "point")] == ["update"] * 3
    assert led.data["no_change"] == [{"layer": "story", "block": "ch-0001", "reason": ""},
                                     {"layer": "point", "block": "st-0001", "reason": ""}]
    ids = task.call_ids()
    assert "blk-chain-ALL-r0" in ids
    assert "blk-story-ch-0002-r0" in ids and "blk-point-st-0002-r0" in ids
    assert "blk-story-ch-0001-r0" not in ids                 # no_change 块不驱动块评审
    assert "blk-point-st-0001-r0" not in ids
    assert "claims-story-r0-ch-0002" in ids and "claims-story-r0-ch-0001" not in ids
    assert kb.upserts == [] and kb.deletes == []

    outline = (env.design / "outline.md").read_text(encoding="utf-8")
    # W3-3 后的口径：同 id 的存量行不再与草稿并呈——将被写库的那一份只有草稿；
    # 存量仍可见，但只出现在本次没动过的节点上（st-0001 挂在 no_change 块里）。
    assert "（更新，P0）" in outline
    assert "  - st-0001 下单成功（存量，chains: ch-0001）" in outline  # 整行钉死
    assert "（存量，P0）" not in outline                    # W3-3：同 id 存量行不再与草稿并呈
    assert "（新增" not in outline                            # 全 update 模式：没有新增标记
    assert "owner=st-0001" in outline                         # 增量 × 存量接缝归属
    assert "块 ch-0001（本块判定无变化" in outline
    assert "块 st-0001（本块判定无变化" in outline

    provider2 = ScriptedProvider([])
    frames2 = list(stream_graph(build_case_design_graph, provider2, tools,
                                [HumanMessage(content="通过")], case_env=env))
    assert frames2[-1]["reply"] == "回写完成：本次过审节点已写入知识库。"
    ledger = Ledger.load(env.design)
    assert ledger.status == "done"
    written = {(layer, node["id"]) for layer, node in kb.upserts}
    assert written == {("chain", "ch-0001"), ("chain", "ch-0002"),
                       ("story", "st-0002"), ("point", "pt-0002")}   # 存量 st-0001/pt-0001 零扰动
    assert kb.deletes == []


def test_p4_blind_enumeration_sees_sources_not_tree(tmp_path: Path) -> None:
    """P4 记录（结构面）：盲枚举的简报不带树、白名单清单只列业务源、判决落盘可回查。"""
    root = tmp_path / "kb_root"
    (root / "business" / "wiki").mkdir(parents=True)
    (root / "business" / "wiki" / "orders.md").write_text(
        "# 订单\n退款约定：全额退款走退款链路。\n", encoding="utf-8")
    kb = StubKb(root=str(root))
    env = _env(tmp_path, kb)
    task = ScriptTask({
        "enum-chain-*": _j({"items": [
            {"kind": "业务对象", "name": "订单", "evidence": "business/wiki/orders.md"}]}),
        "claims-story-r0-ch-0001": _j({"claims": [
            {"ref": "st-0001-a1", "verdict": "covered", "owner": "st-0002", "note": ""}],
            "opinions": []}),
    })
    state = drain(env, kb, task)
    assert _end_text(state["frames"]) == "大纲已生成（design/outline.md），等待人工评审。"
    enum_call = next(c for c in task.calls if c["call_id"].startswith("enum-chain-"))
    assert enum_call["agent"] == CASE_REVIEW_BLIND_AGENT_ID
    assert "design/manifests/sources.json" in enum_call["brief"]
    assert "drafts" not in enum_call["brief"]                # 简报结构性不带树
    manifest = json.loads((env.manifests_dir / "enum-chain.json").read_text(encoding="utf-8"))
    assert manifest["items"] == [{"kind": "业务对象", "name": "订单",
                                  "evidence": "business/wiki/orders.md"}]
    sources = json.loads((env.manifests_dir / "sources.json").read_text(encoding="utf-8"))
    assert sources["kb_files"] == ["business/wiki/orders.md"]   # 白名单=业务源清单


# ---- T25 第四片整图：case_only 从一句人话走到末门呈递，批准后 done 且零知识库写入 ----

def _case_only_script() -> list[AIMessage]:
    """case_only：计划 → 批用例正文 → 停末门呈递。三层一律 skipped，零设计侧块。"""
    plan = {"task_kind": "case_only", "entry_layer": "point", "terminal_layer": "point",
            "target_subtree": "ch-0001", "source_files": [], "note": "给下单链路写用例"}
    cases = {"chain": "ch-0001", "batch": "ch-0001-b1", "cases": [
        {"case_id": "", "title": "下单正向：库存充足时提交订单", "covers": ["pt-0001"],
         "preconditions": "买家已登录，购物车内有一件在售商品",
         "steps": ["以买家身份提交订单", "读取订单状态与库存扣减记录"],
         "expected": ["订单状态为已创建", "库存数量比提交前减少 1"],
         "priority": "P0", "note": ""}]}
    return [
        _ai(_tc("w0", "write", "design/intent.json", {"intent": "task"})),        # 0 意向判定
        _ai("判定为测试设计任务。"),                                                # 0
        _ai(_tc("w1", "write", "design/plan.json", plan)),                    # 1
        _ai("计划已写好，本次是纯用例任务。"),                                  # 2
        _ai(_tc("w2", "write", "design/cases/ch-0001-b1.json", cases)),        # 3
        _ai("第一批用例正文已交。"),                                           # 4
    ]


def test_case_only_end_to_end_delivery_and_approval(tmp_path: Path) -> None:
    """第四片整图：case_only 从一句人话走到末门呈递，批准后 done 且**零知识库写入**（裁定 35/36）。"""
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "parent": "", "level": 1,
                   "name": "下单链路", "business_scope": "下单主流程", "priority": "P0"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"],
                   "name": "下单成功", "priority": "P0"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001",
                   "name": "下单正向", "entities": ["订单"], "directions": ["正向"],
                   "priority": "P0"}],
    }, root=str(tmp_path / "kb_root"))               # 三层 maintained 存量：case_only 的放行前提
    env = _env(tmp_path, kb)
    task = ScriptTask({
        "case-ch-0001-b1-r0": _j({"opinions": [], "resolutions": []}),
        "case-gate-int-r1": _j({"opinions": [], "resolutions": []}),
    })
    tools = _tools_with_task(tmp_path, task)
    provider = ScriptedProvider(_case_only_script())

    frames = list(stream_graph(build_case_design_graph, provider, tools,
                               [HumanMessage(content="给下单链路写用例")], case_env=env))
    assert frames[-1]["reply"] == "用例交付物已生成（design/case-delivery.md），等待人工评审。"
    assert len(provider.calls) == 6                 # 三次工具回合 + 三句人话，不多不少
    assert [e["tool"] for e in frames if e["type"] == "call"] == ["write", "write", "write"]
    # 呈递轮只派过一次批评审：末门解读（case-gate-int-r1）等人话那一轮才发（同
    # test_case_only_ring_reaches_delivery_gate 的 drain 级事实）。
    assert task.call_ids() == ["case-ch-0001-b1-r0"]
    # 设计侧零动：三层全 skipped ⇒ 一块草稿都没落，①②③ 一次没派
    assert [Ledger.load(env.design).layer(x)["mode"] for x in ("chain", "story", "point")] == \
        ["skipped"] * 3
    assert not any(i.startswith(("blk-", "enum-", "claims-", "matrix-"))
                   for i in task.call_ids())
    delivery = (env.design / "case-delivery.md").read_text(encoding="utf-8")
    assert "实测：未落实点 0 个" in delivery
    assert "cc-0001" in delivery                     # 用例 id 由系统补齐，只活在这份交付物里
    assert kb.upserts == [] and kb.deletes == []     # 呈递阶段零写库

    provider2 = ScriptedProvider([])                 # 批准轮：零工具调用，只回人话
    frames2 = list(stream_graph(build_case_design_graph, provider2, tools,
                                [HumanMessage(content="通过")], case_env=env))
    assert frames2[-1]["reply"] == ("用例交付确认完成：用例正文只落项目空间 design/cases/，"
                                    "本次零知识库写入。")
    assert kb.upserts == [] and kb.deletes == []     # 裁定 35：末门批准 ≠ 回写授权
    assert task.call_ids() == ["case-ch-0001-b1-r0", "case-gate-int-r1"]   # 整环共两次子调用
    led = Ledger.load(env.design)
    assert led.status == "done" and led.data["writing"]["gate"]["approved_at"]
    assert (env.cases_dir() / "ch-0001-b1.json").exists()
    body = json.loads((env.cases_dir() / "ch-0001-b1.json").read_text(encoding="utf-8"))
    assert body["cases"][0]["case_id"] == "cc-0001"  # 补号写回文件本体，交付物与制品逐字一致


def test_e2e_gate_halt_then_resume_closes_without_reburn(tmp_path):
    """第五片整链：门待决用尽 → halted → 「同意通过」续跑 → 回写 → done，现场全程不归档。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = drain(env, kb, ScriptTask())                    # 到 awaiting_review
    for _ in range(4):
        _drive(env, {"messages": [HumanMessage("我再想想")], "case": {}}, ScriptTask())
    assert Ledger.load(env.design).status == "halted"
    outline_before = (env.design / "outline.md").read_bytes()
    arc_before = _archive_entries(env)
    _drive(env, {"messages": [HumanMessage("同意通过")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert led.status == "done" and kb.upserts
    assert (env.design / "outline.md").read_bytes() == outline_before   # 大纲没被重生成
    assert _archive_entries(env) == arc_before                           # 零新增归档条目


def test_e2e_needs_input_refuse_then_restart_replans(tmp_path):
    """拒绝轮零调用，重开轮才重新计划——两句话的区别必须由账本说话，不是靠文案（判据 ①④ 离线同型）。"""
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
    assert "在链路树里不存在" in _end_text(frames)          # R-83：帧不能只落盘，中止理由也要在链尾说实话
    calls = len(task.calls)

    arc_before = _archive_entries(env)
    _drive(env, {"messages": [HumanMessage("继续")], "case": {}}, task)             # 拒绝轮
    led2 = Ledger.load(env.design)
    assert len(task.calls) == calls and kb.upserts == []                            # 零子调用、零写入
    assert led2.data["halt"]["count"] == 2                                          # 拒绝也记账
    assert led2.data["cursor"] == led.data["cursor"]                                # R-83：零转场，游标原样
    assert not any(h.startswith("resumed-from-halted")                              # R-83：走拒绝支，非续跑支
                   for h in led2.data["history"])
    assert _archive_entries(env) == arc_before                                      # 现场一个文件没搬
    assert led2.status == "halted"

    _drive(env, {"messages": [HumanMessage("重开任务，按链路树全量来")], "case": {}}, task)
    assert len(_archive_entries(env)) > len(arc_before)                             # R-78：只有这句才销毁现场
    assert Ledger.load(env.design).data["task"] == {}                               # 新账本等待 h_plan
