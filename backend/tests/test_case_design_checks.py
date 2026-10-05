from aitester.case_design.checks import build_universe, run_checks


def _nodes():
    return {
        "chain": [
            {"id": "ch-0001", "type": "chain", "level": 1, "parent": "", "priority": "P0", "state": "approved"},
            {"id": "ch-0002", "type": "chain", "level": 2, "parent": "ch-0001", "priority": "P0", "state": "approved"},
        ],
        "story": [
            {"id": "st-0001", "type": "story", "chains": ["ch-0002"], "priority": "P1", "state": "approved"},
        ],
        "point": [
            {"id": "pt-0001", "type": "point", "story": "st-0001", "priority": "P1",
             "directions": ["正向"], "entities": ["实体甲"], "state": "approved"},
        ],
    }


def test_universe_merges_drafts_and_kb_with_scope():
    uni = build_universe(_nodes(), {"chain": [], "story": [], "point": []},
                         {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}})
    assert uni["chains"]["ch-0001"]["in_scope"] is True
    assert uni["stories"]["st-0001"]["state"] == "approved"


def test_clean_tree_passes_with_zero_hard():
    out = run_checks(build_universe(_nodes(), {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    assert out["hard"] == []
    assert out["report"]["empty_seam"] == 0


def test_broken_parent_cross_level_and_priority():
    nodes = _nodes()
    nodes["chain"][1]["parent"] = "ch-9999"                       # 父引用破损
    nodes["story"][0]["chains"] = ["ch-0002"]
    nodes["point"][0]["priority"] = "P2"
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    codes = {f["code"] for f in out["hard"]}
    assert "broken_parent" in codes and "priority_violation" in codes


def test_cross_level_ref_and_stale_reference():
    nodes = _nodes()
    nodes["chain"][1]["parent"] = "ch-0001"
    nodes["chain"][1]["level"] = 1                                # 与父同层 = 跳层/层级错
    nodes["point"][0]["state"] = "stale_pending"
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    codes = {f["code"] for f in out["hard"]}
    assert "cross_level" in codes and "unapproved_ref" in codes


def test_empty_chain_and_empty_story():
    nodes = _nodes()
    nodes["chain"].append({"id": "ch-0003", "type": "chain", "level": 2, "parent": "ch-0001",
                           "priority": "P1", "state": "approved"})
    nodes["story"].append({"id": "st-0002", "type": "story", "chains": ["ch-0002"],
                           "priority": "P1", "state": "approved"})
    scope = {"chains": {"ch-0001", "ch-0002", "ch-0003"}, "stories": {"st-0001", "st-0002"}}
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []}, scope),
                     claims=[], matrix_cells=[], unresolved={})
    codes = [f["code"] for f in out["hard"]]
    assert "empty_chain" in codes          # ch-0003 无故事
    assert "empty_story" in codes          # st-0002 无点


def test_chain_parent_cycle_reported_as_broken_parent():
    # R-11：闭合父环在大纲里整枝渲染不出来；归入「父引用完整」（broken_parent）家族，
    # 环上每个参与节点报一条，不新增第七个 code。本用例同时验证检查必然终止（无死循环）。
    nodes = {
        "chain": [
            {"id": "ch-0001", "type": "chain", "level": 1, "parent": "ch-0002",
             "priority": "P0", "state": "approved"},
            {"id": "ch-0002", "type": "chain", "level": 2, "parent": "ch-0001",
             "priority": "P0", "state": "approved"},
        ],
        "story": [],
        "point": [],
    }
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}}),
                     claims=[], matrix_cells=[], unresolved={})
    hits = [f for f in out["hard"] if f["code"] == "broken_parent"]
    assert {f["where"] for f in hits} == {"ch-0001", "ch-0002"}


def test_report_counters():
    out = run_checks(build_universe(_nodes(), {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[{"ref": "c1", "verdict": "unclaimed"}, {"ref": "c2", "verdict": "covered"}],
                     matrix_cells=[{"entity": "实体甲", "story": "st-0002", "verdict": "not_needed", "reason": ""},
                                   {"entity": "实体乙", "story": "st-0002", "verdict": "not_needed", "reason": "不涉及"}],
                     unresolved={"chain": [{"ref": "op-01"}]})
    assert out["report"] == {"empty_seam": 1, "matrix_unreasoned": 1, "unresolved": 1}
