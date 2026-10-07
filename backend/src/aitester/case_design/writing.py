"""第四层用例编写环的确定性面：分批、分母、履约核对、交付物渲染。

全部不经 LLM（④ 的第四层同型物）：漏测判据必须机器可核，才配当 hard（裁定 37）。
覆盖度没有客观分母这条更早的裁定不被本片推翻——这里 hard 判的是「已知点有没有落实」，
不是「业务穷尽性」。
"""

from __future__ import annotations

from typing import Any

from aitester.case_design.constants import (
    CASE_BATCH_CAP, ENV_PRECONDITION_MARKS, VAGUE_ASSERTION_MARKS,
)
from aitester.case_design.schema import CaseDraft

# 本环唯一两个 hard code（新增必须同步改 spec 验收节，与三层 R-32 同口径）
_HARD_CODES = ("uncovered_point", "phantom_cover")


def _stories_of_chain(chain: str, story_rows: list[dict]) -> set[str]:
    return {str(s.get("id") or "") for s in story_rows
            if chain in {str(c) for c in (s.get("chains") or [])}}


def _in_scope(value: str, scope_set: set[str] | None) -> bool:
    """空 scope（缺省/未给）= 全量任务，一切都在范围内（与 build_universe 的 fail-closed 同源）。"""
    if scope_set is None:
        return True
    return value in scope_set


def denominator_points(point_rows: list[dict], scope: dict[str, set[str]], chain: str,
                       story_rows: list[dict] | None = None) -> list[dict]:
    """某链路的履约分母 = 所属故事挂在它上面的全部测试点（范围空 = 全量）。

    点→故事→链路的关系一律从节点字段确定性反查，不问模型（消费对称性：编写环吃的是 KB/账本
    制品，不是对话记忆）。
    """
    owned = _stories_of_chain(chain, story_rows or [])
    scope_stories = scope.get("stories") if scope else None
    latest: dict[str, dict] = {}
    for row in point_rows:
        sid = str(row.get("story") or "")
        if story_rows is None:
            # story_rows 缺席（调用方只给了点）时退化为按点所属故事判定范围；scope 也没给 stories
            # 就无从反查链路归属——不许把全量点白送进任意一条链路的分母，fail-closed 归空
            if scope_stories is None or sid not in scope_stories:
                continue
        else:
            # 有 story 表就必须挂在链路上：点所属故事不在本链路名下即不算本链分母
            if sid not in owned:
                continue
            if not _in_scope(sid, scope_stories):
                continue
        if row.get("op") == "delete":
            continue
        if row.get("id"):
            # 后出现者胜：编写环吃 `_rows_of(POINT)`（KB 存量在前、本 run 草稿在后），
            # mixed 任务里同一 pt- id 会有两行，取旧行 = 拿回写前的内容当分母。
            # 去重还挡住「同一点被切进两个批次」——那是假漏测（分母里重复行）的直接来源。
            latest[str(row["id"])] = dict(row)
    return sorted(latest.values(), key=lambda r: str(r["id"]))


def plan_case_targets(chain_rows: list[dict], story_rows: list[dict], point_rows: list[dict],
                      scope: dict[str, set[str]]) -> list[dict]:
    """编写环计划：链路升序 → 每条链路的分母点 → 按 `CASE_BATCH_CAP` 切批。"""
    chains = sorted({str(r.get("id") or "") for r in chain_rows if r.get("id")})
    scope_chains = scope.get("chains") if scope else None
    targets: list[dict] = []
    for cid in chains:
        if not _in_scope(cid, scope_chains):
            continue
        points = [str(p["id"]) for p in denominator_points(point_rows, scope, cid, story_rows)]
        # 批次从 b1 起（账本/交付物/人眼都按 1 起数的批名对齐）
        batches = [{"id": f"{cid}-b{n}", "points": points[j:j + CASE_BATCH_CAP]}
                   for n, j in enumerate(range(0, len(points), CASE_BATCH_CAP), start=1)]
        # 0 点的链路也要在场：末门要让人看到「这条链路没有点可落实」，而不是静默少一条链路
        targets.append({"chain": cid, "batches": batches})
    return targets


def collect_covers(cases: list[CaseDraft]) -> dict[str, list[str]]:
    """点 id → 认领它的用例 id 列表（多点合一条 = 该条同时认领多个点）。"""
    owners: dict[str, list[str]] = {}
    for case in cases:
        for pid in case.covers:
            key = str(pid)
            if case.case_id not in owners.setdefault(key, []):
                owners[key].append(case.case_id)
    return owners


def _norm(text: Any) -> str:
    return "".join(str(text or "").split())


def run_case_checks(points: list[dict], cases: list[CaseDraft]) -> dict:
    """履约（hard）+ 规范（report 呈递）。

    hard 只有两条，都能被机器证明：`uncovered_point`（分母里的点没人认领 = 漏测）、
    `phantom_cover`（用例认领了分母外的点 = 假完整）。必填缺失已在 schema 层拒收，
    这里不再为它增设 code（同一条坏输入不该有两个处置出口）。
    """
    pid_set = {str(p.get("id") or "") for p in points if p.get("id")}
    owners = collect_covers(cases)
    hard: list[dict] = []

    for pid in sorted(pid_set):
        if not owners.get(pid):
            name = next((str(p.get("name") or "") for p in points if str(p.get("id")) == pid), "")
            hard.append({"code": "uncovered_point", "where": pid,
                         "detail": f"测试点「{pid} {name}」没有任何用例认领（covers），属漏测"})
    for case in cases:
        phantom = [str(c) for c in case.covers if str(c) not in pid_set]
        if phantom:
            hard.append({"code": "phantom_cover", "where": case.case_id,
                         "detail": f"用例认领了不在本链路分母内的点 {phantom}，"
                                   "要么点 id 写错、要么用例越界"})

    notes: list[dict] = []
    vague = env_pre = 0
    for case in cases:
        for line in case.expected:
            hits = [w for w in VAGUE_ASSERTION_MARKS if _norm(w) in _norm(line)]
            if hits:
                vague += 1
                notes.append({"where": case.case_id, "kind": "含糊断言",
                              "detail": f"「{line}」含 {hits}，不可机械判定通过与否（含糊断言）"})
        pre_hits = [w for w in ENV_PRECONDITION_MARKS if _norm(w) in _norm(case.preconditions)]
        if pre_hits:
            env_pre += 1
            notes.append({"where": case.case_id, "kind": "环境存量前置",
                          "detail": f"前置「{case.preconditions}」依赖环境既有数据 {pre_hits}，"
                                    "建议改为可造数的前置（环境存量前置）"})

    fulfillment = [{"point": pid,
                    "name": next((str(p.get("name") or "") for p in points
                                  if str(p.get("id")) == pid), ""),
                    "owners": sorted(owners.get(pid, []))} for pid in sorted(pid_set)]
    return {"hard": hard,
            "report": {"cases_total": len(cases), "points_total": len(pid_set),
                       "vague_assertions": vague, "env_preconditions": env_pre,
                       "notes": notes},
            "fulfillment": fulfillment}


_SECTION_ORDER = ("前置判定", "用例清单", "履约差异表", "规范校验表（呈递项）",
                  "未消化项（含不收敛归因）", "失效待重算批次",
                  "意见落点对照表（每条意见的去向）")


def compose_case_delivery(ledger_data: dict, targets: list[dict],
                          checks_by_chain: dict[str, dict], extras: dict) -> str:
    """末门唯一可视对象（裁定 36）：实测计数 + 逐点去向 + 呈递线索，全部确定性组装。"""
    writing = ledger_data.get("writing") or {}
    lines = ["# 用例交付清单（本次任务）", "",
             "> 由第四层用例编写环生成；本文件是末门的唯一可视对象。"
             "用例正文只落项目空间 design/cases/，**不写知识库**。", "",
             "## 前置判定"]
    for t in targets:
        batches = t.get("batches") or []
        n_points = sum(len(b.get("points") or []) for b in batches)
        lines.append(f"- 链路 {t.get('chain', '')}：分母测试点 {n_points}，批次 "
                     f"{[b.get('id') for b in batches]}")
    if writing.get("note"):
        lines.append(f"- 设计侧留痕：{writing['note']}")
    lines += ["", "## 用例清单"]
    for t in targets:
        for b in t.get("batches") or []:
            cases = (extras.get("cases_by_chain") or {}).get(t["chain"], [])
            mine = [c for c in cases if c.case_id]
            lines.append(f"- 批次 {b.get('id', '')}（应落实点 {len(b.get('points') or [])} 个）："
                         f"本链路累计用例 {len(mine)} 条，文件 design/cases/{b.get('id', '')}.json")
    lines += ["", "## 履约差异表（逐点去向，实测）"]
    uncovered_ids = {str(u) for u in (extras.get("uncovered") or [])}
    for chain, checks in sorted(checks_by_chain.items()):
        for row in checks.get("fulfillment") or []:
            pid = str(row.get("point", ""))
            owners = row.get("owners") or []
            if pid in uncovered_ids:
                lines.append(f"- [{chain}] {pid} {row.get('name', '')}：**未落实（无用例认领）**")
            else:
                lines.append(f"- [{chain}] {pid} {row.get('name', '')}：落实于 {','.join(owners)}")
    # 计数走全局去重表（uncovered_ids），不逐链路累加：一点挂两链时逐链累加会把同一个漏测报成两个。
    lines.append(f"- 实测：未落实点 {len(uncovered_ids)} 个（hard 判据要求为 0，非 0 不得进门）")
    lines += ["", "## 规范校验表（呈递项）"]
    notes = extras.get("notes") or []
    for n in notes:
        lines.append(f"- {n.get('where', '')}（{n.get('kind', '')}）：{n.get('detail', '')}")
    if not notes:
        lines.append("- 无")
    lines += ["", "## 未消化项（含不收敛归因）"]
    for u in extras.get("unresolved") or []:
        lines.append(f"- [{u.get('chain', '')}/{u.get('ref', '')}] {u.get('ask', '')}"
                     f"（归因：{u.get('cause', '')}——{u.get('note', '')}）")
    if not extras.get("unresolved"):
        lines.append("- 无")
    lines += ["", "## 失效待重算批次"]
    for bid in writing.get("stale_batches") or []:
        lines.append(f"- {bid}（意见回溯所致；批准交付前请重做，或明确保留旧稿）")
    if not writing.get("stale_batches"):
        lines.append("- 无")
    lines += ["", "## 意见落点对照表（每条意见的去向）"]
    for d in extras.get("dispositions") or []:
        note = d.get("note") or ""
        lines.append(f"- [{d.get('chain', '')}] {d.get('ref', '')}"
                     f"（{d.get('kind', '')}·用例 {d.get('case', '')}）："
                     f"{d.get('ask', '')} → {d.get('status', '')}"
                     + (f"——{note}" if note else ""))
    if not extras.get("dispositions"):
        lines.append("- 无")
    return "\n".join(lines) + "\n"
