"""④ 大纲门确定性检查（A12）：hard 必须清零（修复环→halted），report 随大纲呈递。

不使用 LLM；不负责找漏（那是 ①②③ 的职责，spec §3）。
"""

from __future__ import annotations

from aitester.case_design.constants import CHAIN, LAYERS, POINT, PRIORITY_RANK, STORY

_APPROVED = ("approved", "kb")      # 允许被下游引用的节点状态（kb=存量真相，视为已过审）


def build_universe(nodes_by_layer: dict[str, list[dict]], kb_rows: dict[str, list[dict]],
                   scope: dict[str, set[str]]) -> dict:
    """引用宇宙 = 本任务草稿 ∪ KB 存量；in_scope 标记本次任务范围内的节点。"""
    uni: dict[str, dict[str, dict]] = {"chains": {}, "stories": {}, "points": {}}
    key_of = {CHAIN: "chains", STORY: "stories", POINT: "points"}
    for source, state in ((kb_rows, "kb"), (nodes_by_layer, None)):
        for layer in LAYERS:
            for row in source.get(layer, []) or []:
                if row.get("op") == "delete":
                    continue
                nid = str(row.get("id") or "")
                if not nid:
                    continue
                item = dict(row)
                item.setdefault("state", "kb" if state == "kb" else "approved")
                if scope:
                    scope_ids = scope.get("chains" if layer == CHAIN else
                                          "stories" if layer == STORY else "points", set())
                    item["in_scope"] = nid in scope_ids
                uni[key_of[layer]][nid] = item
    return uni


def _parent_cycle_members(chains: dict[str, dict]) -> set[str]:
    """「父引用完整」的闭环分支：找出所有处于闭合父环上的链路 id。

    outline 只从根（parent 为空）与孤儿（parent 不在宇宙内）起树，闭合环整枝渲染不出来，
    人审门会拿到静默丢分支的假完整大纲——结构破损，归入 hard。沿 parent 指针逐节点走路径，
    重访即成环，环体为路径中首现位置之后的后缀；走法每步消费一个节点，必然终止。
    """
    members: set[str] = set()
    for start in chains:
        path: list[str] = []
        seen: dict[str, int] = {}
        cur = start
        while cur in chains and cur not in seen:
            seen[cur] = len(path)
            path.append(cur)
            cur = str(chains[cur].get("parent") or "")
        if cur in seen:
            members.update(path[seen[cur]:])
    return members


def run_checks(universe: dict, claims: list[dict], matrix_cells: list[dict],
               unresolved: dict[str, list]) -> dict:
    chains, stories, points = universe["chains"], universe["stories"], universe["points"]
    hard: list[dict] = []

    def add(code: str, layer: str, where: str, detail: str) -> None:
        hard.append({"code": code, "layer": layer, "where": where, "detail": detail})

    rank = lambda v: PRIORITY_RANK.get(str(v or "P1"), 1)  # noqa: E731

    # 父引用完整：断链（parent 不在宇宙）与闭环（parent 互指成环）都算破损，共用 broken_parent；
    # 闭环按 R-11 不新增 code，环上每个参与节点报一条。
    cycle_members = _parent_cycle_members(chains)
    for cid in sorted(cycle_members):
        parent = str(chains[cid].get("parent") or "")
        add("broken_parent", CHAIN, cid, f"parent「{parent}」与祖先闭合成环，该分支在大纲中无法呈递")

    for cid, c in chains.items():
        parent = str(c.get("parent") or "")
        if parent:
            p = chains.get(parent)
            if p is None:
                add("broken_parent", CHAIN, cid, f"parent「{parent}」不在引用宇宙内")
            else:
                if int(c.get("level") or 0) != int(p.get("level") or 0) + 1:
                    add("cross_level", CHAIN, cid,
                        f"level={c.get('level')} 与父 {parent} level={p.get('level')} 不连续")
                # 优先级沿树（R-12）：违例 = 子节点优先级**高于**其父（rank 更小）。
                # 链路 priority 是业务分支的重要性、索引树向上聚合，P0 链路下挂 P1/P2
                # 属正常降级；反过来才说明某一层定级有误。三处比较方向一致：
                # 子链路↔父链路、故事↔所属链路、测试点↔所属故事。
                if rank(c.get("priority")) < rank(p.get("priority")):
                    add("priority_violation", CHAIN, cid,
                        f"优先级 {c.get('priority')} 高于其父 {parent} 的 {p.get('priority')}")
    for sid, s in stories.items():
        parents = [str(x) for x in (s.get("chains") or [])]
        if not parents:
            add("broken_parent", STORY, sid, "story 无 chains 引用")
        for cid in parents:
            p = chains.get(cid)
            if p is None:
                add("broken_parent", STORY, sid, f"chains「{cid}」不在引用宇宙内")
            elif rank(s.get("priority")) < rank(p.get("priority")):
                add("priority_violation", STORY, sid,
                    f"优先级 {s.get('priority')} 高于其父 {cid} 的 {p.get('priority')}")
        if str(s.get("state")) not in _APPROVED:
            add("unapproved_ref", STORY, sid, f"节点状态 {s.get('state')} 未过审")
    for pid, p in points.items():
        sid = str(p.get("story") or "")
        parent = stories.get(sid)
        if parent is None:
            add("broken_parent", POINT, pid, f"story「{sid}」不在引用宇宙内")
        else:
            if rank(p.get("priority")) < rank(parent.get("priority")):
                add("priority_violation", POINT, pid,
                    f"优先级 {p.get('priority')} 高于其父 {sid} 的 {parent.get('priority')}")
        if str(p.get("state")) not in _APPROVED:
            add("unapproved_ref", POINT, pid, f"节点状态 {p.get('state')} 未过审")

    # 空链路按**子树**口径：链路自身或其任一子孙链路有故事认领即非空。
    # 简报的直接认领版与其 clean-tree 用例矛盾（P0 根链 ch-0001 只经子链 ch-0002 挂故事，
    # 须零违例）；按用例裁定。从有故事的链沿 parent 指针向上盖到根，重访即停，父成环亦终止。
    story_chains = {str(cid) for s in stories.values() for cid in (s.get("chains") or [])}
    covered: set[str] = set()
    for start in story_chains:
        cur = start
        while cur in chains and cur not in covered:
            covered.add(cur)
            cur = str(chains[cur].get("parent") or "")
    for cid, c in chains.items():
        if c.get("in_scope") and cid not in covered:
            add("empty_chain", STORY, cid, "范围内链路没有任何故事认领（空链路）")
    point_stories = {str(p.get("story") or "") for p in points.values()}
    for sid, s in stories.items():
        if s.get("in_scope") and sid not in point_stories:
            add("empty_story", POINT, sid, "范围内故事没有任何测试点（空故事）")

    report = {
        "empty_seam": sum(1 for c in claims if c.get("verdict") == "unclaimed"),
        "matrix_unreasoned": sum(1 for c in matrix_cells
                                 if c.get("verdict") == "not_needed" and not str(c.get("reason") or "").strip()),
        "unresolved": sum(len(v) for v in unresolved.values()),
    }
    return {"hard": hard, "report": report}
