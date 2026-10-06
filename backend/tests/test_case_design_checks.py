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
    nodes["chain"].append({"id": "ch-0003", "type": "chain", "level": 3, "parent": "ch-0001",
                           "priority": "P0", "state": "approved"})  # 父 level1 → 自己 level3 = 跳层
    nodes["story"][0]["chains"] = ["ch-0002"]
    nodes["point"][0]["priority"] = "P0"                          # 测试点优先级高于所属故事（R-12 方向）
    scope = {"chains": {"ch-0001", "ch-0002", "ch-0003"}, "stories": {"st-0001"}}
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []}, scope),
                     claims=[], matrix_cells=[], unresolved={})
    by_code = {f["code"] for f in out["hard"]}
    assert {"broken_parent", "cross_level", "priority_violation"} <= by_code
    hits = {(f["code"], f["layer"], f["where"]) for f in out["hard"]}
    assert ("broken_parent", "chain", "ch-0002") in hits          # 破损报在被点名的子链上
    assert ("cross_level", "chain", "ch-0003") in hits            # 跳层报在层级错的那一条上


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
    assert out["report"] == {"empty_seam": 1, "matrix_unreasoned": 1, "unresolved": 1,
                             "point_missing_directions": 0}   # 范围内点均带方向 ⇒ 0


# ---------------------------------------------------------------- A-M1 report 断言方向缺失数

def test_report_counts_in_scope_points_missing_directions():
    # A-M1：spec 验收数字线列「断言方向缺失数（④）」，但旧 report 只 3 项；方向非空此前只靠点层
    # schema，KB 存量点 directions:[] 时门零信号。加确定性计数：范围内点 directions 空即计，且只进 report 不进 hard。
    nodes = {
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "priority": "P0", "state": "approved"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"],
                   "priority": "P1", "state": "approved"}],
        "point": [
            {"id": "pt-0001", "type": "point", "story": "st-0001", "priority": "P1",
             "directions": [], "entities": ["实体甲"], "state": "approved"},        # 缺方向，范围内
            {"id": "pt-0002", "type": "point", "story": "st-0001", "priority": "P1",
             "directions": ["正向"], "entities": ["实体乙"], "state": "approved"},
        ],
    }
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    assert out["report"]["point_missing_directions"] == 1
    # 归 report，绝不进 hard（否则 T12 数字线口径被改）
    assert not any(f["code"] == "point_missing_directions" for f in out["hard"])
    assert not any("direction" in str(f["code"]) for f in out["hard"])


def test_report_direction_gap_ignores_out_of_scope_points():
    # 范围守卫同源：范围外 KB 存量点缺方向不得计入（本次任务没动过、修复指令也改不到）。
    kb = {
        "chain": [{"id": "ch-9001", "type": "chain", "level": 1, "parent": "",
                   "priority": "P1", "state": "kb"}],
        "story": [{"id": "st-9001", "type": "story", "chains": ["ch-9001"],
                   "priority": "P1", "state": "kb"}],
        "point": [{"id": "pt-9001", "type": "point", "story": "st-9001", "priority": "P1",
                   "directions": [], "entities": ["实体丙"], "state": "kb"}],
    }
    uni = build_universe({"chain": [], "story": [], "point": []}, kb,
                         {"chains": set(), "stories": set()})
    out = run_checks(uni, claims=[], matrix_cells=[], unresolved={})
    assert out["report"]["point_missing_directions"] == 0


def test_out_of_scope_parent_cycle_does_not_swallow_in_scope_branch():
    # I-1（控制方复现 verify_final_A.py）：KB 存量 ch-9001↔ch-9002 互指成闭合环且都判为范围外；
    # 草稿 ch-0001(parent=ch-9001) 与其下故事/点全部合法、在范围内。旧行为：环体全在范围外 ⇒
    # add() 的 R-13 守卫把 broken_parent 静音掉「范围内受害方」ch-0001，hard==[]，唯一人审门拿到
    # 假完整大纲。修法：对范围内链路沿 parent 走祖先、命中闭合环 ⇒ 报在可修方（草稿链）。
    kb = {
        "chain": [
            {"id": "ch-9001", "type": "chain", "level": 1, "parent": "ch-9002",
             "priority": "P1", "state": "kb"},
            {"id": "ch-9002", "type": "chain", "level": 1, "parent": "ch-9001",
             "priority": "P1", "state": "kb"},
        ],
        "story": [], "point": [],
    }
    draft = {
        "chain": [{"id": "ch-0001", "type": "chain", "level": 2, "parent": "ch-9001",
                   "priority": "P1", "state": "approved", "op": "upsert"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"],
                   "priority": "P1", "state": "approved"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001", "priority": "P1",
                   "directions": ["正向"], "entities": ["实体甲"], "state": "approved"}],
    }
    scope = {"chains": {"ch-0001"}, "stories": {"st-0001"}}
    uni = build_universe(draft, kb, scope)
    assert uni["chains"]["ch-9001"]["in_scope"] is False           # 环体确在范围外
    assert uni["chains"]["ch-0001"]["in_scope"] is True             # 受害方确在范围内
    out = run_checks(uni, claims=[], matrix_cells=[], unresolved={})
    # 范围内草稿链 ch-0001 因祖先闭合成环而报 broken_parent；环体自身（全在范围外）不报。
    hits = {(f["code"], f["layer"], f["where"]) for f in out["hard"]}
    assert ("broken_parent", "chain", "ch-0001") in hits
    assert not any(f["where"] in ("ch-9001", "ch-9002") for f in out["hard"])
    detail = next(f["detail"] for f in out["hard"] if f["where"] == "ch-0001")
    assert "闭合成环" in detail and "无法呈递" in detail


def test_in_scope_branch_under_a_clean_parent_is_not_reported_as_cycled():
    # §1 修法的边界：只有祖先链「真的进入闭合环」才报，正常挂到范围内合法父下不得误报。
    nodes = _nodes()                                        # ch-0001(root)←ch-0002←story←point
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    assert out["hard"] == []


# ---------------------------------------------------------------- I-2 链路自状态 / 引用边状态

def test_stale_chain_is_caught_by_self_state_and_downstream_reference():
    # I-2（控制方复现）：范围内链路 state=stale_pending、其下故事/点 approved ⇒ 旧实现零信号
    # （只有 story/point 循环查自身 state，链路无自状态检查；引用边从不比较被引用方状态）。
    # 而 instructions 承诺「unapproved_ref：被引用的节点必须已过审；失效层节点不得被引用」。
    kb = {
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "priority": "P1", "state": "stale_pending"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"],
                   "priority": "P1", "state": "approved"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001", "priority": "P1",
                   "directions": ["正向"], "entities": ["实体甲"], "state": "approved"}],
    }
    out = run_checks(build_universe({}, kb, {}), claims=[], matrix_cells=[], unresolved={})
    got = {(f["code"], f["layer"], f["where"]) for f in out["hard"]}
    assert ("unapproved_ref", "chain", "ch-0001") in got       # 链路自身失效
    assert ("unapproved_ref", "story", "st-0001") in got        # 故事引用了未过审链路（引用边）


def test_child_chain_referencing_stale_parent_reports_edge_on_child():
    # 子链路↔父链路引用边：父链失效 ⇒ 自状态报在父（ch-0001）、引用边报在可修方子链（ch-0002）。
    nodes = _nodes()
    nodes["chain"][0]["state"] = "stale_pending"
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    got = {(f["code"], f["layer"], f["where"]) for f in out["hard"]}
    assert ("unapproved_ref", "chain", "ch-0001") in got        # 父链自状态
    assert ("unapproved_ref", "chain", "ch-0002") in got        # 子链引用未过审父


def test_point_edge_reports_referencer_not_double_counting_the_referenced_story():
    # 「与自状态检查不重复计数」：失效故事由自状态负责（where=故事），点侧引用边只另报
    # 「引用了未过审的上游」（where=点）——两条 where 不同，各司其职。
    nodes = {
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "priority": "P1", "state": "approved"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"],
                   "priority": "P1", "state": "active"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001", "priority": "P1",
                   "directions": ["正向"], "entities": ["实体甲"], "state": "approved"}],
    }
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    got = [(f["code"], f["layer"], f["where"]) for f in out["hard"]]
    assert ("unapproved_ref", "story", "st-0001") in got         # 自状态（故事本身失效）
    assert ("unapproved_ref", "point", "pt-0001") in got          # 引用边（点引用失效故事）
    # 点自身过审，不得因引用边被「重复」计成点的自状态违例——两条恰为上述两条，不多不少。
    assert {g for g in got if g[0] == "unapproved_ref"} == {
        ("unapproved_ref", "story", "st-0001"), ("unapproved_ref", "point", "pt-0001")}


def test_approved_edges_produce_no_reference_violations():
    # 护栏：三层全 approved（含 KB「kb」态）⇒ 自状态与引用边都不响，零 unapproved_ref。
    out = run_checks(build_universe(_nodes(), {"chain": [], "story": [], "point": []},
                                    {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    assert not any(f["code"] == "unapproved_ref" for f in out["hard"])


# ---------------------------------------------------------------- R-13 范围门控

_KB_OUT_OF_SCOPE = {
    "chain": [
        {"id": "ch-9001", "type": "chain", "level": 1, "parent": "", "priority": "P1", "state": "kb"},
        # 父引用破损（父不在宇宙，层级无从比对）
        {"id": "ch-9002", "type": "chain", "level": 3, "parent": "ch-9003", "priority": "P0", "state": "kb"},
    ],
    "story": [
        # 未过审 + 优先级高于所属链路（链路 P1，故事 P0）
        {"id": "st-9001", "type": "story", "chains": ["ch-9001"], "priority": "P0", "state": "stale_pending"},
    ],
    "point": [
        {"id": "pt-9001", "type": "point", "story": "st-9001", "priority": "P1",
         "directions": ["正向"], "entities": ["实体丙"], "state": "stale_pending"},
    ],
}


def test_violations_on_out_of_scope_nodes_are_not_reported():
    # R-13（评审复现）：窄任务「针对链路 ch-0001」时，未动过的 KB 存量自带破损也须被解析进宇宙，
    # 但一条都不许进 hard——修复指令限定「只改被点名的 design/drafts/ 文件」，主智能体无法合法
    # 修复 KB 数据，报出来只会烧满 5 轮修复环然后 halted；大纲也只渲染本任务节点。
    uni = build_universe(_nodes(), _KB_OUT_OF_SCOPE,
                         {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}})
    assert uni["chains"]["ch-9002"]["in_scope"] is False          # 宇宙是全量（引用解析要用）
    assert uni["stories"]["st-9001"]["in_scope"] is False
    out = run_checks(uni, claims=[], matrix_cells=[], unresolved={})
    assert out["hard"] == []                                      # 破损仍在宇宙里，只是不报
    # 同一批破损一旦进入范围就照报——静音来自范围门，不是检查本身失效
    wide = run_checks(build_universe(_nodes(), _KB_OUT_OF_SCOPE,
                                     {"chains": {"ch-0001", "ch-0002", "ch-9001", "ch-9002"},
                                      "stories": {"st-0001", "st-9001"}}),
                      claims=[], matrix_cells=[], unresolved={})
    assert {(f["code"], f["where"]) for f in wide["hard"]} >= {
        ("broken_parent", "ch-9002"), ("unapproved_ref", "st-9001"),
        ("priority_violation", "st-9001"), ("unapproved_ref", "pt-9001")}


def test_in_scope_node_referencing_out_of_scope_parent_still_reports():
    # R-13 的另一半：父/所属节点在范围外**照样解析**（父可以合法地活在范围外），
    # 被点名的节点在范围内就照报。
    kb_rows = {"chain": [{"id": "ch-9001", "type": "chain", "level": 1, "parent": "",
                          "priority": "P2", "state": "kb"}], "story": [], "point": []}
    nodes = {"chain": [], "story": [
        {"id": "st-0001", "type": "story", "chains": ["ch-9999", "ch-9001"],
         "priority": "P0", "state": "approved"},                  # 一条断链 + 一条优先级高于父
    ], "point": [
        {"id": "pt-0001", "type": "point", "story": "st-0001", "priority": "P0",
         "directions": ["正向"], "entities": ["实体甲"], "state": "approved"},
    ]}
    out = run_checks(build_universe(nodes, kb_rows, {"chains": set(), "stories": {"st-0001"}}),
                     claims=[], matrix_cells=[], unresolved={})
    assert {(f["code"], f["layer"], f["where"]) for f in out["hard"]} == {
        ("broken_parent", "story", "st-0001"),                    # ch-9999 不在宇宙
        ("priority_violation", "story", "st-0001"),               # 父 ch-9001 在范围外，仍比较
    }


def test_point_in_scope_is_inherited_from_its_story():
    # 计划的 scope 只有 chains/stories 两键（T8 `_scope_for_checks`），**KB 存量**点层的范围
    # 从所属故事继承；否则 R-13 的守卫会把存量的点层检查全部静音（后人新加的点层检查也不响）。
    kb_rows = {
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "priority": "P0", "state": "approved"}],
        "story": [
            {"id": "st-0001", "type": "story", "chains": ["ch-0001"], "priority": "P1", "state": "approved"},
            {"id": "st-0002", "type": "story", "chains": ["ch-0001"], "priority": "P1", "state": "approved"},
        ],
        "point": [
            {"id": "pt-0001", "type": "point", "story": "st-0001", "priority": "P1",
             "directions": ["正向"], "entities": ["实体甲"], "state": "stale_pending"},
            {"id": "pt-0002", "type": "point", "story": "st-0002", "priority": "P1",
             "directions": ["正向"], "entities": ["实体乙"], "state": "stale_pending"},
        ],
    }
    scope = {"chains": {"ch-0001"}, "stories": {"st-0001"}}
    uni = build_universe({"chain": [], "story": [], "point": []}, kb_rows, scope)
    assert uni["points"]["pt-0001"]["in_scope"] is True           # 故事在范围内 → 点继承
    assert uni["points"]["pt-0002"]["in_scope"] is False
    hits = run_checks(uni, claims=[], matrix_cells=[], unresolved={})["hard"]
    assert {(f["code"], f["where"]) for f in hits} == {("unapproved_ref", "pt-0001")}


def test_draft_rows_are_always_in_scope():
    # R-17：scope 只约束 KB 存量。草稿行是本次任务刚产出的文件——② 的修复指令能改的正是它们，
    # 按 scope 静音等于把门自己该拦的东西放过去。
    scope = {"chains": set(), "stories": set()}
    uni = build_universe(_sparse_nodes(), {"chain": [], "story": [], "point": []}, scope)
    assert all(n["in_scope"] is True for b in ("chains", "stories", "points")
               for n in uni[b].values())
    assert sorted((f["code"], f["where"]) for f in
                  run_checks(uni, claims=[], matrix_cells=[], unresolved={})["hard"]) == [
        ("empty_chain", "ch-0002"), ("empty_story", "st-0001")]


def test_draft_point_with_dangling_story_still_reports_broken_parent():
    # R-17 的复现路径（旧写法实测静音）：窄任务里草稿点引用一个既不在宇宙、也不在
    # scope["stories"] 里的故事 id —— 继承表达式判它「不在范围」，于是 broken_parent 整条不响，
    # 大纲带着断链的点去过人审门。草稿点必须照报。
    nodes = {
        "chain": [],
        "story": [],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-9999", "priority": "P1",
                   "directions": ["正向"], "entities": ["实体甲"], "state": "approved"}],
    }
    uni = build_universe(nodes, {"chain": [], "story": [], "point": []},
                         {"chains": set(), "stories": set()})
    assert uni["points"]["pt-0001"]["in_scope"] is True
    hits = run_checks(uni, claims=[], matrix_cells=[], unresolved={})["hard"]
    assert {(f["code"], f["layer"], f["where"]) for f in hits} == {
        ("broken_parent", "point", "pt-0001")}


def test_in_scope_chain_claimed_only_by_out_of_scope_story_is_not_empty():
    # A-M2：checks 的「认领关系读全宇宙（范围外的故事也算认领）」曾被变异成按 in_scope 过滤后 48 测全绿。
    # 本用例钉死该语义：范围内链路**仅**被范围外的 KB 存量故事认领 ⇒ 已非空，不报 empty_chain。
    nodes = {
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "priority": "P1", "state": "approved", "op": "upsert"}],
        "story": [], "point": [],
    }
    kb = {
        "chain": [],
        "story": [{"id": "st-9001", "type": "story", "chains": ["ch-0001"],
                   "priority": "P1", "state": "kb"}],
        "point": [],
    }
    # ch-0001 是草稿恒在范围；st-9001 是 KB 存量且不在 scope.stories ⇒ 范围外。
    uni = build_universe(nodes, kb, {"chains": set(), "stories": set()})
    assert uni["chains"]["ch-0001"]["in_scope"] is True
    assert uni["stories"]["st-9001"]["in_scope"] is False
    out = run_checks(uni, claims=[], matrix_cells=[], unresolved={})
    assert not any(f["code"] == "empty_chain" for f in out["hard"])   # 被范围外故事认领 ⇒ 非空
    assert out["hard"] == []


def test_in_scope_chain_with_no_claim_at_all_still_reports_empty():
    # 反向护栏，确保上一条不是因为「empty_chain 整个失效」才不报：无人认领的范围内链路照报。
    nodes = {
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "priority": "P1", "state": "approved", "op": "upsert"}],
        "story": [], "point": [],
    }
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []},
                                    {"chains": set(), "stories": set()}),
                     claims=[], matrix_cells=[], unresolved={})
    assert {(f["code"], f["where"]) for f in out["hard"]} == {("empty_chain", "ch-0001")}


# ------------------------------------------------------------------ R-14 空范围

def _sparse_nodes():
    return {
        "chain": [
            {"id": "ch-0001", "type": "chain", "level": 1, "parent": "", "priority": "P0", "state": "approved"},
            {"id": "ch-0002", "type": "chain", "level": 2, "parent": "ch-0001",
             "priority": "P0", "state": "approved"},              # 无故事认领
        ],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"],
                   "priority": "P0", "state": "approved"}],        # 无测试点
        "point": [],
    }


def test_missing_or_empty_scope_fails_closed():
    # R-14：缺省/空 scope 曾让所有节点都「不在范围内」，全量树任务与「检查根本没跑」在大纲上
    # 同为「hard：全部为 0」——现在按全量处理（fail closed）。
    for scope in ({}, None):
        uni = build_universe(_sparse_nodes(), {"chain": [], "story": [], "point": []}, scope)
        assert all(n["in_scope"] is True for b in ("chains", "stories", "points")
                   for n in uni[b].values())
        out = run_checks(uni, claims=[], matrix_cells=[], unresolved={})
        assert sorted((f["code"], f["where"]) for f in out["hard"]) == [
            ("empty_chain", "ch-0002"), ("empty_story", "st-0001")]


def test_explicitly_empty_scope_sets_stay_narrowed():
    # 与「没给范围」区分：显式空集合是调用方有意收窄（T8 在 F=-1 时给 `{"chains":set(),"stories":set()}`
    # 表示本 run 不进 KB 存量那一层），对**存量**不做 fail closed。
    uni = build_universe({"chain": [], "story": [], "point": []}, _sparse_nodes(),
                         {"chains": set(), "stories": set()})
    assert all(n["in_scope"] is False for b in ("chains", "stories", "points") for n in uni[b].values())
    assert run_checks(uni, claims=[], matrix_cells=[], unresolved={})["hard"] == []


# ------------------------------------------------------------------ R-15 KB 合并

def test_kb_rows_merge_into_universe_and_kb_state_passes_reference_check():
    # R-15：T8 `_gate_universe` 每次都喂非空 kb_rows，而旧测试全传空表——KB 分支零覆盖。
    # 若默认 state="kb" 或 `"kb" in _APPROVED` 退掉，每个被引用的 KB 故事/测试点都会报
    # unapproved_ref，门恒 halted。
    kb_rows = {
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "", "priority": "P0"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"], "priority": "P0"}],
        "point": [],
    }
    nodes = {"chain": [], "story": [],
             "point": [{"id": "pt-0001", "type": "point", "story": "st-0001", "priority": "P0",
                        "directions": ["正向"], "entities": ["实体甲"]}]}
    uni = build_universe(nodes, kb_rows, {"chains": set(), "stories": {"st-0001"}})
    assert uni["chains"]["ch-0001"]["state"] == "kb"              # KB 行默认状态
    assert uni["stories"]["st-0001"]["state"] == "kb"
    assert uni["points"]["pt-0001"]["state"] == "approved"         # 草稿行默认状态
    assert uni["points"]["pt-0001"]["in_scope"] is True            # 草稿点引用 KB 故事
    out = run_checks(uni, claims=[], matrix_cells=[], unresolved={})
    assert out["hard"] == []                                       # 零 unapproved_ref


def test_draft_row_overrides_same_id_kb_row():
    kb_rows = {
        "chain": [
            {"id": "ch-0001", "type": "chain", "level": 1, "parent": "", "priority": "P2",
             "state": "kb", "business_scope": "存量范围"},
            {"id": "ch-9001", "type": "chain", "level": 1, "parent": "", "priority": "P0", "state": "kb"},
        ],
        "story": [], "point": [],
    }
    nodes = {"chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                        "priority": "P0", "state": "approved"}], "story": [], "point": []}
    uni = build_universe(nodes, kb_rows, {"chains": {"ch-0001"}, "stories": set()})
    assert uni["chains"]["ch-0001"]["priority"] == "P0"            # 同 id：草稿覆盖 KB
    assert uni["chains"]["ch-0001"]["state"] == "approved"
    assert "business_scope" not in uni["chains"]["ch-0001"]
    assert uni["chains"]["ch-9001"]["state"] == "kb"               # 只在 KB 的存量仍并进宇宙（并集）
    assert uni["chains"]["ch-9001"]["in_scope"] is False


def test_op_delete_nodes_are_excluded_from_universe():
    nodes = _nodes()
    nodes["chain"].append({"id": "ch-0008", "type": "chain", "level": 2, "parent": "ch-9999",
                           "priority": "P0", "state": "approved", "op": "delete", "reason": "并入上级"})
    nodes["story"].append({"id": "st-0002", "type": "story", "chains": ["ch-0008"],
                           "priority": "P0", "state": "approved"})   # 引用被删链路 = 断链（人看得见）
    nodes["point"].append({"id": "pt-0008", "type": "point", "story": "st-9999", "priority": "P0",
                           "state": "approved", "op": "delete", "reason": "废弃"})
    kb_rows = dict(_KB_OUT_OF_SCOPE)
    kb_rows["story"] = _KB_OUT_OF_SCOPE["story"] + [
        {"id": "st-9002", "type": "story", "chains": ["ch-9998"], "priority": "P1",
         "state": "kb", "op": "delete", "reason": "存量下线"}]
    scope = {"chains": {"ch-0001", "ch-0002", "ch-0008"}, "stories": {"st-0001", "st-0002", "st-9002"}}
    uni = build_universe(nodes, kb_rows, scope)
    assert "ch-0008" not in uni["chains"] and "pt-0008" not in uni["points"]
    assert "st-9002" not in uni["stories"]                        # KB 侧同样跳过 delete
    out = run_checks(uni, claims=[], matrix_cells=[], unresolved={})
    # 删除节点自身不参与检查（父引用破损不报），但它留下的引用破损照常报。
    assert {(f["code"], f["layer"], f["where"]) for f in out["hard"]} == {
        ("broken_parent", "story", "st-0002"), ("empty_story", "point", "st-0002")}


def test_chain_parent_is_itself_reports_broken_parent():
    nodes = _nodes()
    nodes["chain"].append({"id": "ch-0003", "type": "chain", "level": 2, "parent": "ch-0003",
                           "priority": "P0", "state": "approved"})  # 自指 = 最小环
    scope = {"chains": {"ch-0001", "ch-0002", "ch-0003"}, "stories": {"st-0001"}}
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []}, scope),
                     claims=[], matrix_cells=[], unresolved={})
    assert {f["where"] for f in out["hard"] if f["code"] == "broken_parent"} == {"ch-0003"}


# ---------------------------------------------------------------------- Minors

def test_priority_detail_uses_effective_value_when_missing():
    # M-a：rank() 把缺省优先级兜底成 P1，detail 也必须说 P1——「优先级 None 高于其父…」人类读不懂。
    nodes = {
        "chain": [
            {"id": "ch-0001", "type": "chain", "level": 1, "parent": "", "priority": "P2", "state": "approved"},
            {"id": "ch-0002", "type": "chain", "level": 2, "parent": "ch-0001", "state": "approved"},
        ],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"], "state": "approved"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001", "priority": "P0",
                   "directions": ["正向"], "entities": ["实体甲"], "state": "approved"}],
    }
    scope = {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001"}}
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []}, scope),
                     claims=[], matrix_cells=[], unresolved={})
    hits = [f for f in out["hard"] if f["code"] == "priority_violation"]
    assert [(f["layer"], f["where"]) for f in hits] == [("chain", "ch-0002"), ("story", "st-0001"),
                                                        ("point", "pt-0001")]
    assert all("None" not in f["detail"] for f in hits)
    assert hits[0]["detail"] == "优先级 P1 高于其父 ch-0001 的 P2"


def test_non_integer_level_reports_cross_level_instead_of_crashing():
    # M-b：`int(非数字)` 曾让 run_checks 直接抛异常——门里的异常比报出来的违例更糟。
    nodes = {
        "chain": [
            {"id": "ch-0001", "type": "chain", "level": 1, "parent": "", "priority": "P0", "state": "approved"},
            {"id": "ch-0002", "type": "chain", "level": "二层", "parent": "ch-0001",
             "priority": "P0", "state": "approved"},                # 子 level 非整数
            {"id": "ch-0003", "type": "chain", "level": None, "parent": "ch-0001",
             "priority": "P0", "state": "approved"},                # 兜底 0 → 层级不连续
            {"id": "ch-0004", "type": "chain", "level": 2, "parent": "ch-0005",
             "priority": "P0", "state": "approved"},                # 父 level 非整数
            {"id": "ch-0005", "type": "chain", "level": "根", "parent": "",
             "priority": "P0", "state": "approved"},                # 根：无父可校，不报
            {"id": "ch-0006", "type": "chain", "level": "六层", "parent": "ch-0005",
             "priority": "P0", "state": "approved"},                # 父子都坏：只报一条，点名子
        ],
        "story": [], "point": [],
    }
    scope = {"chains": {f"ch-000{i}" for i in range(1, 7)}, "stories": set()}
    out = run_checks(build_universe(nodes, {"chain": [], "story": [], "point": []}, scope),
                     claims=[], matrix_cells=[], unresolved={})
    assert {(f["layer"], f["where"]) for f in out["hard"] if f["code"] == "cross_level"} == {
        ("chain", "ch-0002"), ("chain", "ch-0003"), ("chain", "ch-0004"), ("chain", "ch-0006")}
    both = [f for f in out["hard"]
            if f["code"] == "cross_level" and f["where"] == "ch-0006"]
    assert len(both) == 1 and "六层" in both[0]["detail"]
    bad = next(f for f in out["hard"]
               if f["code"] == "cross_level" and f["where"] == "ch-0002")
    assert "不是整数" in bad["detail"] and "二层" in bad["detail"]


def _messy_nodes():
    return {
        "chain": [
            {"id": "ch-0003", "type": "chain", "level": 2, "parent": "ch-9999",
             "priority": "P0", "state": "approved"},                # broken_parent
            {"id": "ch-0002", "type": "chain", "level": 3, "parent": "ch-0001",
             "priority": "P0", "state": "approved"},                # cross_level
            {"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
             "priority": "P0", "state": "approved"},
        ],
        "story": [
            {"id": "st-0002", "type": "story", "chains": ["ch-0002"],
             "priority": "P0", "state": "pending_review"},          # unapproved_ref
            {"id": "st-0001", "type": "story", "chains": [], "priority": "P1", "state": "approved"},
        ],
        "point": [
            {"id": "pt-0002", "type": "point", "story": "st-0002", "priority": "P0",
             "directions": ["正向"], "entities": ["实体乙"], "state": "approved"},
            {"id": "pt-0001", "type": "point", "story": "st-0001", "priority": "P0",
             "directions": ["正向"], "entities": ["实体甲"], "state": "approved"},  # 高于所属故事
        ],
    }


_EXPECTED_ORDER = [("cross_level", "chain", "ch-0002"),
                   ("broken_parent", "chain", "ch-0003"),
                   ("broken_parent", "story", "st-0001"),
                   ("unapproved_ref", "story", "st-0002"),
                   ("priority_violation", "point", "pt-0001"),
                   ("unapproved_ref", "point", "pt-0002"),
                   ("empty_chain", "story", "ch-0003")]


def test_hard_list_order_does_not_follow_universe_insertion_order():
    # M-c：除环检测外各 emit 循环按 (层, where) 自然序稳定输出——同一份缺陷清单在修复环各轮、
    # 在人类与主智能体眼里必须同序，不能跟着草稿列举顺序漂。
    scope = {"chains": {"ch-0001", "ch-0002", "ch-0003"}, "stories": {"st-0001", "st-0002"}}
    forward = build_universe(_messy_nodes(), {"chain": [], "story": [], "point": []}, scope)
    shuffled_lists = {"chain": [dict(c) for c in reversed(_messy_nodes()["chain"])],
                      "story": [dict(s) for s in reversed(_messy_nodes()["story"])],
                      "point": [dict(p) for p in reversed(_messy_nodes()["point"])]}
    reversed_ = build_universe(shuffled_lists, {"chain": [], "story": [], "point": []}, scope)
    assert list(forward["chains"]) != list(reversed_["chains"])      # 宇宙插入顺序确实不同
    got = [(f["code"], f["layer"], f["where"]) for f in
           run_checks(forward, claims=[], matrix_cells=[], unresolved={})["hard"]]
    assert got == _EXPECTED_ORDER
    assert [(f["code"], f["layer"], f["where"]) for f in
            run_checks(reversed_, claims=[], matrix_cells=[], unresolved={})["hard"]] == _EXPECTED_ORDER
