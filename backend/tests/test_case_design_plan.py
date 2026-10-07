from pathlib import Path

import pytest

from aitester.case_design.instructions import (
    attribute_instruction, gen_instruction, opt_instruction, plan_instruction,
)
from aitester.case_design.outline import compose_outline
from aitester.case_design.plan import (
    in_scope_targets, init_task, materialize_blocks, plan_layers, summarize_probe,
    validate_plan,
)


def test_validate_plan_ok_and_rejects(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "req.md").write_text("需求", encoding="utf-8")
    (tmp_path / "design").mkdir()
    raw = {"task_kind": "design", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": ["docs/req.md"], "note": "全量"}
    desc, errors = validate_plan(raw, str(tmp_path))
    assert errors == [] and desc["entry_layer"] == "chain"

    bad = {**raw, "entry_layer": "story", "terminal_layer": "chain", "source_files": ["docs/req.md"]}
    _, errors = validate_plan(bad, str(tmp_path))
    assert any("终止层" in e for e in errors)

    bad2 = {**raw, "source_files": ["docs/miss.md", "design/ledger.json"]}
    _, errors = validate_plan(bad2, str(tmp_path))
    assert any("miss.md" in e for e in errors) and any("design/" in e for e in errors)


def test_validate_plan_rejects_absolute_and_escaping_source_files(tmp_path: Path):
    """F6：绝对路径 / `..` 段一律拒列——否则 root/rel 落到 project_dir 之外，
    存在性探测变成任意路径探针，路径还会被当「项目相对路径」写进提示词。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "req.md").write_text("需求", encoding="utf-8")
    escape = str(tmp_path / "docs" / "req.md")          # 确实存在，用于证明拒列与存在性无关
    raw = {"task_kind": "design", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "note": "",
           "source_files": [f"/{escape.lstrip('/')}", escape, "docs/../docs/req.md",
                            "../outside.md", "docs/req.md"]}
    desc, errors = validate_plan(raw, str(tmp_path))
    assert desc == {}
    assert sum("项目相对路径" in e for e in errors) == 4   # 前四项被拒，最后一项合法
    assert not any(e.endswith("：docs/req.md") for e in errors)      # 合法相对路径不误拒
    assert not any("不存在" in e for e in errors)                    # 拒列先于探测，不做任意路径探针


def test_validate_plan_rejects_non_list_and_missing_source_files(tmp_path: Path):
    """I-4：`source_files` 给非列表（str/None/dict）旧实现静默清空成 [] 且 errors==[]，
    键缺失同样静默通过——盲枚举的业务信息来源清单缩水到只剩 KB 桶，恰是 ① 要防的「整块业务没进树」，
    且一次重试机会都没有。修法：非列表与缺键都报错并触发重试；空列表 [] 仍合法（纯存量更新）。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "req.md").write_text("需求", encoding="utf-8")
    base = {"task_kind": "design", "entry_layer": "chain", "terminal_layer": "point",
            "target_subtree": "", "note": ""}
    for val in ("req.md", None, {"a": "req.md"}):
        desc, errors = validate_plan({**base, "source_files": val}, str(tmp_path))
        assert desc == {} and any("source_files" in e and "数组" in e for e in errors), val
    # 键缺失也报错（不是静默 [] 通过）
    desc, errors = validate_plan(base, str(tmp_path))
    assert desc == {} and any("source_files" in e for e in errors)
    # 空列表 [] 仍合法：无新文档的纯存量更新任务要走得通
    desc, errors = validate_plan({**base, "source_files": []}, str(tmp_path))
    assert errors == [] and desc["source_files"] == []


def test_summarize_probe_p3_rule():
    ok = summarize_probe("chain", [{"id": "ch-0001", "type": "chain", "parent": ""}])
    assert ok["maintained"] is True
    bad = summarize_probe("chain", [{"id": "ch-0001", "type": "chain"}])          # 缺 parent
    assert bad["maintained"] is False
    assert summarize_probe("story", [])["maintained"] is False

    # 各层父引用规则（F10 补测）：story 看 chains、point 看 story，缺任一件即整层未维护
    assert summarize_probe("story", [{"id": "st-0001", "type": "story", "chains": ["ch-0001"]}])["maintained"] is True
    assert summarize_probe("story", [{"id": "st-0001", "type": "story", "chains": []}])["maintained"] is False
    assert summarize_probe("point", [{"id": "pt-0001", "type": "point", "story": "st-0001"}])["maintained"] is True
    assert summarize_probe("point", [{"id": "pt-0001", "type": "point", "story": ""}])["maintained"] is False
    assert summarize_probe("chain", [{"id": "ch-0001", "type": "story", "parent": ""}])["maintained"] is False


def test_plan_layers_和_blocks():
    descriptor = {"entry_layer": "chain", "terminal_layer": "point", "target_subtree": ""}
    # 简报此处自相矛盾：同一 probe 夹具下，断言 1（story=first_build，须 maintained=False）与
    # 断言 2（story=update，须 maintained=True）不可能同时成立；而 modes3（stale 强制 update）
    # 证明实现内不得有非 stale 之外的特判。按 Interfaces/后续任务口径保留实现与两条断言字面值，
    # 为断言 2 拆出维护态夹具 probe2。
    probe = {"chain": {"maintained": True}, "story": {"maintained": False}, "point": {"maintained": False}}
    probe2 = {**probe, "story": {"maintained": True}}
    modes = plan_layers(descriptor, probe, stale_layers=set())
    assert modes == {"chain": "update", "story": "first_build", "point": "first_build"}

    descriptor2 = {**descriptor, "entry_layer": "story", "terminal_layer": "story"}
    modes2 = plan_layers(descriptor2, probe2, stale_layers=set())
    assert modes2 == {"chain": "skipped", "story": "update", "point": "skipped"}

    modes3 = plan_layers(descriptor, probe, stale_layers={"point"})
    assert modes3["point"] == "update"                    # stale → 强制 update

    chain_rows = [{"id": "ch-0001", "parent": "", "level": 1}, {"id": "ch-0002", "parent": "ch-0001", "level": 2}]
    story_rows = [{"id": "st-0001", "chains": ["ch-0002"]}, {"id": "st-0002", "chains": ["ch-0001"]}]
    scope = in_scope_targets({"target_subtree": "ch-0001"}, chain_rows, story_rows)
    assert scope["chains"] == {"ch-0001", "ch-0002"} and scope["stories"] == {"st-0001", "st-0002"}
    scope2 = in_scope_targets({"target_subtree": "ch-0002"}, chain_rows, story_rows)
    assert scope2["chains"] == {"ch-0002"} and scope2["stories"] == {"st-0001"}
    assert materialize_blocks("chain", scope) == ["ALL"]
    # 简报此处笔误：断言值（["ch-0002"] / ["st-0001"]）对应 scope2（窄子树），
    # 且与 Interfaces 节「story→范围内链路 id；point→范围内故事 id」一致——改为 scope2。
    assert materialize_blocks("story", scope2) == ["ch-0002"]
    assert materialize_blocks("point", scope2) == ["st-0001"]
    # 全量范围下按同一实现物化（补测，锁定接口语义）
    assert materialize_blocks("story", scope) == ["ch-0001", "ch-0002"]
    assert materialize_blocks("point", scope) == ["st-0001", "st-0002"]


def test_in_scope_targets_phantom_subtree_is_empty_scope():
    """F4：target_subtree 指向不存在的链路 id → 范围为空，不为幻影链路物化块。"""
    chain_rows = [{"id": "ch-0001", "parent": "", "level": 1}, {"id": "ch-0002", "parent": "ch-0001", "level": 2}]
    story_rows = [{"id": "st-0001", "chains": ["ch-0002"]}, {"id": "st-0002", "chains": ["ch-0001"]}]
    scope = in_scope_targets({"target_subtree": "ch-9999"}, chain_rows, story_rows)
    assert scope["chains"] == set() and scope["stories"] == set()
    assert materialize_blocks("story", scope) == []
    assert materialize_blocks("point", scope) == []


def test_init_task_shape(tmp_path: Path):
    from aitester.case_design.ledger import Ledger
    led = Ledger.fresh(tmp_path)
    descriptor = {"task_kind": "design", "entry_layer": "chain", "terminal_layer": "point",
                  "target_subtree": "", "source_files": [], "note": ""}
    probe = {l: {"maintained": False, "count": 0, "evidence": "空"} for l in ("chain", "story", "point")}
    modes = {"chain": "first_build", "story": "first_build", "point": "first_build"}
    init_task(led.data, descriptor, probe, modes)
    assert led.data["task"]["plan"]["blocks"]["chain"] == ["ALL"]
    assert led.layer("chain")["mode"] == "first_build"
    assert led.cursor["stage"] == "gen" and led.cursor["layer"] == "chain"

    # F10 补测：游标块、逐层状态与块清单形状
    assert led.cursor["block"] == "ALL"
    for layer in ("chain", "story", "point"):
        state = led.layer(layer)
        assert state["state"] == "pending" and state["mode"] == "first_build"
        expected = [{"id": "ALL", "state": "todo", "round": 0}] if layer == "chain" else []
        assert state["blocks"] == expected
    assert led.data["task"]["plan"]["budget"] == {"round_cap": 5}   # F5：取自 constants.ROUND_CAP


def test_instructions_carry_paths_and_schema():
    text = plan_instruction()
    assert "design/plan.json" in text and "source_files" in text

    g = gen_instruction("story", "ch-0001", draft_path="design/drafts/story/ch-0001.json",
                        ref_hint="链路 id 见 design/drafts/chain/ALL.json",
                        kb_manifest_path="design/manifests/kb-story.json",
                        opinions_path=None, errors=["nodes[0]: name 不能为空"], mode="update")
    assert "design/drafts/story/ch-0001.json" in g
    assert "kb-story.json" in g and "name 不能为空" in g and '"chains"' in g

    o = opt_instruction("point", "st-0001", 2, draft_path="d", opinions_path="op.json",
                        fix_path="fx.json", source_cn="块评审")
    assert "第 2 轮" in o and "dispositions" in o
    a = attribute_instruction("chain", "ALL", opinions_path="op.json", out_path="att.json")
    assert "业务信息不足" in a and "成本超限" in a


def test_gen_instruction_update_requires_kb_manifest_path():
    """F3：更新态缺清单路径要响亮失败——否则把字面 None 写进模型指令，
    在生成阶段诱导一次幻觉读文件（付费轮次）并污染待校验草稿。"""
    with pytest.raises(ValueError, match="kb_manifest_path"):
        gen_instruction("story", "ch-0001", draft_path="d", ref_hint="r", mode="update")
    with pytest.raises(ValueError, match="mode=update 需要 kb_manifest_path"):
        gen_instruction("chain", "ALL", draft_path="d", ref_hint="r", kb_manifest_path="", mode="update")

    first = gen_instruction("chain", "ALL", draft_path="d", ref_hint="r")   # 首建态无需清单
    assert "None" not in first and "首建" in first and "既有节点清单" not in first


_LEDGER_DATA = {
    "task": {"started_at": "t", "descriptor": {"task_kind": "design", "entry_layer": "chain",
             "terminal_layer": "point", "target_subtree": "", "note": ""},
             "probe": {"chain": {"maintained": False, "count": 0, "evidence": "空"}},
             "plan": {"blocks": {"chain": ["ALL"], "story": [], "point": []},
                      "budget": {"round_cap": 5}}, "replans": []},
    "layers": {"chain": {"state": "done", "mode": "first_build"},
               "story": {"state": "stale_pending", "mode": "update"},
               "point": {"state": "pending", "mode": "first_build"}},
}


def _outline(nodes_by_layer: dict, report: dict, extras: dict, *, replans: list | None = None) -> str:
    """夹具：账本切片按层状态取自 _LEDGER_DATA，仅替换本次呈递内容。"""
    data = {"task": {**_LEDGER_DATA["task"], "replans": replans or []},
            "layers": _LEDGER_DATA["layers"]}
    return compose_outline(data, nodes_by_layer, report, extras)


def _section(md: str, title: str) -> list[str]:
    """取「## title」到下一个二级标题之间的正文行（丢掉两侧的空行，只留条目）。"""
    lines = md.splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"## {title}")
    body = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    while body and body[-1] == "":
        body.pop()
    return body


def test_outline_renders_point_missing_directions():
    """A-M1：report 行同步渲染「断言方向缺失数」，呈递给人审门（归 report，不进 hard）。"""
    nodes = {"chain": [{"id": "ch-0001", "name": "示例链路甲", "op": "upsert", "parent": "",
                        "state": "新增", "priority": "P0"}], "story": [], "point": []}
    report = {"hard": [], "report": {"empty_seam": 0, "matrix_unreasoned": 0, "unresolved": 0,
                                     "point_missing_directions": 3}}
    md = _outline(nodes, report, {"claims": [], "matrix_notes": [], "unresolved": [], "duplicates": []})
    assert "断言方向缺失 3" in md


def test_compose_outline_sections():
    ledger_data = _LEDGER_DATA
    nodes_by_layer = {"chain": [{"id": "ch-0001", "name": "示例链路甲", "op": "upsert",
                                 "parent": "", "state": "新增", "priority": "P0"}],
                      "story": [], "point": []}
    report = {"hard": [{"code": "broken_parent", "layer": "story", "where": "st-0003",
                        "detail": "父引用 ch-9999 不存在"}],
              "report": {"empty_seam": 0, "matrix_unreasoned": 0, "unresolved": 1}}
    extras = {"claims": [{"claimant": "块评审", "claim": "交界阶段归属", "verdict": "unclaimed", "owner": ""},
                          {"claimant": "全局审", "claim": "上下游归属", "verdict": "claimed", "owner": "st-0002"}],
              "matrix_notes": [{"entity": "实体甲", "story": "st-0002", "reason": "该故事不触及此实体"}],
              "unresolved": [{"layer": "chain", "ref": "op-03", "ask": "补一条",
                              "cause": "评审分歧", "note": "两子意见互斥"}],
              "duplicates": [["st-0007", "st-0012"]]}
    md = compose_outline(ledger_data, nodes_by_layer, report, extras)
    assert "增量树" in md and "ch-0001" in md
    assert "stale_pending" in md or "失效待重算" in md
    assert "评审分歧" in md and "st-0007" in md
    assert "结构指标" in md

    # F10 补测：hard 非零明细行、接缝归属表的空归属/已核对两态、矩阵复核行
    assert "- hard：1 项未清零" in md
    assert "  - [broken_parent] story/st-0003：父引用 ch-9999 不存在" in md
    assert "- 块评审 声称「交界阶段归属」→ 空归属（未消化）（owner=）" in md
    assert "- 全局审 声称「上下游归属」→ 已核对（owner=st-0002）" in md
    assert "- （实体甲, st-0002）不需要：该故事不触及此实体" in md


def test_outline_deletes_appear_once_per_layer():
    """F1：三层各自的 delete 节点都只进「删除」清单一行，且不在树上以存活形态出现。"""
    nodes_by_layer = {
        "chain": [{"id": "ch-0001", "name": "示例链路甲", "op": "upsert", "parent": "",
                   "state": "新增", "priority": "P0"},
                  {"id": "ch-0009", "name": "示例链路乙", "op": "delete", "reason": "并入甲"}],
        "story": [{"id": "st-0001", "name": "示例故事一", "op": "upsert", "chains": ["ch-0001"],
                   "state": "新增"},
                  {"id": "st-0009", "name": "示例故事九", "op": "delete", "reason": "与 st-0001 重复"},
                  {"id": "st-0010", "op": "delete", "reason": "删除节点本就无 name（T2 口径）"}],
        "point": [{"id": "pt-0001", "name": "示例的点", "op": "upsert", "story": "st-0001",
                   "directions": ["正向"], "entities": ["实体甲"], "state": "新增"},
                  {"id": "pt-0009", "name": "示例点九", "op": "delete", "reason": "不在范围"}],
    }
    md = _outline(nodes_by_layer, {"hard": [], "report": {}},
                  {"claims": [], "matrix_notes": [], "unresolved": [], "duplicates": []})
    for nid in ("ch-0009", "st-0009", "st-0010", "pt-0009"):
        assert md.count(nid) == 1, nid                     # 一次：删除清单里的唯一一行
    tree = _section(md, "增量树")
    assert [line for line in tree if "删除" in line] == [
        "- ch-0009 示例链路乙（删除：并入甲）",
        "- st-0009 示例故事九（删除：与 st-0001 重复）",
        "- st-0010 （删除：删除节点本就无 name（T2 口径））",
        "- pt-0009 示例点九（删除：不在范围）",
    ]
    assert "- ch-0001 示例链路甲（新增，P0）" in md          # 存活节点照常成树
    assert "  - st-0001 示例故事一（新增，chains: ch-0001）" in md
    assert "    - pt-0001 示例的点（方向: 正向；实体: 实体甲）" in md


def test_outline_dedups_same_id_delete_across_blocks():
    """A-M3：`_tree_lines` 的跨块同 id delete 去重（seen_deleted）曾被整段删掉而全绿——既有
    `test_outline_deletes_appear_once_per_layer` 各删除 id 互不相同，永不触发该分支。本夹具让同一
    链路 id 被两个块各出一条 delete，断言删除清单只出一条（首条），大纲里该 id 只出现一次。"""
    nodes_by_layer = {
        "chain": [{"id": "ch-0001", "name": "示例链路甲", "op": "upsert", "parent": "",
                   "state": "新增", "priority": "P0"},
                  {"id": "ch-0009", "name": "示例链路乙", "op": "delete", "reason": "块A 判定下线"},
                  {"id": "ch-0009", "name": "示例链路乙", "op": "delete", "reason": "块B 判定下线"}],
        "story": [], "point": [],
    }
    md = _outline(nodes_by_layer, {"hard": [], "report": {}},
                  {"claims": [], "matrix_notes": [], "unresolved": [], "duplicates": []})
    assert md.count("ch-0009") == 1                            # 跨块同 id 只呈递一次
    deletes = [line for line in _section(md, "增量树") if "删除" in line]
    assert deletes == ["- ch-0009 示例链路乙（删除：块A 判定下线）"]
    assert "- ch-0001 示例链路甲（新增，P0）" in md             # 存活节点照常成树


def test_outline_renders_orphan_branch():
    """F2：驱动只把「本次涉及的草稿节点」交给大纲（窄子树任务、父节点被删时上游父节点不在
    输入里）。父引用落空的链路按根起树并递归下钻——否则整条分支（含其下 upsert 的故事与测试点）
    静默消失，非空任务会在人审门呈「- （本次无增量节点）」，人类据假完整大纲批准回写。"""
    nodes_by_layer = {
        "chain": [{"id": "ch-0001", "name": "示例链路甲", "op": "upsert", "parent": "",
                   "state": "存量", "priority": "P0"},
                  {"id": "ch-0003", "name": "示例链路丙", "op": "upsert", "parent": "ch-0002",
                   "state": "更新", "priority": "P1"},
                  {"id": "ch-0004", "name": "示例链路丁", "op": "upsert", "parent": "ch-0003",
                   "state": "更新", "priority": "P2"}],
        "story": [{"id": "st-0001", "name": "示例故事一", "op": "upsert", "chains": ["ch-0001"],
                   "state": "新增"},
                  {"id": "st-0004", "name": "示例故事四", "op": "upsert", "chains": ["ch-0004"],
                   "state": "新增"}],
        "point": [{"id": "pt-0004", "name": "示例的点四", "op": "upsert", "story": "st-0004",
                   "directions": ["正向"], "entities": ["实体丙"], "state": "新增"}],
    }
    tree = _section(_outline(nodes_by_layer, {"hard": [], "report": {}},
                             {"claims": [], "matrix_notes": [], "unresolved": [], "duplicates": []}),
                    "增量树")
    assert tree == [
        "- ch-0001 示例链路甲（存量，P0）",                       # 一级根照常
        "  - st-0001 示例故事一（新增，chains: ch-0001）",
        "- ch-0003 示例链路丙（更新，P1）",                       # 父引用落空 → 按根起树
        "  - ch-0004 示例链路丁（更新，P2）",                     # 孤儿分支递归下钻
        "    - st-0004 示例故事四（新增，chains: ch-0004）",
        "      - pt-0004 示例的点四（方向: 正向；实体: 实体丙）",
        "- [用户故事层] 失效待重算（下次任务重跑）",
    ]
    assert "- （本次无增量节点）" not in "\n".join(tree)


def test_outline_final_fallback_renders_branch_whose_parent_sits_in_out_of_scope_cycle():
    """I-1 大纲侧双保险：驱动把「影响闭包内 KB 存量 ∪ 本 run 草稿」一起交给大纲。当范围内草稿链
    的父引用落进一段全在范围外的闭合父环（ch-9001↔ch-9002）时，它既非根也非孤儿（父在 chain_ids
    里），旧 `_tree_lines` 无根可起 ⇒ 整条分支静默丢失、呈「- （本次无增量节点）」假完整大纲。
    修法在起树后加终兜底：凡未被 walked 覆盖的链路一律按根补走，杜绝丢分支。"""
    nodes_by_layer = {
        "chain": [{"id": "ch-9001", "name": "存量甲", "op": "upsert", "parent": "ch-9002",
                   "state": "存量", "priority": "P1"},
                  {"id": "ch-9002", "name": "存量乙", "op": "upsert", "parent": "ch-9001",
                   "state": "存量", "priority": "P1"},
                  {"id": "ch-0001", "name": "草稿链", "op": "upsert", "parent": "ch-9001",
                   "state": "更新", "priority": "P1"}],
        "story": [{"id": "st-0001", "name": "草稿故事", "op": "upsert", "chains": ["ch-0001"],
                   "state": "更新"}],
        "point": [{"id": "pt-0001", "name": "草稿点", "op": "upsert", "story": "st-0001",
                   "directions": ["正向"], "entities": ["实体甲"], "state": "更新"}],
    }
    md = _outline(nodes_by_layer, {"hard": [], "report": {}},
                  {"claims": [], "matrix_notes": [], "unresolved": [], "duplicates": []})
    tree = _section(md, "增量树")
    for nid in ("ch-0001", "st-0001", "pt-0001"):
        assert any(nid in line for line in tree), nid           # 三个草稿 id 全部在场
    assert "- （本次无增量节点）" not in "\n".join(tree)


def test_outline_fallback_is_noop_when_all_chains_already_walked():
    """§1 终兜底对既有呈递零影响：正常根树不新增任何行（只在原本会丢分支时触发）。"""
    nodes_by_layer = {
        "chain": [{"id": "ch-0001", "name": "示例链路甲", "op": "upsert", "parent": "",
                   "state": "新增", "priority": "P0"},
                  {"id": "ch-0002", "name": "示例链路乙", "op": "upsert", "parent": "ch-0001",
                   "state": "新增", "priority": "P0"}],
        "story": [], "point": [],
    }
    tree = _section(_outline(nodes_by_layer, {"hard": [], "report": {}},
                             {"claims": [], "matrix_notes": [], "unresolved": [], "duplicates": []}),
                    "增量树")
    assert tree == [
        "- ch-0001 示例链路甲（新增，P0）",
        "  - ch-0002 示例链路乙（新增，P0）",
        "- [用户故事层] 失效待重算（下次任务重跑）",
    ]


def test_outline_terminates_on_duplicated_chain_id():
    """F2 递归的自保：父子引用成环或重复 id 的异常草稿，同一链路只呈递一次、不递归失控。"""
    nodes_by_layer = {
        "chain": [{"id": "ch-0001", "name": "示例链路甲", "op": "upsert", "parent": "",
                   "state": "新增", "priority": "P0"},
                  {"id": "ch-0001", "name": "示例链路甲", "op": "upsert", "parent": "ch-0001",
                   "state": "新增", "priority": "P0"}],
        "story": [], "point": [],
    }
    tree = _section(_outline(nodes_by_layer, {"hard": [], "report": {}},
                             {"claims": [], "matrix_notes": [], "unresolved": [], "duplicates": []}),
                    "增量树")
    assert [line for line in tree if line.lstrip("- ").startswith("ch-0001 ")] == [
        "- ch-0001 示例链路甲（新增，P0）"]


def test_outline_empty_and_populated_lists_have_no_blank_artifacts():
    """F8：清单为空出「- 无」，非空只出条目本身；重规划事件不悬空。"""
    nodes = {"chain": [{"id": "ch-0001", "name": "示例链路甲", "op": "upsert", "parent": "",
                        "state": "新增", "priority": "P0"}], "story": [], "point": []}
    report = {"hard": [], "report": {"empty_seam": 0, "matrix_unreasoned": 0, "unresolved": 0}}
    populated = {"claims": [{"claimant": "块评审", "claim": "取消阶段归属", "verdict": "claimed",
                             "owner": "st-0002"}],
                 "matrix_notes": [{"entity": "实体甲", "story": "st-0002", "reason": "不触及"}],
                 "unresolved": [{"layer": "story", "ref": "op-01", "ask": "补边界",
                                 "cause": "业务信息不足", "note": "缺需求"}],
                 "duplicates": [["st-0007", "st-0012"]]}
    md = _outline(nodes, report, populated,
                  replans=[{"at": "2026-10-05T10:00:00", "trigger": "范围收窄"}])
    for title in ("重复标注清单", "接缝归属表（②摘要）", "矩阵复核（③「不需要」的业务理由）",
                  "未消化项（含不收敛归因）"):
        body = _section(md, title)
        assert body and all(line.startswith("- ") for line in body), title   # 只有条目，无空行产物
        assert "- 无" not in body, title
    plan_body = _section(md, "本次计划")
    head = next(i for i, line in enumerate(plan_body) if line.startswith("- 重规划事件："))
    assert plan_body[head] == "- 重规划事件："                # 有事件：标题行 + 子项，无悬空值
    assert plan_body[head + 1] == "  - [2026-10-05T10:00:00] 范围收窄"

    empty = _outline(nodes, report, {"claims": [], "matrix_notes": [], "unresolved": [], "duplicates": []})
    for title in ("重复标注清单", "接缝归属表（②摘要）", "矩阵复核（③「不需要」的业务理由）",
                  "未消化项（含不收敛归因）"):
        assert _section(empty, title) == ["- 无"], title
    assert "- 重规划事件：无" in empty


def test_update_mode_instruction_offers_no_change_terminal_state():
    text = gen_instruction("story", "ch-0002", draft_path="design/drafts/story/ch-0002.json",
                           ref_hint="parent 填链路 id", kb_manifest_path="design/manifests/kb-story.json",
                           mode="update")
    assert '"nodes": []' in text and '"note": "no_change"' in text   # 第四种合法去向在场
    assert "reason" in text                                          # 理由有独立落点
    assert "首建" in text                                             # 讲明这条通道不延伸到首建


def test_first_build_mode_never_offers_no_change():
    text = gen_instruction("chain", "ALL", draft_path="design/drafts/chain/ALL.json",
                           ref_hint="parent 留空", mode="first_build")
    assert "no_change" not in text        # 首建模式给这条通道 = 教模型空手过关
    assert "本块无变化" not in text
    assert '"nodes": [...]' in text       # 首建仍只有「产出节点」这一条出路
