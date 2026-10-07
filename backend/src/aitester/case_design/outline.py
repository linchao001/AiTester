"""增量大纲：人审门的唯一可视对象（spec §2）。全部确定性组装，不经 LLM。"""

from __future__ import annotations

from aitester.case_design.constants import CHAIN, LAYER_CN, LAYERS, POINT, STORY
from aitester.case_design.schema import is_draft_row

_LAYER_STATE_CN = {"done": "已定稿", "audited": "已过审", "active": "进行中", "pending": "未开始",
                   "stale_pending": "失效待重算（下次任务重跑）", "skipped": "本次不动"}


def _dedup_written(rows: list[dict]) -> list[dict]:
    """同 id 只留**将被写库的那一份**，位置仍取该 id 首次出现处（树形顺序不漂）。

    W3-3（走查三呈报、R-58 并入本片）：`_outline_nodes()` 的行序是「先 KB 存量、后本 run 草稿」，
    旧写法不去重 ⇒ 同一节点在唯一人审门里呈双行（实测 38 行 / 19 唯一 id）。
    取舍必须和写库侧**同一个口径**，否则人看的是后一块、库里进的是前一块：
    `_collect_writeback_items`（stages.py:2257-2289）只遍历草稿、且 `seen` 首见即留 ⇒
    ① 有草稿就不呈存量行（KB 存量永不进写库侧）；
    ② 草稿之间取**先出现**的那一条（按 sorted 文件名序，与写库侧同序）。
    无 id 的行不参与去重、原样留在列表里（`_tree_lines` 渲染本来就需要 id，本函数不新增兜底、
    也不改变它那侧的既有行为：呈递侧宁可多一行，不可静默少一行）。
    """
    out: list[dict] = []
    slot_of: dict[str, int] = {}          # id → 该 id 首次出现的下标（位置不漂）
    for row in rows:
        key = str(row.get("id") or "")
        if not key:
            out.append(row)               # 无 id 行：不参与去重、原位保留
            continue
        if key not in slot_of:
            slot_of[key] = len(out)
            out.append(row)               # 同 id 只出一行：占在该 id 的首次出现处
            continue
        slot = slot_of[key]
        if not is_draft_row(out[slot]):
            out[slot] = row               # 存量让位给草稿；草稿之间先出现者胜
    return out


def _tree_lines(nodes_by_layer: dict, layers: dict) -> list[str]:
    deleted: list[dict] = []
    seen_deleted: set[str] = set()
    for layer in LAYERS:                            # 同一节点被多个块草稿重复表达时只出一条（id 带层前缀）
        for n in nodes_by_layer.get(layer, []):
            if n.get("op") != "delete":
                continue
            nid = str(n.get("id") or "")
            if nid and nid in seen_deleted:
                continue
            seen_deleted.add(nid)
            deleted.append(n)
    chains = _dedup_written([n for n in nodes_by_layer.get(CHAIN, []) if n.get("op") != "delete"])
    stories = _dedup_written([n for n in nodes_by_layer.get(STORY, []) if n.get("op") != "delete"])
    points = _dedup_written([n for n in nodes_by_layer.get(POINT, []) if n.get("op") != "delete"])
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

    chain_ids = {str(c.get("id")) for c in chains}
    walked: set[str] = set()          # 父子引用成环（异常草稿）时同一链路只走一次，绝不挂死

    def walk_chain(node: dict, indent: int) -> None:
        cid = str(node["id"])
        if cid in walked:
            return
        walked.add(cid)
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
        for child in by_parent.get(cid, []):        # 子链路连同其下故事/测试点一起呈递
            walk_chain(child, indent + 1)

    # 驱动只把「本次涉及的草稿节点」交给大纲：窄子树任务、或父节点被删时，上游父节点不在输入里。
    # 只从 parent 为空的节点起树会让整条分支（含其下 upsert 的故事/测试点）静默消失，
    # 呈给人类的是一份假完整大纲。故：父为空、或父引用落空的链路都按根起树，并递归下钻。
    orphans = [c for c in chains if str(c.get("parent") or "")
               and str(c.get("parent") or "") not in chain_ids]
    for root in by_parent.get("", []) + orphans:
        walk_chain(root, 0)
    # I-1 终兜底：起完根与孤儿后，凡 `chains` 里仍未被 walked 覆盖的链路（其祖先不可达且父引用
    # 落在别处，如一段范围外的闭合父环），一律按根补走一遍——「祖先不可达且非孤儿」的分支因此
    # 不可能静默丢失。此兜底只在原本会丢分支时触发：正常根树里所有链路都已 walked，故零影响。
    for c in chains:
        if str(c["id"]) not in walked:
            walk_chain(c, 0)
    for layer in LAYERS:
        st = layers.get(layer, {}).get("state", "")
        if st in ("stale_pending", "skipped"):
            lines.append(f"- [{LAYER_CN[layer]}层] {_LAYER_STATE_CN.get(st, st)}")
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
        f"- 块序：链路 {plan.get('blocks', {}).get(CHAIN)}；故事 {plan.get('blocks', {}).get(STORY)}；"
        f"测试点 {plan.get('blocks', {}).get(POINT)}",
    ]
    replans = task.get("replans") or []
    if replans:
        lines.append("- 重规划事件：")
        for ev in replans:
            lines.append(f"  - [{ev.get('at', '')}] {ev.get('trigger', '')}")
    else:
        lines.append("- 重规划事件：无")
    lines += ["", "## 增量树", *_tree_lines(nodes_by_layer, layers), "", "## 结构指标（④）"]
    hard = report.get("hard") or []
    lines.append("- hard：" + ("全部为 0" if not hard else f"{len(hard)} 项未清零"))
    exempted = report.get("exempted") or []
    if exempted:                                   # R-18(a)：豁免必须可见，hard 全 0 不等于「没有可豁免的」
        lines.append("- hard 豁免（下游层未进本 run 窗口）："
                     f"empty_chain {sum(1 for f in exempted if f.get('code') == 'empty_chain')}"
                     "／"
                     f"empty_story {sum(1 for f in exempted if f.get('code') == 'empty_story')}")
    for f in hard:
        lines.append(f"  - [{f.get('code')}] {f.get('layer')}/{f.get('where')}：{f.get('detail')}")
    rep = report.get("report") or {}
    lines.append(f"- report：剩余空归属 {rep.get('empty_seam', 0)}（呈递项，不卡关）；"
                 f"矩阵无理由空格 {rep.get('matrix_unreasoned', 0)}；"
                 f"未消化项 {rep.get('unresolved', 0)}；断言方向缺失 {rep.get('point_missing_directions', 0)}")
    lines += ["", "## 未消化项（含不收敛归因）"]
    unresolved = extras.get("unresolved") or []
    if not unresolved:
        lines.append("- 无")
    for u in unresolved:
        line = (f"- [{u.get('layer', '')}/{u.get('ref', '')}] {u.get('ask', '')}"
                f"（归因：{u.get('cause', '')}——{u.get('note', '')}）")
        # F-2（R-51）：回扫补认后的旧归因项不销账，只在行尾追加交叉标注（在「（归因：…）」
        # 之后、不替换它）——两处口径相反时给人一条能对上的线索。
        if u.get("rescan_note"):
            line += f"（{u['rescan_note']}）"
        lines.append(line)
    lines += ["", "## 重复标注清单"]
    duplicates = extras.get("duplicates") or []
    if duplicates:
        for group in duplicates:
            lines.append(f"- {' / '.join(group)}（请人工决定是否合并）")
    else:
        lines.append("- 无")
    lines += ["", "## 接缝归属表（②摘要）"]
    claims = extras.get("claims") or []
    if claims:
        for c in claims:
            tail = {"deterministic": "（回扫补认·确定性）",
                    "reviewer": "（回扫补认·复核）"}.get(str(c.get("rescanned") or ""), "")
            lines.append(f"- {c.get('claimant', '')} 声称「{c.get('claim', '')}」→ "
                         f"{'空归属（未消化）' if c.get('verdict') == 'unclaimed' else '已核对'}"
                         f"（owner={c.get('owner', '')}）{tail}")
        # F-1（R-50）：计数与紧邻其上的逐行尾注同源——从本张 claims 表现算，不读账本
        # 旧计数（故事层重建后 claims_rescan 不重建，读它会出「0 行带标记却报 1 条」）；
        # d/r 都为 0 时整行不出，免得「0 条」与「从未回扫」两种意思挤在同一行。
        rescan = extras.get("claims_rescan") or {}
        d = sum(1 for c in claims if c.get("rescanned") == "deterministic")
        r = sum(1 for c in claims if c.get("rescanned") == "reviewer")
        if d or r:
            lines.append(f"- 回扫补认：确定性 {d} 条／复核 {r} 条（{rescan.get('at', '')}）")
    else:
        lines.append("- 无")
    lines += ["", "## 矩阵复核（③「不需要」的业务理由）"]
    notes = extras.get("matrix_notes") or []
    if notes:
        for n in notes:
            lines.append(f"- （{n.get('entity', '')}, {n.get('story', '')}）不需要：{n.get('reason', '')}")
    else:
        lines.append("- 无")
    lines += ["", "## 树外遗漏对照（①盲枚举 × 落点）"]
    enumeration = extras.get("enumeration") or []
    if enumeration:
        for item in enumeration:
            landing = str(item.get("landing") or "").strip()
            tail = f"落点 {landing}" if landing else f"树外遗漏（{item.get('note') or '无落点'}）"
            lines.append(f"- [{item.get('kind', '')}] {item.get('name', '')}：{tail}")
    else:
        lines.append("- 无")
    lines += ["", "## 意见落点对照表（每条意见的去向）"]
    dispositions = extras.get("dispositions") or []
    if dispositions:
        for d in dispositions:
            note = d.get("note") or d.get("disposition") or ""
            lines.append(f"- [{d.get('layer', '')}] {d.get('ref', '')}"
                         f"（{d.get('source', '')}·{d.get('kind', '')}·{d.get('target', '')}）："
                         f"{d.get('ask', '')} → {d.get('status', '')}"
                         + (f"——{note}" if note else ""))
    else:
        lines.append("- 无")
    lines += ["", "## 本次无变化块"]
    no_change = extras.get("no_change") or []
    if no_change:
        for item in no_change:
            layer_cn = LAYER_CN.get(item.get("layer", ""), item.get("layer", ""))
            reason = str(item.get("reason") or "").strip()
            lines.append(f"- [{layer_cn}] 块 {item.get('block', '')}"
                         "（本块判定无变化，未下发节点）"
                         + (f"：{reason}" if reason else ""))
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"
