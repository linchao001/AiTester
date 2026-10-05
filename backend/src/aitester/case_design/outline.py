"""增量大纲：人审门的唯一可视对象（spec §2）。全部确定性组装，不经 LLM。"""

from __future__ import annotations

from aitester.case_design.constants import LAYER_CN, LAYERS

_LAYER_STATE_CN = {"done": "已定稿", "audited": "已过审", "active": "进行中", "pending": "未开始",
                   "stale_pending": "失效待重算（下次任务重跑）", "skipped": "本次不动"}


def _tree_lines(nodes_by_layer: dict, layers: dict) -> list[str]:
    chains = [n for n in nodes_by_layer.get("chain", []) if n.get("op") != "delete"]
    stories = nodes_by_layer.get("story", [])
    points = nodes_by_layer.get("point", [])
    lines: list[str] = []
    by_parent: dict[str, list[dict]] = {}
    for c in chains:
        by_parent.setdefault(str(c.get("parent") or ""), []).append(c)
    stories_of: dict[str, list[dict]] = {}
    for s in stories:
        for cid in s.get("chains") or []:
            stories_of.setdefault(str(cid), []).append(s)
    points_of: dict[str, list[dict]] = {}
    for p in points:
        points_of.setdefault(str(p.get("story") or ""), []).append(p)

    def walk_chain(node: dict, indent: int) -> None:
        mark = node.get("state", "")
        lines.append(f"{'  ' * indent}- {node['id']} {node.get('name', '')}"
                     f"（{mark}，{node.get('priority', 'P1')}）")
        for s in stories_of.get(str(node["id"]), []):
            lines.append(f"{'  ' * (indent + 1)}- {s['id']} {s.get('name', '')}"
                         f"（{s.get('state', '')}，chains: {','.join(s.get('chains') or [])}）")
            for p in points_of.get(str(s["id"]), []):
                lines.append(f"{'  ' * (indent + 2)}- {p['id']} {p.get('name', '')}"
                             f"（方向: {'/'.join(p.get('directions') or [])}；"
                             f"实体: {','.join(p.get('entities') or [])}）")
    for root in by_parent.get("", []):
        walk_chain(root, 0)
    for layer in LAYERS:
        st = layers.get(layer, {}).get("state", "")
        if st in ("stale_pending", "skipped"):
            lines.append(f"- [{LAYER_CN[layer]}层] {_LAYER_STATE_CN.get(st, st)}")
    deleted = [n for n in (chains + stories + points) if n.get("op") == "delete"]
    for d in deleted:
        lines.append(f"- {d['id']} {d.get('name', '')}（删除：{d.get('reason', '')}）")
    return lines or ["- （本次无增量节点）"]


def compose_outline(ledger_data: dict, nodes_by_layer: dict, report: dict, extras: dict) -> str:
    task = ledger_data.get("task") or {}
    desc = task.get("descriptor") or {}
    probe = task.get("probe") or {}
    plan = task.get("plan") or {}
    layers = ledger_data.get("layers") or {}
    lines: list[str] = [
        "# 增量测试大纲（本次任务）",
        "",
        "> 由用例设计专属 loop 生成；本文件是人审门的唯一可视对象，回写知识库前必须人工确认。",
        "",
        "## 本次计划",
        f"- 任务种类：{desc.get('task_kind', '')}；入口层 {desc.get('entry_layer', '')} → 终止层 {desc.get('terminal_layer', '')}",
        f"- 目标子树：{desc.get('target_subtree') or '全量'}",
        "- 探测：" + "；".join(f"{LAYER_CN[l]} {probe.get(l, {}).get('evidence', '未探测')}" for l in LAYERS),
        "- 层判定：" + "；".join(f"{LAYER_CN[l]}={layers.get(l, {}).get('mode', '')}" for l in LAYERS),
        f"- 块序：链路 {plan.get('blocks', {}).get('chain')}；故事 {plan.get('blocks', {}).get('story')}；"
        f"测试点 {plan.get('blocks', {}).get('point')}",
    ]
    replans = task.get("replans") or []
    lines.append("- 重规划事件：" + ("无" if not replans else ""))
    for ev in replans:
        lines.append(f"  - [{ev.get('at', '')}] {ev.get('trigger', '')}")
    lines += ["", "## 增量树", *_tree_lines(nodes_by_layer, layers), "", "## 结构指标（④）"]
    hard = report.get("hard") or []
    lines.append("- hard：" + ("全部为 0" if not hard else f"{len(hard)} 项未清零"))
    for f in hard:
        lines.append(f"  - [{f.get('code')}] {f.get('layer')}/{f.get('where')}：{f.get('detail')}")
    rep = report.get("report") or {}
    lines.append(f"- report：剩余空归属 {rep.get('empty_seam', 0)}；矩阵无理由空格 {rep.get('matrix_unreasoned', 0)}；"
                 f"未消化项 {rep.get('unresolved', 0)}")
    lines += ["", "## 未消化项（含不收敛归因）"]
    unresolved = extras.get("unresolved") or []
    if not unresolved:
        lines.append("- 无")
    for u in unresolved:
        lines.append(f"- [{u.get('layer', '')}/{u.get('ref', '')}] {u.get('ask', '')}"
                     f"（归因：{u.get('cause', '')}——{u.get('note', '')}）")
    lines += ["", "## 重复标注清单"]
    duplicates = extras.get("duplicates") or []
    lines.append("- 无" if not duplicates else "")
    for group in duplicates:
        lines.append(f"- {' / '.join(group)}（请人工决定是否合并）")
    lines += ["", "## 接缝归属表（②摘要）"]
    claims = extras.get("claims") or []
    lines.append("- 无" if not claims else "")
    for c in claims:
        lines.append(f"- {c.get('claimant', '')} 声称「{c.get('claim', '')}」→ "
                     f"{'空归属（未消化）' if c.get('verdict') == 'unclaimed' else '已核对'}"
                     f"（owner={c.get('owner', '')}）")
    lines += ["", "## 矩阵复核（③「不需要」的业务理由）"]
    notes = extras.get("matrix_notes") or []
    lines.append("- 无" if not notes else "")
    for n in notes:
        lines.append(f"- （{n.get('entity', '')}, {n.get('story', '')}）不需要：{n.get('reason', '')}")
    return "\n".join(lines) + "\n"
