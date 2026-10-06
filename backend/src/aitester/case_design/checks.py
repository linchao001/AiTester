"""④ 大纲门确定性检查（A12）：hard 必须清零（修复环→halted），report 随大纲呈递。

不使用 LLM；不负责找漏（那是 ①②③ 的职责，spec §3）。
"""

from __future__ import annotations

from aitester.case_design.constants import CHAIN, LAYERS, POINT, PRIORITY_RANK, STORY

_APPROVED = ("approved", "kb")      # 允许被下游引用的节点状态（kb=存量真相，视为已过审）

# 层 → 引用宇宙桶名：build_universe 写入、run_checks 的 R-13 守卫读取，单一映射不散写。
_BUCKET = {CHAIN: "chains", STORY: "stories", POINT: "points"}
_BUCKET_ORDER: tuple[str, ...] = ("chains", "stories", "points")


def _priority(value: object) -> str:
    """生效优先级：缺省按 P1（与 rank 的兜底同源，M-a 用它拼 detail）。"""
    return str(value or "P1")


def _level(value: object) -> int | None:
    """生效层级：非整数返回 None（M-b）——层级坏掉是**违例**，不是让门崩掉的异常。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return None


def build_universe(nodes_by_layer: dict[str, list[dict]], kb_rows: dict[str, list[dict]],
                   scope: dict[str, set[str]]) -> dict:
    """引用宇宙 = 本任务草稿 ∪ KB 存量；in_scope 标记本次任务范围内的节点。

    范围语义（R-13/R-14/R-17）：
    - scope 缺省或为空 dict = 全量任务，**所有节点都在范围内**（fail closed）。
      「范围」绝不用「没打标」表达：否则全量树任务与「检查根本没跑」在大纲上同为 hard 全 0，
      人审门唯一的信号就此静默丢失。
    - **草稿行永远在范围内**（R-17）。scope 只用来判定 KB 存量哪些算「本任务没动过」。
      草稿就是本次任务刚产出的文件：② 的修复指令允许主智能体改的正是这些文件，把它的结构缺陷
      静音掉（旧写法：草稿点引用悬空 story id → 继承表达式判它不在范围 → broken_parent 整条不响）
      等于把门自己该拦的东西放过去。
    - KB 存量的点层范围**从所属故事继承**（计划的 scope 只有 chains/stories 两键，见 T8
      `_scope_for_checks`）；否则 R-13 的守卫会把存量的点层检查全部静音。
    - 显式给出但集合为空（如 `{"chains": set(), "stories": set()}`）是调用方的有意收窄
      （本 run 不进 KB 存量那一层），对存量不做 fail closed。
    """
    uni: dict[str, dict[str, dict]] = {"chains": {}, "stories": {}, "points": {}}
    story_scope = scope.get("stories", set()) if scope else set()
    for source, is_kb in ((kb_rows, True), (nodes_by_layer, False)):
        for layer in LAYERS:
            for row in source.get(layer, []) or []:
                if row.get("op") == "delete":
                    continue
                nid = str(row.get("id") or "")
                if not nid:
                    continue
                item = dict(row)
                item.setdefault("state", "kb" if is_kb else "approved")
                if not scope:
                    item["in_scope"] = True
                elif not is_kb:
                    item["in_scope"] = True
                elif layer == POINT:
                    item["in_scope"] = str(item.get("story") or "") in story_scope
                else:
                    item["in_scope"] = nid in scope.get(_BUCKET[layer], set())
                uni[_BUCKET[layer]][nid] = item
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
        # R-13：引用**解析**走全宇宙（父节点/所属故事可以合法地落在范围外，仍必须找得到），
        # 但只对**被点名的范围内节点**报违例。范围外节点是本次任务没动过的 KB 存量，修复指令
        # 限定「只改被点名的 design/drafts/ 文件」，主智能体无法合法修复——报出来只会白烧满
        # 5 轮修复环然后 halted；而大纲只渲染本任务节点，人也看不见这个 where。
        # 守卫按 where 定位节点，不按 layer：empty_chain/empty_story 的 layer 标注的是
        # 「缺失的那一层」，与被点名节点不在同一桶。
        for bucket in _BUCKET_ORDER:
            node = (universe.get(bucket) or {}).get(where)
            if node is not None:
                if not node.get("in_scope", True):
                    return
                break
        hard.append({"code": code, "layer": layer, "where": where, "detail": detail})

    rank = lambda v: PRIORITY_RANK.get(_priority(v), 1)  # noqa: E731

    # 输出顺序稳定（M-c）：修复环各轮之间、以及人类与主智能体看到的同一份缺陷清单，顺序必须
    # 一致——违例按 (桶, 节点 id) 的自然序 emit，不跟随宇宙插入顺序（草稿列举顺序可能变）。
    # 父引用完整：断链（parent 不在宇宙）与闭环（parent 互指成环）都算破损，共用 broken_parent；
    # 闭环按 R-11 不新增 code，环上每个参与节点报一条。
    cycle_members = _parent_cycle_members(chains)
    for cid in sorted(cycle_members):
        parent = str(chains[cid].get("parent") or "")
        add("broken_parent", CHAIN, cid, f"parent「{parent}」与祖先闭合成环，该分支在大纲中无法呈递")

    # I-1：环检测的呈报面不得被 R-13 静音掉「范围内受害方」。若一条**范围内**链路的祖先指针
    # 走入了闭合环（命中 cycle_members 的环体），该分支在大纲里既起不了根也作不了孤儿，会整枝
    # 静默丢失——即便环体本身全在范围外。报在**可修方**（草稿侧改挂合法父），与 R-13「只报在能修
    # 的一方」同源；环体自身若全在范围外仍由上面的循环 + add() 守卫保持不报。
    for cid in sorted(chains):
        if cid in cycle_members or not chains[cid].get("in_scope", True):
            continue
        seen: set[str] = {cid}
        cur = str(chains[cid].get("parent") or "")
        hit = ""
        while cur in chains and cur not in seen:
            if cur in cycle_members:
                hit = cur
                break
            seen.add(cur)
            cur = str(chains[cur].get("parent") or "")
        if hit:
            add("broken_parent", CHAIN, cid,
                f"祖先「{hit}」与更上层闭合成环，本分支在大纲中无法呈递")

    for cid in sorted(chains):
        c = chains[cid]
        parent = str(c.get("parent") or "")
        if parent:
            p = chains.get(parent)
            if p is None:
                add("broken_parent", CHAIN, cid, f"parent「{parent}」不在引用宇宙内")
            else:
                level, parent_level = _level(c.get("level")), _level(p.get("level"))
                if level is None or parent_level is None:
                    bad = c if level is None else p
                    add("cross_level", CHAIN, cid,
                        f"level「{bad.get('level')}」不是整数，无法校验与父 {parent} 的层级连续性")
                elif level != parent_level + 1:
                    add("cross_level", CHAIN, cid,
                        f"level={c.get('level')} 与父 {parent} level={p.get('level')} 不连续")
                # 优先级沿树（R-12）：违例 = 子节点优先级**高于**其父（rank 更小）。
                # 链路 priority 是业务分支的重要性、索引树向上聚合，P0 链路下挂 P1/P2
                # 属正常降级；反过来才说明某一层定级有误。三处比较方向一致：
                # 子链路↔父链路、故事↔所属链路、测试点↔所属故事。
                if rank(c.get("priority")) < rank(p.get("priority")):
                    add("priority_violation", CHAIN, cid,
                        f"优先级 {_priority(c.get('priority'))} 高于其父 {parent} 的"
                        f" {_priority(p.get('priority'))}")
                # I-2 引用边状态：子链引用了未过审的父链 ⇒ 报在可修方（子链）。「父自身违例」
                # 由下面父链的自状态检查负责，此处只表达「引用了失效上游」，两条 where 不同。
                if str(p.get("state")) not in _APPROVED:
                    add("unapproved_ref", CHAIN, cid,
                        f"父链「{parent}」状态 {p.get('state')} 未过审，不得被引用")
        # I-2 链路自状态：与 story/point 同款——失效链路此前零检查，却被下游引用。
        if str(c.get("state")) not in _APPROVED:
            add("unapproved_ref", CHAIN, cid, f"节点状态 {c.get('state')} 未过审")
    for sid in sorted(stories):
        s = stories[sid]
        parents = sorted(str(x) for x in (s.get("chains") or []))
        if not parents:
            add("broken_parent", STORY, sid, "story 无 chains 引用")
        for cid in parents:
            p = chains.get(cid)
            if p is None:
                add("broken_parent", STORY, sid, f"chains「{cid}」不在引用宇宙内")
            else:
                if rank(s.get("priority")) < rank(p.get("priority")):
                    add("priority_violation", STORY, sid,
                        f"优先级 {_priority(s.get('priority'))} 高于其父 {cid} 的"
                        f" {_priority(p.get('priority'))}")
                # I-2 引用边状态：故事引用了未过审的所属链路 ⇒ 报在可修方（故事）。
                if str(p.get("state")) not in _APPROVED:
                    add("unapproved_ref", STORY, sid,
                        f"所属链路「{cid}」状态 {p.get('state')} 未过审，不得被引用")
        if str(s.get("state")) not in _APPROVED:
            add("unapproved_ref", STORY, sid, f"节点状态 {s.get('state')} 未过审")
    for pid in sorted(points):
        p = points[pid]
        sid = str(p.get("story") or "")
        parent = stories.get(sid)
        if parent is None:
            add("broken_parent", POINT, pid, f"story「{sid}」不在引用宇宙内")
        else:
            if rank(p.get("priority")) < rank(parent.get("priority")):
                add("priority_violation", POINT, pid,
                    f"优先级 {_priority(p.get('priority'))} 高于其父 {sid} 的"
                    f" {_priority(parent.get('priority'))}")
            # I-2 引用边状态：点引用了未过审的所属故事 ⇒ 报在可修方（点）。
            if str(parent.get("state")) not in _APPROVED:
                add("unapproved_ref", POINT, pid,
                    f"所属故事「{sid}」状态 {parent.get('state')} 未过审，不得被引用")
        if str(p.get("state")) not in _APPROVED:
            add("unapproved_ref", POINT, pid, f"节点状态 {p.get('state')} 未过审")

    # 空链路按**子树**口径：链路自身或其任一子孙链路有故事认领即非空。
    # 简报的直接认领版与其 clean-tree 用例矛盾（P0 根链 ch-0001 只经子链 ch-0002 挂故事，
    # 须零违例）；按用例裁定。从有故事的链沿 parent 指针向上盖到根，重访即停，父成环亦终止。
    # 认领关系读全宇宙（范围外的故事也算认领）——被点名节点仍由 add() 的 R-13 守卫把关。
    story_chains = {str(cid) for s in stories.values() for cid in (s.get("chains") or [])}
    covered: set[str] = set()
    for start in story_chains:
        cur = start
        while cur in chains and cur not in covered:
            covered.add(cur)
            cur = str(chains[cur].get("parent") or "")
    for cid in sorted(chains):
        if chains[cid].get("in_scope") and cid not in covered:
            add("empty_chain", STORY, cid, "范围内链路没有任何故事认领（空链路）")
    point_stories = {str(p.get("story") or "") for p in points.values()}
    for sid in sorted(stories):
        if stories[sid].get("in_scope") and sid not in point_stories:
            add("empty_story", POINT, sid, "范围内故事没有任何测试点（空故事）")

    report = {
        "empty_seam": sum(1 for c in claims if c.get("verdict") == "unclaimed"),
        "matrix_unreasoned": sum(1 for c in matrix_cells
                                 if c.get("verdict") == "not_needed" and not str(c.get("reason") or "").strip()),
        "unresolved": sum(len(v) for v in unresolved.values()),
        # A-M1：spec 验收数字线「断言方向缺失数（④）」——范围内点 directions 空即计。归 report（呈递项），
        # 不得进 hard：方向非空此前只靠点层草稿 schema，KB 存量点 directions:[] 时门会零信号；进 hard 会改 T12 口径。
        "point_missing_directions": sum(1 for p in points.values()
                                        if p.get("in_scope") and not (p.get("directions") or [])),
    }
    return {"hard": hard, "report": report}
