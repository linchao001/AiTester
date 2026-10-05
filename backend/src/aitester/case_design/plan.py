"""计划制品（裁定 17）：确定性组装为主。描述符由主智能体写 plan.json、驱动校验；
探测/层判定/块物化全部由本模块确定性产出。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from aitester.case_design.constants import CHAIN, LAYERS, POINT, ROUND_CAP, STORY

_TASK_KINDS = ("design", "mixed", "case_only")
_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:|/)")   # 盘符开头（Windows）或 / 开头（POSIX/UNC）


def _is_project_relative(rel: str) -> bool:
    """项目相对路径：不得为绝对路径，也不得含 ``..`` 段（否则 root/rel 逃出 project_dir）。"""
    if _ABSOLUTE_RE.match(rel):
        return False
    return ".." not in rel.split("/")


def validate_plan(raw: Any, project_dir: str) -> tuple[dict, list[str]]:
    """校验 design/plan.json（主智能体产出）。错误表非空即重试（NUDGE_CAP），超限 halted。"""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["plan.json 根必须是对象"]
    kind = str(raw.get("task_kind") or "")
    if kind not in _TASK_KINDS:
        errors.append(f"task_kind「{kind}」非法（design/mixed/case_only）")
    entry = str(raw.get("entry_layer") or "")
    terminal = str(raw.get("terminal_layer") or "")
    if entry not in LAYERS:
        errors.append(f"入口层「{entry}」非法")
    if terminal not in LAYERS:
        errors.append(f"终止层「{terminal}」非法")
    if entry in LAYERS and terminal in LAYERS and LAYERS.index(terminal) < LAYERS.index(entry):
        errors.append("终止层不得高于入口层")
    subtree = str(raw.get("target_subtree") or "").strip()
    root = Path(project_dir)
    files = raw.get("source_files")
    files = files if isinstance(files, list) else []
    clean_files: list[str] = []
    for item in files:
        rel = str(item or "").strip().replace("\\", "/")
        if not rel:
            continue
        if rel.startswith("design/"):
            errors.append(f"来源文件不得列 design/ 工作稿：{rel}")
            continue
        if not _is_project_relative(rel):
            errors.append(f"来源文件必须是项目相对路径（不得为绝对路径或含 .. 段）：{rel}")
            continue
        if not (root / rel).is_file():
            errors.append(f"来源文件不存在：{rel}")
            continue
        clean_files.append(rel)
    descriptor = {
        "task_kind": kind, "entry_layer": entry, "terminal_layer": terminal,
        "target_subtree": subtree, "source_files": clean_files,
        "note": str(raw.get("note") or ""),
    }
    return (descriptor if not errors else {}), errors


def _has_link(layer: str, row: dict) -> bool:
    if layer == CHAIN:
        return "parent" in row                     # 顶层 parent 可以是空串，但键必须在
    if layer == STORY:
        return bool(row.get("chains"))
    return bool(row.get("story"))


def summarize_probe(layer: str, rows: list[dict]) -> dict:
    """P-3：缺 id / type / 父引用任一件即按「该层未维护」处理（不做猜测性对齐）。"""
    if not rows:
        return {"maintained": False, "count": 0, "evidence": "桶内无节点"}
    bad = [r for r in rows if not (r.get("id") and r.get("type") == layer and _has_link(layer, r))]
    if bad:
        return {"maintained": False, "count": len(rows),
                "evidence": f"{len(bad)} 个节点缺 id/type/父引用（P-3 视为未维护）"}
    return {"maintained": True, "count": len(rows), "evidence": f"{len(rows)} 个节点带完整标记"}


def plan_layers(descriptor: dict, probe: dict, stale_layers: set[str]) -> dict[str, str]:
    """三层各自独立判定（裁定 6）；stale 层强制 update 全块重跑（A8）。"""
    entry = descriptor.get("entry_layer") or CHAIN
    terminal = descriptor.get("terminal_layer") or POINT
    i0, i1 = LAYERS.index(entry), LAYERS.index(terminal)
    modes: dict[str, str] = {}
    for i, layer in enumerate(LAYERS):
        if i < i0 or i > i1:
            modes[layer] = "skipped"
        elif layer in stale_layers:
            modes[layer] = "update"
        else:
            modes[layer] = "update" if probe.get(layer, {}).get("maintained") else "first_build"
    return modes


def _subtree_ids(root_id: str, chain_rows: list[dict]) -> set[str]:
    """向下闭包：目标子树 = 节点自身 + 全部后代（按 parent 引用）。"""
    children: dict[str, list[str]] = {}
    for row in chain_rows:
        children.setdefault(str(row.get("parent") or ""), []).append(str(row.get("id")))
    out: set[str] = set()
    stack = [root_id]
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(children.get(cur, []))
    return out


def in_scope_targets(descriptor: dict, chain_rows: list[dict], story_rows: list[dict]) -> dict[str, set[str]]:
    """范围 = 目标子树（target_subtree 为空 = 全集）；故事按 chains 与链路范围相交。

    子树先与真实链路 id 求交：target_subtree 指向不存在的节点时得到空范围，
    驱动据此走「范围里没有任何块」的显式路径，而不是为幻影链路物化块。
    """
    all_chains = {str(r["id"]) for r in chain_rows}
    target = str(descriptor.get("target_subtree") or "")
    chains = (_subtree_ids(target, chain_rows) & all_chains) if target else set(all_chains)
    stories = {str(r["id"]) for r in story_rows
               if chains & {str(c) for c in (r.get("chains") or [])}}
    return {"chains": chains, "stories": stories}


def materialize_blocks(layer: str, scope: dict[str, set[str]]) -> list[str]:
    """块 = 一次生成的最小单位：链路=整树；故事=一条链路；测试点=一个故事。"""
    if layer == CHAIN:
        return ["ALL"]
    if layer == STORY:
        return sorted(scope["chains"])
    return sorted(scope["stories"])


def init_task(ledger_data: dict, descriptor: dict, probe: dict, modes: dict[str, str]) -> None:
    """账本首节 = 计划（裁定 17）：描述符 + 探测 + 层判定 + 块清单 + 预算。"""
    blocks = {
        CHAIN: materialize_blocks(CHAIN, {"chains": set(), "stories": set()}) if modes[CHAIN] != "skipped" else [],
        STORY: [],      # 逐层进入时物化（依赖父层定稿节点），物化结果即写入本表
        POINT: [],
    }
    ledger_data["task"] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "descriptor": descriptor,
        "probe": probe,
        "plan": {"blocks": blocks,
                 "block_rule": {"chain": "ALL", "story": "per chain", "point": "per story"},
                 "budget": {"round_cap": ROUND_CAP}},
        "replans": [],
    }
    for layer in LAYERS:
        state = ledger_data["layers"][layer]
        state["mode"] = modes[layer]
        state["state"] = "skipped" if modes[layer] == "skipped" else "pending"
        state["blocks"] = ([{"id": bid, "state": "todo", "round": 0}
                            for bid in blocks[layer]] if modes[layer] != "skipped" else [])
    entry = descriptor["entry_layer"]
    ledger_data["cursor"] = {"stage": "gen", "layer": entry, "block": "", "round": 0,
                             "source": "block", "nudge": 0}
    if modes[entry] == "first_build" and entry == CHAIN:
        ledger_data["cursor"]["block"] = "ALL"
