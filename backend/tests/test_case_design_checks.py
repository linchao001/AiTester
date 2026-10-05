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
    nodes["point"][0]["priority"] = "P0"                          # 测试点优先级高于所属故事（R-12 方向）
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    codes = {f["code"] for f in out["hard"]}
    assert "broken_parent" in codes and "priority_violation" in codes


def test_story_priority_above_chain_fires_once():
    # R-12：P0 故事挂在 P2 链路下 = 有一层定级有误，须恰好一条 priority_violation。
    nodes = _nodes()
    nodes["chain"][1]["priority"] = "P2"
    nodes["story"][0]["priority"] = "P0"
    nodes["point"][0]["priority"] = "P2"
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    hits = [f for f in out["hard"] if f["code"] == "priority_violation"]
    assert [(f["layer"], f["where"]) for f in hits] == [("story", "st-0001")]
    assert "高于" in hits[0]["detail"]


def test_point_priority_above_story_fires_once():
    nodes = _nodes()
    nodes["point"][0]["priority"] = "P0"                          # 故事 P1
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    hits = [f for f in out["hard"] if f["code"] == "priority_violation"]
    assert [(f["layer"], f["where"]) for f in hits] == [("point", "pt-0001")]


def test_child_chain_priority_above_parent_fires_once():
    nodes = _nodes()
    nodes["chain"][0]["priority"] = "P1"
    nodes["chain"][1]["priority"] = "P0"                          # 子链路高于父链路
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    hits = [f for f in out["hard"] if f["code"] == "priority_violation"]
    assert [(f["layer"], f["where"]) for f in hits] == [("chain", "ch-0002")]


def test_descending_priority_down_the_tree_is_legal():
    # 向上聚合语义：P0 链路 → P2 故事 → P2 测试点是正常降级覆盖，零 hard。
    nodes = _nodes()
    nodes["story"][0]["priority"] = "P2"
    nodes["point"][0]["priority"] = "P2"
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    assert out["hard"] == []


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


def test_empty_chain_uses_subtree_story_coverage_not_direct_claim():
    # 只有子链路才有用户故事：祖先链经子孙链路认领即非空；真正无故事的分支才报。
    nodes = _nodes()
    nodes["chain"] += [
        {"id": "ch-0003", "type": "chain", "level": 3, "parent": "ch-0002",
         "priority": "P0", "state": "approved"},
        {"id": "ch-0004", "type": "chain", "level": 3, "parent": "ch-0002",
         "priority": "P0", "state": "approved"},
    ]
    nodes["story"][0]["chains"] = ["ch-0003"]
    scope = {"chains": {"ch-0001", "ch-0002", "ch-0003", "ch-0004"}, "stories": {"st-0001"}}
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []}, scope),
                     claims=[], matrix_cells=[], unresolved={})
    assert {f["where"] for f in out["hard"] if f["code"] == "empty_chain"} == {"ch-0004"}
    # 父指针成环时 covered 上行走查也要终止：环上链有故事 → 环内链都非空，其余照报
    cyc = _nodes()
    cyc["chain"] += [
        {"id": "ch-0003", "type": "chain", "level": 3, "parent": "ch-0002",
         "priority": "P0", "state": "approved"},
        {"id": "ch-0004", "type": "chain", "level": 3, "parent": "ch-0002",
         "priority": "P0", "state": "approved"},
    ]
    cyc["chain"][0]["parent"] = "ch-0002"
    cyc["story"][0]["chains"] = ["ch-0002"]
    out = run_checks(build_universe(cyc, {"chain": [], "story": [], "point": []}, scope),
                     claims=[], matrix_cells=[], unresolved={})
    assert {f["where"] for f in out["hard"] if f["code"] == "empty_chain"} == {"ch-0003", "ch-0004"}


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
