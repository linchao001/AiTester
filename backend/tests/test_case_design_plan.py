from pathlib import Path

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


def test_summarize_probe_p3_rule():
    ok = summarize_probe("chain", [{"id": "ch-0001", "type": "chain", "parent": ""}])
    assert ok["maintained"] is True
    bad = summarize_probe("chain", [{"id": "ch-0001", "type": "chain"}])          # 缺 parent
    assert bad["maintained"] is False
    assert summarize_probe("story", [])["maintained"] is False


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


def test_init_task_shape():
    from aitester.case_design.ledger import Ledger
    import tempfile
    led = Ledger.fresh(Path(tempfile.mkdtemp()) )
    descriptor = {"task_kind": "design", "entry_layer": "chain", "terminal_layer": "point",
                  "target_subtree": "", "source_files": [], "note": ""}
    probe = {l: {"maintained": False, "count": 0, "evidence": "空"} for l in ("chain", "story", "point")}
    modes = {"chain": "first_build", "story": "first_build", "point": "first_build"}
    init_task(led.data, descriptor, probe, modes)
    assert led.data["task"]["plan"]["blocks"]["chain"] == ["ALL"]
    assert led.layer("chain")["mode"] == "first_build"
    assert led.cursor["stage"] == "gen" and led.cursor["layer"] == "chain"


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


def test_compose_outline_sections():
    ledger_data = {
        "task": {"started_at": "t", "descriptor": {"task_kind": "design", "entry_layer": "chain",
                 "terminal_layer": "point", "target_subtree": "", "note": ""},
                 "probe": {"chain": {"maintained": False, "count": 0, "evidence": "空"}},
                 "plan": {"blocks": {"chain": ["ALL"], "story": [], "point": []},
                          "budget": {"round_cap": 5}}, "replans": []},
        "layers": {"chain": {"state": "done", "mode": "first_build"},
                   "story": {"state": "stale_pending", "mode": "update"},
                   "point": {"state": "pending", "mode": "first_build"}},
    }
    nodes_by_layer = {"chain": [{"id": "ch-0001", "name": "下单链路", "op": "upsert",
                                 "state": "新增", "priority": "P0"}],
                      "story": [], "point": []}
    report = {"hard": [], "report": {"empty_seam": 0, "matrix_unreasoned": 0, "unresolved": 1}}
    extras = {"claims": [], "matrix_notes": [],
              "unresolved": [{"layer": "chain", "ref": "op-03", "ask": "补一条",
                              "cause": "评审分歧", "note": "两子意见互斥"}],
              "duplicates": [["st-0007", "st-0012"]]}
    md = compose_outline(ledger_data, nodes_by_layer, report, extras)
    assert "增量树" in md and "ch-0001" in md
    assert "stale_pending" in md or "失效待重算" in md
    assert "评审分歧" in md and "st-0007" in md
    assert "结构指标" in md
