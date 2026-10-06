# backend/src/aitester/case_design/stages.py
"""阶段处理器 + 驱动状态机（专属 loop 的大脑；T9 的 driver 节点只是它的薄壳）。

一次 `drive_turn` = 一次「驱动激活」：把游标能走完的内部阶段全走完（评审子同步驱动、④ 检查、
大纲组装、回写），只在需要主智能体产制品（plan/gen/opt/attribute/gate 修复）时下发一条指令
HumanMessage 并把 route 置 agent；终局（人审门/回写完成/中止）发 delta+turn 终帧并把 route
置 end。`env=None` 为直通（free/echo/单测）：零副作用，只判 route。

fresh 检测（每个用户回合 = 新 thread，`services/chat.py:258`；case 不跨 run 持久）：只有
「最后一条是 HumanMessage 且其 id 与 case.instr_id 不同」才算新 run——主智能体回合结束后
重入驾驶时最后一条是它的 AIMessage；指令派出后重入时最后一条是我们自己发的指令（id 相同）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp

from aitester.case_design.checks import build_universe, run_checks
from aitester.case_design.constants import (
    CASE_REVIEW_AGENT_ID, CASE_REVIEW_BLIND_AGENT_ID, CHAIN, FIX_CAP, LAYERS, LAYER_CN,
    MAX_TRANSITIONS, NUDGE_CAP, OUTLINE_NAME, PLAN_NAME, POINT, ROUND_CAP, STORY,
    TYPE_PREFIX, WRITEBACK_FIX_CAP,
)
from aitester.case_design.instructions import (
    attribute_instruction, gate_fix_instruction, gen_instruction, opt_instruction,
    plan_instruction,
)
from aitester.case_design.kb import KbClient, KbClientError
from aitester.case_design.ledger import Ledger
from aitester.case_design.outline import compose_outline
from aitester.case_design.plan import (
    in_scope_targets, init_task, materialize_blocks, plan_layers, summarize_probe,
    validate_plan,
)
from aitester.case_design.reviewers import run_reviewer
from aitester.case_design.schema import (
    ClaimsOut, CompareOut, EnumeratorOut, MatrixOut, Opinion, OpinionTarget, ReviewOut,
    parse_draft_file,
)

logger = logging.getLogger(__name__)

_ARCHIVE_ITEMS = (PLAN_NAME, OUTLINE_NAME, "drafts", "reviews", "attribution", "manifests")


class _Halt(RuntimeError):
    """驱动内的确定性中止（重试超限/轮次用尽/计划空窗）：收敛为 halted + 终帧。"""


@dataclass
class Ctx:
    """一次驱动激活的全部显式依赖（无全局态；env=None 时不构造）。"""

    env: Any
    task_tool: Any
    writer: Any
    config: Any
    state_messages: list[BaseMessage]
    led: Ledger | None = None
    case: dict[str, Any] = field(default_factory=dict)
    ticks: int = 1
    transitions: int = 0
    _kb_cache: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def cur(self) -> dict[str, Any]:
        assert self.led is not None
        return self.led.cursor

    def rel(self, path: Path) -> str:
        p = Path(path)
        try:
            return p.relative_to(self.env.project_dir).as_posix()
        except ValueError:
            return p.as_posix()

    def kb_rows(self, layer: str) -> list[dict]:
        if layer not in self._kb_cache:
            self._kb_cache[layer] = KbClient(self.env.kb).list_layer(layer)
        return self._kb_cache[layer]

    def kb_rows_closure(self, layer: str) -> list[dict]:
        """R-10 影响闭包（单点口径）：大纲与 ④ 结构检查吃同一份快照。"""
        key = f"closure:{layer}"
        if key not in self._kb_cache:
            self._kb_cache[key] = _kb_closure(self)[layer]
        return self._kb_cache[key]

    def round_cap(self) -> int:
        """预算单点：块环/审环/修复环上限一律读账本里的 plan["budget"]["round_cap"]。"""
        plan = (self.led.data.get("task") or {}).get("plan") or {}
        return int((plan.get("budget") or {}).get("round_cap") or ROUND_CAP)

    def instr(self, text: str) -> HumanMessage:
        return HumanMessage(content=text, id=f"cdinstr-{self.ticks}")

    def ask(self, text: str, cap: int = NUDGE_CAP) -> HumanMessage:
        """带重试上限的下发：首问只置 asked，重问加 nudge；用尽即中止（plan/gen 3、opt/attr 2）。"""
        cur = self.cur
        if cur.get("asked"):
            if int(cur.get("nudge") or 0) >= cap:
                raise _Halt(f"{cur['stage']}/{cur['layer'] or '-'}/{cur['block'] or '-'} 重试超限")
            cur["nudge"] = int(cur.get("nudge") or 0) + 1
        cur["asked"] = True
        return self.instr(text)

    def turn(self, messages: list[BaseMessage], route: str) -> dict:
        if messages and getattr(messages[-1], "id", None):
            self.case["instr_id"] = str(messages[-1].id)
        return {"messages": messages,
                "case": {"route": route, "boot": True, "ticks": self.ticks,
                         "instr_id": self.case.get("instr_id")}}

    def end(self, text: str) -> dict:
        """终局帧沿用 react 口径：delta 直发 + turn 无 tool_calls（stream_graph 折成 reply）。"""
        round_no = 1 + sum(1 for m in self.state_messages if isinstance(m, ToolMessage))
        self.writer({"type": "delta", "round": round_no, "text": text})
        self.writer({"type": "turn", "round": round_no, "text": text,
                     "stopped": False, "tool_calls": []})
        if self.led is not None:
            self.led.save()
        return self.turn([], "end")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _is_no_change(raw: Any) -> bool:
    """no_change 块是合法终态（空 nodes + note 标记）：层环优化与回写都当零节点放行。

    不认它 = 把「智能体判定无变化」误判成坏草稿：混合层优化重问 FIX_CAP 次后中止、
    回写整轮失败——而主智能体根本无从"修"一个无变化块。
    """
    return (isinstance(raw, dict) and not raw.get("nodes")
            and str(raw.get("note") or "") == "no_change")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _archive(env: Any) -> None:
    """上一任务工作面归档到 design/archive/{时间戳}/（新任务开账前调用）。

    时间戳带微秒：同一秒内连续两次开新账（done→新任务相邻发生）时 shutil.move
    撞已存在目标会抛 shutil.Error，把整个驱动掀成 halted——用唯一目录消掉这个缝。
    """
    dest = env.design / "archive" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest.mkdir(parents=True, exist_ok=True)
    for name in _ARCHIVE_ITEMS:
        src = env.design / name
        if src.exists():
            shutil.move(str(src), str(dest / name))


def _go(ctx: Ctx, stage: str, *, layer: str = "", block: str = "", round: int = 0,
        source: str = "block") -> None:
    """转场：游标整体换新（asked/nudge 随新阶段清零）。"""
    cur = ctx.cur
    cur.clear()
    cur.update({"stage": stage, "layer": layer, "block": block, "round": int(round),
                "source": source, "nudge": 0, "asked": False})


def _boot(ctx: Ctx, fresh: bool) -> None:
    """载入/初始化账本；fresh（新用户回合）时按旧状态决定新任务 / 续拼人审 / 重试回写。"""
    env, led = ctx.env, Ledger.load(ctx.env.design)
    loaded = led is not None
    if led is None:
        led = Ledger.fresh(ctx.env.design)
        if env.design.exists() and any(env.design.iterdir()):
            _archive(env)                      # 无账本但有旧工作面：先归位再开新账
    ctx.led = led
    if fresh:
        if led.status in ("done", "halted"):
            carried = [l for l in LAYERS if led.layer(l)["state"] == "stale_pending"]
            _archive(env)
            led = Ledger.fresh(ctx.env.design)
            led.data["carried_stale"] = carried
            ctx.led = led
        elif led.status == "awaiting_review":
            _go(ctx, "gate_interpret")         # 人审续步：审 gate-int → 回写或优化环
        elif led.status == "writeback_failed":
            # B-F1：续跑按本轮人话分流——批准+意见混写（「同意，把 st-0002 拆成两条」）
            # 绝不许被当成纯授权直接不可逆回写、意见静默丢弃。只有**裸授权**（重试/批准
            # 词之外零内容）才直回写（不白烧一次评审子调用）；其余一律先过 gate_interpret，
            # 复用现成的意见登记 + 目标层及下游失效 + 优化环 + 回门链路。
            human_now = _human_text(ctx.state_messages)
            if _writeback_authorized(human_now) and _is_bare_authorization(human_now):
                _go(ctx, "writeback")
            else:
                _go(ctx, "gate_interpret")
        elif led.status == "active" and loaded:
            # B-F2/R-31：上一回合没跑完（取消/崩溃）的续跑留痕。旧写法先赋 "interrupted"
            # 再无条件覆盖回 "active"、中间没有 save——磁盘账本永远看不到 interrupted，
            # 契约 §9「六态可观测」是假闭环。裁定清偿最小形态：删死赋值，往账本既有
            # history 追加带时间戳痕迹，仍由下面 led.save() 单点落盘；不新增状态词。
            led.data["history"].append(f"resumed-from-interrupted@{_now()}")
        # 「重入驾驶=active」只在**新回合**成立：非 fresh 的图内重入（待决转述轮等）
        # 必须原样保留 awaiting_review，否则 h_gate 的 B-F4 守卫拿不到等待态事实。
        led.status = "active"
    led.save()


def _layer_of_id(node_id: str) -> str | None:
    for layer, prefix in TYPE_PREFIX.items():
        if str(node_id).startswith(prefix + "-"):
            return layer
    return None


def _first_live(entry: str, terminal: str, modes: dict[str, str]) -> str | None:
    i0, i1 = LAYERS.index(entry), LAYERS.index(terminal)
    for layer in LAYERS[i0:i1 + 1]:
        if modes.get(layer) != "skipped":
            return layer
    return None


def _rows_of(ctx: Ctx, layer: str) -> list[dict]:
    """当层活宇宙：KB 存量 ∪ 本层草稿（upsert，id 已由生成阶段补齐）。"""
    out = [dict(r) for r in ctx.kb_rows(layer)]
    drafts = ctx.env.drafts_dir(layer)
    if drafts.is_dir():
        for path in sorted(drafts.glob("*.json")):
            raw = _read_json(path) or {}
            for item in raw.get("nodes") or []:
                if isinstance(item, dict) and item.get("op") != "delete":
                    out.append(item)
    return out


def _chain_closure(seeds: list[str], chain_rows: list[dict]) -> set[str]:
    """链路影响闭包：种子（子树根 / 草稿引用的存量）自身 + 全部后代 + 上游祖先 + 祖先的同父兄弟。

    兄弟只带**自身**（接缝可见即可），不带兄弟子树——否则一条兄弟枝整枝被拖进宇宙，
    窄任务的闭包会趋近全 KB，既违背契约 §3「同父兄弟」的字面口径，也毁掉「审按范围缩」。
    兄弟真正引用的节点仍由种子（草稿引用解析）那条腿带回来。
    """
    by_id = {str(r.get("id") or ""): r for r in chain_rows}
    children: dict[str, list[str]] = {}
    for r in chain_rows:
        children.setdefault(str(r.get("parent") or ""), []).append(str(r.get("id") or ""))
    keep: set[str] = set()
    expand: list[str] = []
    for seed in seeds:
        stack = [str(seed or "")]
        while stack:                              # 目标子树本体：自身 + 全部后代
            cid = stack.pop()
            if not cid or cid in keep:
                continue
            keep.add(cid)
            expand.append(cid)
            stack.extend(children.get(cid, []))
    for cid in expand:                            # 上游祖先链 + 祖先的同父兄弟（接缝，只带自身）
        cur = cid
        while True:
            row = by_id.get(cur)
            if row is None:
                break
            parent = str(row.get("parent") or "")
            if not parent:
                break                             # 已是根：children[""] 是全树根，绝不能当兄弟收
            keep.add(parent)
            keep.update(children.get(parent, []))
            cur = parent
    return keep


def _kb_closure(ctx: Ctx) -> dict[str, list[dict]]:
    """R-10 影响闭包：给人审门大纲与 ④ 结构检查的 KB 存量快照（单点口径）。

    既不是「只含本次草稿」（回答不了「这次没动哪些」），也不是「全 KB」（违背审按范围缩）：
    **目标子树草稿 + 其上游祖先链（存量）+ 同父兄弟（接缝）**。全量任务（无 target_subtree）
    时整棵树就是影响闭包，原样返回。窄子树额外纳入草稿合法引用的存量及其祖先/兄弟——
    ④ 的引用解析是硬要求，闭包外的被引节点会被假判 broken_parent。
    orphan-as-root（父为空或父不在集合内）保留在 outline._tree_lines 作窄任务/父被删的退化路径。
    """
    rows = {layer: [dict(r) for r in ctx.kb_rows(layer)] for layer in LAYERS}
    descriptor = (ctx.led.data.get("task") or {}).get("descriptor") or {}
    subtree = str(descriptor.get("target_subtree") or "")
    if not subtree:
        return rows
    keep: dict[str, set[str]] = {layer: set() for layer in LAYERS}
    # 草稿合法引用的存量必须留在闭包内（与子树根同等待遇）
    keep[CHAIN] = _chain_closure(
        [subtree] + [str(c) for node in _draft_nodes(ctx, STORY)
                     for c in (node.get("chains") or [])], rows[CHAIN])
    for node in _draft_nodes(ctx, POINT):
        keep[STORY].add(str(node.get("story") or ""))
    for r in rows[STORY]:
        rid = str(r.get("id") or "")
        if rid and keep[CHAIN] & {str(c) for c in (r.get("chains") or [])}:
            keep[STORY].add(rid)
    for r in rows[POINT]:
        rid = str(r.get("id") or "")
        if rid and str(r.get("story") or "") in keep[STORY]:
            keep[POINT].add(rid)
    for node in _draft_nodes(ctx, STORY):
        keep[STORY].discard(str(node.get("id") or ""))   # 草稿 id 不是存量，不参与过滤
    return {layer: [r for r in rows[layer] if str(r.get("id") or "") in keep[layer]]
            for layer in LAYERS}


def _seed_counters(ctx: Ctx) -> None:
    for layer in LAYERS:
        best = 0
        for row in ctx.kb_rows(layer):
            m = re.search(r"(\d+)$", str(row.get("id") or ""))
            if m:
                best = max(best, int(m.group(1)))
        ctx.led.data["counters"][layer] = best


def _write_manifests(ctx: Ctx, descriptor: dict) -> None:
    for layer in LAYERS:
        rows = ctx.kb_rows(layer)
        _write_json(ctx.env.manifests_dir / f"kb-{layer}.json",
                    {"layer": layer, "count": len(rows), "nodes": rows})
    _write_json(ctx.env.manifests_dir / "sources.json",
                {"project_files": list(descriptor.get("source_files") or []),
                 "kb_files": KbClient(ctx.env.kb).list_business_files()})


def _target_chains(ctx: Ctx) -> set[str] | None:
    """target_subtree 与**当前链路宇宙**（KB 存量 ∪ 本 run 链草稿）的交集；None = 全量任务。

    与 `_enter_layer` 的块物化同源（都走 `in_scope_targets` 的 chains 腿）：h_plan 的幻影子树
    预检时链草稿还不存在，回溯重做后再次进层时草稿已经有了——用同一表达式两边都对。
    """
    descriptor = ctx.led.data["task"]["descriptor"]
    if not str(descriptor.get("target_subtree") or ""):
        return None
    return in_scope_targets(descriptor, _rows_of(ctx, CHAIN), [])["chains"]


def _phantom_subtree_halt(layer: str, subtree: str) -> _Halt:
    """契约 §7：幻影 target_subtree 显式终止报因，不空转、也不把「0 块」渲染成「已完成」。"""
    return _Halt(f"目标子树「{subtree or '全量'}」在链路树里不存在："
                 f"范围内没有任何可生成的块（{LAYER_CN[layer]}层）")


def _enter_layer(ctx: Ctx, layer: str) -> None:
    """进入一层：按活宇宙物化块（链路层 init_task 已物化）→ 游标指向该层生成。"""
    ctx.led.layer(layer)["state"] = "active"
    descriptor = ctx.led.data["task"]["descriptor"]
    subtree = str(descriptor.get("target_subtree") or "")
    scope: dict[str, set[str]] | None = None
    if layer == CHAIN:
        blocks = ["ALL"]
        if subtree and not _target_chains(ctx):
            # 兜底：h_plan 已在任何生成/评审之前做过同一预检，这里只挡直接以链路层入口的流。
            raise _phantom_subtree_halt(layer, subtree)
    else:
        story_rows = _rows_of(ctx, STORY) if layer == POINT else []
        scope = in_scope_targets(descriptor, _rows_of(ctx, CHAIN), story_rows)
        blocks = materialize_blocks(layer, scope)
    if not blocks and scope is not None and not scope["chains"]:
        # 幻影 target_subtree（契约 §7）：in_scope_targets 与真实链路求交后 chains 为空
        # → 范围里没有任何块，显式终止报因，不进循环空转、不把「0 块」渲染成「已完成」。
        # 注意与「chains 非空但 stories 暂为空」区分：后者是草稿坏引用（如 story 引用了
        # 不存在链路）导致的零块，属合法流——交给 ④ 结构检查在大纲门修复环处置。
        raise _phantom_subtree_halt(layer, subtree)
    ctx.led.data["task"]["plan"]["blocks"][layer] = blocks
    ctx.led.layer(layer)["blocks"] = [{"id": b, "state": "todo", "round": 0} for b in blocks]
    _go(ctx, "gen", layer=layer)


def _block_entry(ctx: Ctx, layer: str, block: str) -> dict:
    for entry in ctx.led.layer(layer)["blocks"]:
        if entry["id"] == block:
            return entry
    entry = {"id": block, "state": "todo", "round": 0}
    ctx.led.layer(layer)["blocks"].append(entry)
    return entry


def _next_block(ctx: Ctx, layer: str) -> str | None:
    for entry in ctx.led.layer(layer)["blocks"]:
        if entry["state"] != "done":
            return str(entry["id"])
    return None


def _errors_block(errors: list[str]) -> str:
    return "\n\n上一版未通过校验，请修正后重写整个文件：\n" + "\n".join(f"- {e}" for e in errors)


def _archive_review(ctx: Ctx, call_id: str, raw: str) -> None:
    """评审原文归档（契约 T6 评审口径：判决模型可解析，原文也不许丢）。"""
    path = ctx.env.reviews_dir / f"{call_id}.review.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw or "", encoding="utf-8")


def h_plan(ctx: Ctx) -> Any:
    """计划阶段：design/plan.json 到达并校验 → 探测/层判定/建账/物化清单 → 进首活层。"""
    plan_path = ctx.env.design / PLAN_NAME
    if not plan_path.is_file():
        return ctx.ask(plan_instruction())
    descriptor, errors = validate_plan(_read_json(plan_path), ctx.env.project_dir)
    if errors:
        # 含「来源文件必须是项目相对路径」边界错误：随 _errors_block 进 NUDGE 重试文案。
        return ctx.ask(plan_instruction() + _errors_block(errors))
    probe = {layer: summarize_probe(layer, ctx.kb_rows(layer)) for layer in LAYERS}
    stale = {str(x) for x in (ctx.led.data.get("carried_stale") or [])}
    modes = plan_layers(descriptor, probe, stale)
    if descriptor["target_subtree"]:
        owner = _layer_of_id(descriptor["target_subtree"])
        if owner is not None:
            modes[owner] = "skipped"           # R5：目标层自身只做只读上下文
    init_task(ctx.led.data, descriptor, probe, modes)
    subtree = str(descriptor.get("target_subtree") or "")
    if subtree and not _target_chains(ctx):
        # 契约 §7 幻影子树预检（在任何生成/评审之前）：target_subtree 指向的 id 不在链路树里
        # → 立刻终止报因。只靠 _enter_layer 的守卫不够：owner 层被 R5 置 skipped 时（例如
        # 幻影故事 id），入口层 chains=["ALL"] 绕过守卫，整层的真实评审调用与草稿先被烧掉。
        raise _phantom_subtree_halt(_layer_of_id(subtree) or descriptor["entry_layer"], subtree)
    _seed_counters(ctx)
    _write_manifests(ctx, descriptor)
    entry = _first_live(descriptor["entry_layer"], descriptor["terminal_layer"], modes)
    if entry is None:
        raise _Halt("计划窗口内没有任何需要生成的层")
    _enter_layer(ctx, entry)
    return None


def _ref_hint(ctx: Ctx, layer: str, block: str) -> str:
    if layer == CHAIN:
        return "父链路 id 见本块草稿与 design/manifests/kb-chain.json"
    if layer == STORY:
        return "链路 id 见 design/drafts/chain/ALL.json"
    path = _draft_file_of(ctx, STORY, block)
    return f"故事 id 见 {ctx.rel(path)}" if path else "故事 id 见 design/drafts/story/ 下各块草稿"


def _draft_file_of(ctx: Ctx, layer: str, node_id: str) -> Path | None:
    drafts = ctx.env.drafts_dir(layer)
    if not drafts.is_dir():
        return None
    for path in sorted(drafts.glob("*.json")):
        raw = _read_json(path) or {}
        for item in raw.get("nodes") or []:
            if isinstance(item, dict) and str(item.get("id") or "") == str(node_id):
                return path
    return None


def _gen_text(ctx: Ctx, layer: str, block: str, errors: list[str] | None = None) -> str:
    mode = str(ctx.led.layer(layer)["mode"])
    manifest = ctx.rel(ctx.env.manifests_dir / f"kb-{layer}.json") if mode == "update" else None
    return gen_instruction(
        layer, block,
        draft_path=ctx.rel(ctx.env.drafts_dir(layer) / f"{block}.json"),
        ref_hint=_ref_hint(ctx, layer, block),
        kb_manifest_path=manifest, errors=errors, mode=mode,
    )


def h_gen(ctx: Ctx) -> Any:
    """生成阶段：本块草稿到达 → 校验/补 id 回写 → 块评审 r0（内联）；no_change 直接过块。"""
    layer, led = ctx.cur["layer"], ctx.led
    block = ctx.cur["block"]
    if not block or _block_entry(ctx, layer, block)["state"] == "done":
        block = _next_block(ctx, layer)
    if block is None:
        _go(ctx, "audit", layer=layer)         # 本层块齐 → 层全局审
        return None
    ctx.cur["block"] = block
    draft_path = ctx.env.drafts_dir(layer) / f"{block}.json"
    raw = _read_json(draft_path)
    if raw is None:
        return ctx.ask(_gen_text(ctx, layer, block))
    if not raw.get("nodes") and str(raw.get("note") or "") == "no_change":
        led.data.setdefault("no_change", []).append({"layer": layer, "block": block})
        _block_entry(ctx, layer, block)["state"] = "done"
        _after_block(ctx, layer)
        return None
    nodes, errors = parse_draft_file(layer, draft_path)
    if errors:
        return ctx.ask(_gen_text(ctx, layer, block, errors=errors))
    for i, node in enumerate(nodes):           # 新节点 id 由驱动分配并回写草稿
        if node.op == "upsert" and not node.id:
            node.id = led.next_seq(layer)
            raw["nodes"][i]["id"] = node.id
    _write_json(draft_path, raw)
    _run_block_review(ctx, layer, block)
    return None


def _after_block(ctx: Ctx, layer: str) -> None:
    nxt = _next_block(ctx, layer)
    if nxt is not None:
        _go(ctx, "gen", layer=layer, block=nxt)
        return
    _go(ctx, "audit", layer=layer)             # 块齐：进层全局审


# ---- 意见簿：登记、销账、在途清单（块/审/人三条回路共用） ----

def _next_ref(ctx: Ctx) -> str:
    ctx.led.data["op_seq"] = int(ctx.led.data.get("op_seq") or 0) + 1
    return f"op-{ctx.led.data['op_seq']:02d}"


def _settled(op: dict) -> bool:
    return bool(op.get("resolved") or op.get("escalated"))


def _open_of(ctx: Ctx, layer: str, *, source: str | None = None,
             block: str | None = None) -> list[dict]:
    out = []
    for op in ctx.led.layer(layer)["opinions"]:
        if _settled(op):
            continue
        if source is not None and op["source"] != source:
            continue
        if block is not None and op["block"] != block:
            continue
        out.append(op)
    return out


def _register(ctx: Ctx, layer: str, entries: list[dict], *, source: str,
              block: str) -> None:
    """登记意见：同（source,key）仍有在途条目则复用其 ref，否则分配新 ref。

    复用而不是新增，是为了让「主智能体声称已改、评审子若再犯」表现为同一 ref 重新出现
    （销账后被再发即为新一轮条目），而不是同一条意见无限复制。
    """
    st = ctx.led.layer(layer)
    for entry in entries:
        op, key = entry["opinion"], entry["key"]
        if any(o["source"] == source and o["key"] == key and not _settled(o)
               for o in st["opinions"]):
            continue
        st["opinions"].append({
            "ref": _next_ref(ctx), "key": key, "source": source, "block": block,
            "target": op.target.model_dump(), "kind": op.kind, "ask": op.ask,
            "evidence": op.evidence, "resolved": False, "escalated": False,
            "disposition": "", "note": "",
        })


def _apply_resolutions(ctx: Ctx, resolutions: list) -> None:
    """复审回执销账：ref 全局唯一，跨层查；resolved=False 只更新 note 不销账。

    裁定 25（双文保留）：处置说明是「意见落点对照表」里人要看的东西，复审回执不得
    无条件顶掉它——两处都有时处置说明在前、回执说明以「复审：」缀在后。
    """
    for r in resolutions:
        for st in ctx.led.data["layers"].values():
            for op in st["opinions"]:
                if op["ref"] == r.ref:
                    if r.resolved:
                        op["resolved"] = True
                    if op["note"] and r.note:
                        # 全链路累积（有界于 ROUND_CAP）：唯一人审门要看得懂整条处置轨迹，故意不截断。
                        op["note"] = f"{op['note']}；复审：{r.note}"
                    else:
                        op["note"] = r.note or op["note"]


def _close_missing(ctx: Ctx, layer: str, source: str, issued_keys: set[str]) -> None:
    """审计源隐式销账：本轮未再出现的在途条目视为已消化（复审未见重现）。"""
    for op in ctx.led.layer(layer)["opinions"]:
        if op["source"] == source and not _settled(op) and op["key"] not in issued_keys:
            op["resolved"] = True
            op["note"] = op["note"] or "复审未见重现"


def _write_in_file(path: Path, ops: list[dict]) -> None:
    """在途清单文件（opt 的输入 / attr 的输入）：主智能体按 ref 逐条处置。"""
    _write_json(path, {"refs": [{"ref": o["ref"], "kind": o["kind"], "ask": o["ask"],
                                 "target": o["target"], "evidence": o["evidence"]}
                                for o in ops]})


def _opt_prefix(layer: str, block: str, source: str) -> str:
    if source == "block":
        return f"blk-{layer}-{block}"
    if source == "audit":
        return f"aud-{layer}"
    return f"human-{layer}"


_SOURCE_CN = {"block": "块评审", "audit": "全局审", "human": "人工评审"}
_CAUSES = ("业务信息不足", "契约冲突", "评审分歧", "成本超限")


def _block_review_brief(ctx: Ctx, layer: str, block: str, round_no: int) -> str:
    draft = ctx.rel(ctx.env.drafts_dir(layer) / f"{block}.json")
    open_ops = _open_of(ctx, layer, source="block", block=block)
    lines = [
        f"【块评审·{LAYER_CN[layer]}·块 {block}·第 {round_no} 轮】",
        f"请只读地审阅草稿 {draft}（可结合 design/manifests/ 下的存量清单），给出本块判决：",
        '{"opinions": [{"target": {"type": "node|seam|outside", "value": "节点 id 或 声称 id"}, '
        '"kind": "漏测|颗粒度|边界归属|命名漂移|失效", "ask": "怎么改", "evidence": "依据"}], '
        '"resolutions": [{"ref": "op-01", "resolved": true, "note": "为何已消化"}]}',
    ]
    if open_ops:
        lines.append("上一轮尚未销账的意见（逐条给 resolutions 回执）：")
        lines += [f"- {o['ref']}: {o['ask']}" for o in open_ops]
    lines.append("意见要可执行、可核对；没有意见就返回空数组。")
    return "\n".join(lines)


def _run_block_review(ctx: Ctx, layer: str, block: str) -> None:
    """块评审一轮（内联驱动）：清账 → 块 done；出意见 → 交 opt（主智能体）或超限归因。"""
    entry = _block_entry(ctx, layer, block)
    round_no = int(entry["round"])
    call_id = f"blk-{layer}-{block}-r{round_no}"
    out, raw = run_reviewer(
        ctx.task_tool, CASE_REVIEW_AGENT_ID, _block_review_brief(ctx, layer, block, round_no),
        model_cls=ReviewOut, call_id=call_id,
        title=f"块评审·{LAYER_CN[layer]}·{block}·r{round_no}", config=ctx.config)
    _archive_review(ctx, call_id, raw)
    _apply_resolutions(ctx, out.resolutions)
    _register(ctx, layer,
              [{"opinion": op, "key": f"{op.target.type}:{op.target.value}:{op.kind}"}
               for op in out.opinions],
              source="block", block=block)
    open_ops = _open_of(ctx, layer, source="block", block=block)
    if not open_ops:
        entry["state"] = "done"
        _after_block(ctx, layer)
        return
    if round_no >= ctx.round_cap():
        _go(ctx, "attribute", layer=layer, block=block, round=round_no, source="block")
        return
    _write_in_file(ctx.env.reviews_dir / f"blk-{layer}-{block}-in-r{round_no}.json", open_ops)
    _go(ctx, "opt", layer=layer, block=block, round=round_no, source="block")


def h_opt(ctx: Ctx) -> Any:
    """优化阶段：等主智能体产出「更新后的草稿 + 处置表」→ 校验/销账 → 回环复审。"""
    layer, block = ctx.cur["layer"], ctx.cur["block"]
    round_no, source = int(ctx.cur["round"]), ctx.cur["source"]
    prefix = _opt_prefix(layer, block, source)
    draft_path = (ctx.rel(ctx.env.drafts_dir(layer) / f"{block}.json") if block
                  else ctx.rel(ctx.env.drafts_dir(layer)))
    opinions_path = ctx.rel(ctx.env.reviews_dir / f"{prefix}-in-r{round_no}.json")
    fix_rel = ctx.rel(ctx.env.reviews_dir / f"{prefix}-fix-r{round_no}.json")
    text = opt_instruction(layer, block or "（本层各块）", round_no, draft_path=draft_path,
                           opinions_path=opinions_path, fix_path=fix_rel,
                           source_cn=_SOURCE_CN.get(source, source))
    raw = _read_json(ctx.env.reviews_dir / f"{prefix}-fix-r{round_no}.json")
    all_ops = {op["ref"]: op
               for st in ctx.led.data["layers"].values() for op in st["opinions"]}
    if raw is None and not ctx.cur.get("asked"):
        return ctx.ask(text, cap=FIX_CAP)          # 首派：等主智能体交回「草稿 + 处置表」
    errors = []
    if not isinstance(raw, dict) or not isinstance(raw.get("dispositions"), list):
        errors.append("处置表缺失或形状不对（须为 JSON 对象且含 dispositions 数组）")
    else:
        for i, row in enumerate(raw["dispositions"]):
            if not isinstance(row, dict) or str(row.get("ref") or "") not in all_ops:
                errors.append(f"dispositions[{i}]: ref 不在意见簿中")
            elif str(row.get("status") or "") not in ("fixed", "covered", "unresolved"):
                errors.append(f"dispositions[{i}]: status 须为 fixed|covered|unresolved")
    if not errors:
        errors = _drafts_errors(ctx, layer, block)
    if errors:
        return ctx.ask(text + _errors_block(errors), cap=FIX_CAP)
    _patch_ids(ctx, layer, block)                  # 本轮新添节点补 id
    for row in raw["dispositions"]:
        op = all_ops[str(row["ref"])]
        status, note = str(row["status"]), str(row.get("note") or "")
        op["disposition"] = status
        # 裁定 28（与裁定 25 对称）：处置侧也不得整体顶掉既有轨迹——唯一人审门要看得懂整条处置过程，
        # 新处置说明以「处置：」缀在旧轨迹（含复审回执）之后。
        if op["note"] and note:
            op["note"] = f"{op['note']}；处置：{note}"
        else:
            op["note"] = note or op["note"]
        if status in ("fixed", "covered"):
            op["resolved"] = True               # 主智能体声称已消化；复审再犯即会重新登记
        else:
            op["escalated"] = True              # 主智能体判定本轮消化不了 → 进未消化项
            _layer_of_op(ctx, op)["unresolved"].append(
                {"block": op["block"], "refs": [op["ref"]], "cause": "评审分歧",
                 "note": note or "主智能体判定本轮无法消化"})
    if source == "block":
        _block_entry(ctx, layer, block)["round"] = round_no + 1
        _run_block_review(ctx, layer, block)    # 复审 inline；下一轮 call_id 自然新鲜
        return None
    st = ctx.led.layer(layer)
    st["audit_round"] = int(st.get("audit_round") or 0) + 1
    _go(ctx, "audit", layer=layer)              # 审计环单调递增不回退（见 T8 关键约定）
    return None


def _layer_of_op(ctx: Ctx, op: dict) -> dict:
    for st in ctx.led.data["layers"].values():
        if op in st["opinions"]:
            return st
    return {}


def _drafts_errors(ctx: Ctx, layer: str, block: str) -> list[str]:
    """优化后草稿仍须可解析：块环查本块、层环查全层各块（broken JSON 直接重问）。

    no_change 块（空 nodes + note 标记）是合法终态，跳过——否则混合层走层环优化时
    会被空草稿绊住，重问到 FIX_CAP 后错误中止（见 _is_no_change）。
    """
    blocks = [block] if block else [str(e["id"]) for e in ctx.led.layer(layer)["blocks"]]
    errors: list[str] = []
    for bid in blocks:
        path = ctx.env.drafts_dir(layer) / f"{bid}.json"
        if _is_no_change(_read_json(path)):
            continue
        _, errs = parse_draft_file(layer, path)
        errors += [f"{bid}.json: {e}" for e in errs]
    return errors


def h_attribute(ctx: Ctx) -> Any:
    """归因阶段：轮次用尽仍有在途意见 → 主智能体写四选一归因 → 收口继续（不阻塞全局）。"""
    layer, block = ctx.cur["layer"], ctx.cur["block"]
    round_no, source = int(ctx.cur["round"]), ctx.cur["source"]
    scope = block or "layer"
    open_ops = _open_of(ctx, layer, source="block", block=block) if block else _open_of(ctx, layer)
    open_path = ctx.env.attribution_dir / f"open-{layer}-{scope}-r{round_no}.json"
    out_path = ctx.env.attribution_dir / f"attr-{layer}-{scope}-r{round_no}.json"
    text = attribute_instruction(layer, block or "（整层）",
                                 opinions_path=ctx.rel(open_path), out_path=ctx.rel(out_path))
    raw = _read_json(out_path)
    if raw is None:
        if not open_path.is_file():
            _write_in_file(open_path, open_ops)
        return ctx.ask(text, cap=FIX_CAP)
    cause, note = str(raw.get("cause") or ""), str(raw.get("note") or "").strip()
    if cause not in _CAUSES or not note:
        return ctx.ask(text + _errors_block([f"cause 须为 {'|'.join(_CAUSES)} 之一且 note 非空"]),
                       cap=FIX_CAP)
    ctx.led.layer(layer)["unresolved"].append(
        {"block": block, "refs": [o["ref"] for o in open_ops], "cause": cause, "note": note})
    for op in open_ops:
        op["escalated"] = True                  # 带账离开在途集：不再触发优化环
    if source == "block":
        _block_entry(ctx, layer, block)["state"] = "done"
        _after_block(ctx, layer)
        return None
    _layer_audited(ctx, layer)                  # 审计环收口：层过审 → 下一层 / gate
    return None


# ---- 层全局审（每层独立环；审计源隐式销账靠 _close_missing） ----

def _draft_nodes(ctx: Ctx, layer: str) -> list[dict]:
    """本层草稿节点（upsert 且非 delete）；audit 各钩子只吃草稿、不吃 KB 行。"""
    out: list[dict] = []
    drafts = ctx.env.drafts_dir(layer)
    if drafts.is_dir():
        for path in sorted(drafts.glob("*.json")):
            raw = _read_json(path) or {}
            for item in raw.get("nodes") or []:
                if isinstance(item, dict) and item.get("op") != "delete":
                    out.append(item)
    return out


def _draft_fingerprint(ctx: Ctx, layer: str) -> str:
    """草稿指纹做 call_id：内容没变 → 复用子摘要（重放零成本），变了 → 新一轮枚举。"""
    blob = "".join(p.read_text(encoding="utf-8")
                   for p in sorted(ctx.env.drafts_dir(layer).glob("*.json")))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:8]


def _enum_chain(ctx: Ctx) -> None:
    """①-a 盲枚举：面只读 + 简报不给树（结构盲靠白名单清单，不靠提示词叮嘱）。"""
    brief = (
        "【盲枚举·业务对象盘点】请只读地浏览 design/manifests/sources.json 列出的项目业务资料，"
        "不要依赖任何测试设计产物，列出其中出现的业务对象 / 角色 / 阶段。\n"
        '输出一个 JSON：{"items": [{"kind": "业务对象|角色|阶段", "name": "...", '
        '"evidence": "出现在哪个文件"}]}'
    )
    call_id = f"enum-chain-{_draft_fingerprint(ctx, CHAIN)}"
    out, raw = run_reviewer(ctx.task_tool, CASE_REVIEW_BLIND_AGENT_ID, brief,
                            model_cls=EnumeratorOut, call_id=call_id,
                            title="盲枚举·业务对象", config=ctx.config)
    _archive_review(ctx, call_id, raw)
    _write_json(ctx.env.manifests_dir / "enum-chain.json",
                {"items": [dict(i) for i in out.items]})


def _cmp_chain(ctx: Ctx, round_no: int) -> None:
    """①-b 对照器：逐条给落点；landing 为空 = 树外遗漏（显式登记为意见）。"""
    enum_path = ctx.rel(ctx.env.manifests_dir / "enum-chain.json")
    draft = ctx.rel(ctx.env.drafts_dir(CHAIN) / "ALL.json")
    brief = (
        f"【对照·落点核对】枚举清单在 {enum_path}；当前链路树草稿在 {draft}"
        "（存量见 design/manifests/kb-chain.json）。逐条判断每个枚举项在这棵树里有没有落点。\n"
        '输出一个 JSON：{"items": [{"name": "...", "kind": "...", "landing": "节点 id 或空串", '
        '"note": "落点为空时说明缺了什么"}]}（landing 为空 = 树外遗漏）'
    )
    call_id = f"cmp-chain-r{round_no}"
    out, raw = run_reviewer(ctx.task_tool, CASE_REVIEW_AGENT_ID, brief, model_cls=CompareOut,
                            call_id=call_id, title=f"对照·落点·r{round_no}",
                            config=ctx.config)
    _archive_review(ctx, call_id, raw)
    items = [dict(i) for i in out.items]
    ctx.led.layer(CHAIN)["enumeration"] = items
    issued: set[str] = set()
    entries: list[dict] = []
    for i in items:
        if str(i.get("landing") or "").strip():
            continue
        key = f"outside:{i.get('name', '')}"
        issued.add(key)
        entries.append({
            "opinion": Opinion(target=OpinionTarget(type="outside", value=str(i.get("name") or "")),
                               kind="漏测",
                               ask=f"树外遗漏：{i.get('name', '')}（{i.get('note') or '无落点'}）",
                               evidence=enum_path),
            "key": key,
        })
    _register(ctx, CHAIN, entries, source="audit", block="")
    _close_missing(ctx, CHAIN, "audit", issued | {e["key"] for e in entries})


def _claims_brief(ctx: Ctx, block: str, rows: list[dict]) -> str:
    return "\n".join([
        f"【声称核对·块 {block}·第 X 轮】用户故事对「由谁覆盖」有一个或多个声称（assumptions）。"
        "请逐条核对每个声称在树内是否真的被覆盖。",
        "待核对声称（ref 原样回填）：",
        json.dumps(rows, ensure_ascii=False),
        "故事与测试点的存量清单见 design/manifests/kb-story.json 与 design/drafts/。",
        '输出一个 JSON：{"claims": [{"ref": "...", "verdict": "covered|unclaimed", '
        '"owner": "覆盖它的故事 id 或空", "note": ""}], "opinions": []}',
        "unclaimed 表示没有任何节点认领这个声称（会被登记为接缝漏测意见）。",
    ])


def _claims_story(ctx: Ctx, round_no: int) -> None:
    """② 声称核对（按故事层块 = 链路分片）：unclaimed → seam 意见；复审未再出现即销账。"""
    st = ctx.led.layer(STORY)
    round_rows: list[dict] = []
    issued: set[str] = set()
    for entry in st["blocks"]:
        block = str(entry["id"])
        rows: list[dict] = []
        for node in _draft_nodes(ctx, STORY):
            if block not in [str(c) for c in (node.get("chains") or [])]:
                continue
            for i, claim in enumerate(node.get("assumptions") or []):
                rows.append({"ref": f"{node.get('id')}-a{i + 1}",
                             "claimant": str(node.get("id") or ""), "claim": str(claim)})
        if not rows:
            continue                                # 本块没有声称：不驱动核对器
        call_id = f"claims-story-r{round_no}-{block}"
        out, raw = run_reviewer(ctx.task_tool, CASE_REVIEW_AGENT_ID,
                                _claims_brief(ctx, block, rows), model_cls=ClaimsOut,
                                call_id=call_id,
                                title=f"声称核对·{block}·r{round_no}", config=ctx.config)
        _archive_review(ctx, call_id, raw)
        by_ref = {str(r.get("ref") or ""): r for r in out.claims if isinstance(r, dict)}
        enriched = [{**row,
                     "verdict": str((by_ref.get(row["ref"]) or {}).get("verdict") or "unclaimed"),
                     "owner": str((by_ref.get(row["ref"]) or {}).get("owner") or ""),
                     "note": str((by_ref.get(row["ref"]) or {}).get("note") or "")}
                    for row in rows]
        round_rows += enriched
        entries: list[dict] = []
        for row in enriched:
            if row["verdict"] != "unclaimed":
                continue
            key = f"claims:{row['ref']}"
            issued.add(key)
            entries.append({
                "opinion": Opinion(target=OpinionTarget(type="seam", value=row["ref"]),
                                   kind="漏测", ask=f"声称未认领：{row['claim']}",
                                   evidence=row["claimant"]),
                "key": key,
            })
        entries += [{"opinion": op, "key": f"{op.target.type}:{op.target.value}:{op.kind}"}
                    for op in out.opinions]
        _register(ctx, STORY, entries, source="audit", block=block)
        issued |= {e["key"] for e in entries}
    st["claims"] = round_rows
    _close_missing(ctx, STORY, "audit", issued)


def _matrix_brief(ctx: Ctx, chain_id: str, stories: list[dict], entities: list[str]) -> str:
    return "\n".join([
        f"【矩阵复核·链路 {chain_id}】对每个（业务实体 × 用户故事）组合判断当前测试点是否覆盖。",
        "故事：" + json.dumps([{"id": s.get("id"), "name": s.get("name")} for s in stories],
                             ensure_ascii=False),
        "业务实体：" + json.dumps(entities, ensure_ascii=False),
        "既有测试点草稿在 design/drafts/point/ 下（只读）。",
        '输出一个 JSON：{"cells": [{"entity": "...", "story": "故事 id", '
        '"verdict": "covered|needed|not_needed", "reason": "覆盖它/不需要它/缺它的业务理由"}]}',
        "判 not_needed 必须给 reason；判 needed = 应有测试点但缺失（会被登记为漏测意见）。",
    ])


def _matrix_point(ctx: Ctx, round_no: int) -> None:
    """③ 矩阵复核（按故事所属链路分片）：needed / 无理由空格 → 意见；not_needed+理由 → 入册。"""
    st = ctx.led.layer(POINT)
    stories = [n for n in _rows_of(ctx, STORY) if str(n.get("id") or "")]
    entities = sorted({str(e) for p in _rows_of(ctx, POINT) for e in (p.get("entities") or [])})
    shards: dict[str, list[dict]] = {}
    for s in stories:
        for cid in s.get("chains") or []:
            shards.setdefault(str(cid), []).append(s)
    round_cells: list[dict] = []
    issued: set[str] = set()
    for cid in sorted(shards):
        call_id = f"matrix-point-r{round_no}-{cid}"
        out, raw = run_reviewer(ctx.task_tool, CASE_REVIEW_AGENT_ID,
                                _matrix_brief(ctx, cid, shards[cid], entities),
                                model_cls=MatrixOut, call_id=call_id,
                                title=f"矩阵复核·{cid}·r{round_no}", config=ctx.config)
        _archive_review(ctx, call_id, raw)
        cells = [dict(c) for c in out.cells if isinstance(c, dict)]
        round_cells += cells
        entries: list[dict] = []
        for cell in cells:
            pair = f"{cell.get('entity', '')},{cell.get('story', '')}"
            verdict = str(cell.get("verdict") or "")
            reason = str(cell.get("reason") or "").strip()
            if verdict == "covered" or (verdict == "not_needed" and reason):
                continue
            key = f"matrix:{pair}"
            issued.add(key)
            ask = (f"矩阵空格待补点：{pair}" if verdict == "needed"
                   else f"矩阵判不需要但缺业务理由：{pair}")
            entries.append({
                "opinion": Opinion(target=OpinionTarget(type="matrix_cell", value=pair),
                                   kind="漏测", ask=ask, evidence=reason),
                "key": key,
            })
        _register(ctx, POINT, entries, source="audit", block=cid)
    st["matrix"] = round_cells
    _close_missing(ctx, POINT, "audit", issued)


def h_audit(ctx: Ctx) -> None:
    """层全局审一轮（内联驱动）：本层 ①/②/③ 判一遍 → 隐式销账 → 过审 / opt / 超限归因。"""
    layer = ctx.cur["layer"]
    round_no = int(ctx.led.layer(layer).get("audit_round") or 0)
    if layer == CHAIN:
        _enum_chain(ctx)
        _cmp_chain(ctx, round_no)
    elif layer == STORY:
        _claims_story(ctx, round_no)
    else:
        _matrix_point(ctx, round_no)
    open_ops = _open_of(ctx, layer, source="audit")
    if not open_ops:
        _layer_audited(ctx, layer)
        return
    if round_no >= ctx.round_cap():
        _go(ctx, "attribute", layer=layer, round=round_no, source="audit")
        return
    _write_in_file(ctx.env.reviews_dir / f"aud-{layer}-in-r{round_no}.json", open_ops)
    _go(ctx, "opt", layer=layer, block="", round=round_no, source="audit")


def _layer_audited(ctx: Ctx, layer: str) -> None:
    ctx.led.layer(layer)["state"] = "audited"
    _after_layer(ctx, layer)


def _after_layer(ctx: Ctx, layer: str) -> None:
    """层收口后：窗口内下一个 pending 层进入；没有就直接进大纲门（stale/skipped 都不动）。"""
    descriptor = ctx.led.data["task"]["descriptor"]
    i0 = LAYERS.index(descriptor["entry_layer"])
    i1 = LAYERS.index(descriptor["terminal_layer"])
    for nxt in LAYERS[i0:i1 + 1]:
        if LAYERS.index(nxt) <= LAYERS.index(layer):
            continue
        if ctx.led.layer(nxt)["state"] == "pending":
            _enter_layer(ctx, nxt)
            return
    _go(ctx, "gate")


# ---- 大纲门（④ 确定性检查 → 修复环 → 增量大纲呈递人审门） ----

def _scope_for_checks(ctx: Ctx) -> dict[str, set[str]]:
    """④ 检查范围（F 规则 × 计划块）：F = 最深「已收口（audited/done）」层下标。

    链路范围取本 run 链路草稿 id、故事范围取点层计划块——两层都只在「下游层确实进了本 run
    窗口」时才计入（F≥1 / F≥2），否则空链路/空故事会把「本次根本不动下游」误报成 hard。

    故事范围特意用点层计划块（物化时刻范围内故事）而非「草稿故事 id」：点层对某故事零块，
    就是「本 run 没有要求它出点」，不该判空故事（否则链层草稿缺失导致点层无块时修复环空转）。
    """
    layers = ctx.led.data["layers"]
    f = max((i for i, layer in enumerate(LAYERS)
             if layers[layer]["state"] in ("audited", "done")), default=-1)
    scope: dict[str, set[str]] = {"chains": set(), "stories": set()}
    if f >= 1:
        scope["chains"] = {str(n.get("id") or "") for n in _draft_nodes(ctx, CHAIN)}
    if f >= 2:
        scope["stories"] = {str(b) for b in
                            (ctx.led.data["task"]["plan"]["blocks"].get(POINT) or [])}
    scope["chains"].discard("")
    scope["stories"].discard("")
    return scope


def _gate_universe(ctx: Ctx, scope: dict[str, set[str]]) -> dict:
    """④ 引用宇宙：本 run 各层草稿 ∪ 影响闭包内的 KB 存量（R-10）；stale 层整层排除（防假 hard）。

    stale 层既然要在下次任务重跑，本 run 就不拿它的旧草稿/旧存量做一致性判据；它在大纲里
    以「失效待重算」状态行展示（`_tree_lines`），信息不丢。
    """
    drafts_by_layer: dict[str, list[dict]] = {}
    kb_rows: dict[str, list[dict]] = {}
    for layer in LAYERS:
        if ctx.led.layer(layer)["state"] == "stale_pending":
            drafts_by_layer[layer] = []
            kb_rows[layer] = []
            continue
        drafts_by_layer[layer] = _draft_nodes(ctx, layer)
        kb_rows[layer] = ctx.kb_rows_closure(layer)
    return build_universe(drafts_by_layer, kb_rows, scope)


def _outline_nodes(ctx: Ctx) -> dict[str, list[dict]]:
    """增量树输入：影响闭包内 KB 存量（只读上下文，标「存量」、无 op）∪ 本 run 草稿（按层模式标 新增/更新）。"""
    out: dict[str, list[dict]] = {}
    for layer in LAYERS:
        st = ctx.led.layer(layer)
        if st["state"] == "stale_pending":
            out[layer] = []                         # 状态行由 _tree_lines 出，节点不展开
            continue
        rows = [{**dict(r), "state": "存量"} for r in ctx.kb_rows_closure(layer)]
        mark = "新增" if st["mode"] == "first_build" else "更新"
        drafts = ctx.env.drafts_dir(layer)
        if drafts.is_dir():
            for path in sorted(drafts.glob("*.json")):
                raw = _read_json(path) or {}
                for item in raw.get("nodes") or []:
                    if not isinstance(item, dict) or not item.get("id"):
                        continue
                    node = dict(item)
                    node["state"] = "删除" if node.get("op") == "delete" else mark
                    rows.append(node)
        out[layer] = rows
    return out


def _duplicate_groups(nodes_by_layer: dict) -> list[list[str]]:
    """A11：按规范化名称分组、检出即入清单（是否合并交人审）；同 id 去重防「更新自己」误报。"""
    groups: list[list[str]] = []
    for layer in LAYERS:
        buckets: dict[str, list[str]] = {}
        for row in nodes_by_layer.get(layer) or []:
            if not isinstance(row, dict) or row.get("op") == "delete":
                continue
            nid = str(row.get("id") or "")
            key = re.sub(r"\s+", "", str(row.get("name") or "")).casefold()
            if nid and key and nid not in buckets.setdefault(key, []):
                buckets[key].append(nid)
        groups += [ids for ids in buckets.values() if len(ids) > 1]
    return groups


def _outline_extras(ctx: Ctx, nodes_by_layer: dict) -> dict:
    """大纲附加段（全部确定性组装）：dispositions=意见落点对照表、enumeration=① 对照等。"""
    layers = ctx.led.data["layers"]
    unresolved: list[dict] = []
    dispositions: list[dict] = []
    for layer in LAYERS:
        asks = {op["ref"]: op["ask"] for op in layers[layer]["opinions"]}
        for u in layers[layer]["unresolved"]:
            refs = [str(r) for r in (u.get("refs") or [])]
            unresolved.append({"layer": layer, "ref": ",".join(refs),
                               "ask": "；".join(asks.get(r, r) for r in refs),
                               "cause": u.get("cause", ""), "note": u.get("note", "")})
        for op in layers[layer]["opinions"]:
            status = ("已销账" if op["resolved"] else
                      "未消化（已归因）" if op["escalated"] else "在途")
            dispositions.append({
                "layer": layer, "ref": op["ref"],
                "source": _SOURCE_CN.get(op["source"], op["source"]),
                "kind": op["kind"],
                "target": f"{op['target'].get('type')}:{op['target'].get('value')}",
                "ask": op["ask"], "status": status,
                "disposition": op["disposition"], "note": op["note"],
            })
    return {
        "unresolved": unresolved,
        "duplicates": _duplicate_groups(nodes_by_layer),
        "claims": layers[STORY].get("claims") or [],
        "matrix_notes": [c for c in (layers[POINT].get("matrix") or [])
                         if str(c.get("verdict")) == "not_needed"
                         and str(c.get("reason") or "").strip()],
        "enumeration": layers[CHAIN].get("enumeration") or [],
        "dispositions": dispositions,
        "no_change": ctx.led.data.get("no_change") or [],
    }


def _live_window(ctx: Ctx, layer: str) -> bool:
    """该层是否真的参与了本 run 的一致性判定（skipped=不在窗口；stale_pending=整层排除出宇宙）。"""
    return ctx.led.layer(layer)["state"] not in ("skipped", "stale_pending")


def _drop_out_of_window_hards(ctx: Ctx, hard: list[dict],
                              scope: dict[str, set[str]]) -> tuple[list[dict], list[dict]]:
    """F 规则在 R-17 口径下的补全：empty_chain/empty_story 只在下游层真参与判定时才成立。

    checks.build_universe 现语义（契约 R-13/R-14/R-17）：引用解析走全宇宙、上报只认 in_scope、
    且**草稿行永远在范围内**。于是 `_scope_for_checks` 的 F≥1/F≥2 门控只静音得住 KB 存量，
    静音不住本 run 草稿：链层单独收口的任务里，链草稿因「宇宙里没有故事」被假判 empty_chain，
    而修复指令要求「在对应层草稿补节点」——对应层恰是 skipped/stale 规则不许动的层。
    本过滤把同一意图落到草稿行上（只丢弃确实不该成立的指控，六项 hard code 一项不动，
    T12 数字线不受影响）：
    - empty_chain：下游故事层不在本 run 有效宇宙 → 丢弃；
    - empty_story：测试点层不在本 run 有效宇宙，或被点名故事不在点层计划块里
      （「点层对某故事零块 = 本 run 没有要求它出点」，与 _scope_for_checks 同源判据）→ 丢弃。

    返回（保留, 豁免）：R-18(a) 要求豁免对人是**可见**的——「hard 全 0」绝不能冒充
    「0 条被下游未进窗口豁免掉」，所以被丢弃的条目原样交给大纲渲染计数。
    """
    point_blocks = scope.get("stories") or set()
    kept: list[dict] = []
    dropped: list[dict] = []
    for item in hard:
        code = str(item.get("code") or "")
        if code == "empty_chain" and not _live_window(ctx, STORY):
            dropped.append(item)
            continue
        if code == "empty_story" and (not _live_window(ctx, POINT)
                                      or str(item.get("where") or "") not in point_blocks):
            dropped.append(item)
            continue
        kept.append(item)
    return kept, dropped


def h_gate(ctx: Ctx) -> Any:
    """大纲门 ④：hard 检查清零（修复环 ≤round_cap 轮，仍非零 halted）→ 组增量大纲 → 呈递人审门。"""
    led, gate = ctx.led, ctx.led.data["gate"]
    scope = _scope_for_checks(ctx)
    report = run_checks(
        _gate_universe(ctx, scope),
        claims=led.layer(STORY).get("claims") or [],
        matrix_cells=led.layer(POINT).get("matrix") or [],
        unresolved={layer: led.layer(layer)["unresolved"] for layer in LAYERS})
    # 同源不变式（R-18(b)）：_live_window 与 scope 都读 layers[layer]["state"]，而进门的唯一路径
    # （_after_layer / _go(ctx, "gate")）保证窗口内每个非 skipped/非 stale 层都已被 _layer_audited
    # 置 audited；若将来加一条「跳过某层直接进 gate」的边，这里与 scope 会立刻漂开。
    kept, exempted = _drop_out_of_window_hards(ctx, report["hard"], scope)
    # exempted 只用于大纲渲染豁免计数（R-18(a)）：④ 每次进门都确定性重算，不必落账本。
    report = {**report, "hard": kept, "exempted": exempted}
    if not report["hard"] and led.status == "awaiting_review":
        # B-F4：待决转述轮里主智能体若无工具调用，图路由回 driver 时游标仍是 gate——
        # 再跑一遍会重写大纲并二次呈递「大纲已生成」终帧（人本轮的话没被答复却收到第二条
        # 呈递）。静默交回等待态：零重写、零终帧、零写入。守卫不许改成回 gate_interpret
        # （重复解读、重复烧评审子调用、可能重复登记意见）。
        return ctx.turn([], "end")
    if report["hard"]:
        if int(gate["round"]) >= ctx.round_cap():
            raise _Halt("大纲门结构检查连续未清零（修复环用尽）")
        gate["round"] = int(gate["round"]) + 1
        issues_path = ctx.env.reviews_dir / f"gate-issues-r{gate['round']}.json"
        _write_json(issues_path, {"round": gate["round"], "hard": report["hard"]})
        # B-F3 预算单点：这条环的预算就是 gate["round"]/round_cap（与 A7/契约 §9 同源），
        # ask 的 cap 显式传 round_cap——旧写法吃默认 NUDGE_CAP=3，「重试超限」会抢在
        # ROUND_CAP 分支前收口，有效修复指令只下发 4 次；如今第 round_cap+1 次进门
        # 必由上面那句 raise 以「修复环用尽」人话终结。
        return ctx.ask(gate_fix_instruction(issues_path=ctx.rel(issues_path),
                                            round_no=gate["round"]),
                       cap=ctx.round_cap())
    nodes = _outline_nodes(ctx)
    (ctx.env.design / OUTLINE_NAME).write_text(
        compose_outline(led.data, nodes, report, _outline_extras(ctx, nodes)),
        encoding="utf-8")
    led.status = "awaiting_review"
    _go(ctx, "gate")                               # 人审门游标：等 gate_interpret 续步
    return ctx.end("大纲已生成（design/outline.md），等待人工评审。")


# ---- 人审续步（唯一人审门：结构化人的要求 → 回溯重做；明示批准才回写） ----

# 批准措辞（闭合集）：回写是不可逆写，「解读子没提取到意见」不等于人说了通过。
_APPROVAL_WORDS: tuple[str, ...] = ("通过", "批准", "同意", "确认", "回写")
# 重试措辞：只在「回写失败的续跑」这条路合法（R-27），不进 _APPROVAL_WORDS——
# 在大纲门说一句「重试」不该触发一次不可逆写。
_RETRY_WORDS: tuple[str, ...] = ("重试",)
# 否定/延后标记：全句出现任一即不算明示批准。「不通过」「先别回写」里都含着批准词，字面
# 匹配会把一句拒绝读成授权——而这条路径的失败代价是不可逆的 KB 写入，方向只能偏保守。
_NEGATION_MARKS: tuple[str, ...] = ("不", "未", "别", "暂", "没", "先")
_GATE_UNDECIDED = (
    "【人审门·待决】上面这条人审消息既没有可执行的意见，也没有明示批准（通过/批准/同意/确认/回写）。"
    "本轮不回写知识库，大纲 design/outline.md 维持原状。请把上述状态转述给人并等待其明确答复，"
    "不要代替人给出批准。本轮只输出一条面向人的答复，不要改动任何文件。"
)


def _human_text(messages: list[BaseMessage]) -> str:
    """本轮人审原话 = 最后一条非空 HumanMessage。批准只认这一条：更早的话在它那一轮就处置完了。"""
    for m in reversed(messages):
        if isinstance(m, HumanMessage) and str(m.content or "").strip():
            return str(m.content).strip()
    return ""


def _explicit_approval(text: str) -> bool:
    """本轮人话是否是明示批准：出现批准措辞，且全句没有否定/延后标记。"""
    if any(mark in text for mark in _NEGATION_MARKS):
        return False
    return any(word in text for word in _APPROVAL_WORDS)


def _writeback_authorized(text: str) -> bool:
    """回写再入授权（R-27）：没有否定/延后标记，且出现批准措辞或「重试」。

    与 `_explicit_approval` 的差别只多认一个「重试」：那条路是**首次**批准（大纲门），
    措辞必须是批准词；这条路是裁定 19 的续跑支路——人已经在更早的回合过了一次门，
    本轮只需要一个不带否定的重试信号即可把不可逆写接着做完。否定句仍然一律不算授权。
    """
    if any(mark in text for mark in _NEGATION_MARKS):
        return False
    return any(word in text for word in _APPROVAL_WORDS + _RETRY_WORDS)


# 授权词之外的「杂质」剥离面：标点/空白（B-F1 判裸授权用）。
_AUTH_STRIP_RE = re.compile(r"[\s，。、！!？?；;：:,.~〜「」『』\"'`（）()]+")


def _is_bare_authorization(text: str) -> bool:
    """本轮人话是否是**裸授权**：剥掉标点与批准/重试措辞后不剩任何内容。

    B-F1：writeback_failed 续跑只有裸授权（「重试」「同意。」这类）才允许跳过解读
    直回写——「同意，把 st-0002 拆成两条」剥完措辞还剩「拆成两条」，意见必须先去
    gate_interpret 登记，绝不许被当成纯授权消费掉再按旧大纲做不可逆回写。
    """
    if not text:
        return False
    rest = _AUTH_STRIP_RE.sub("", text)
    for word in _APPROVAL_WORDS + _RETRY_WORDS:
        rest = rest.replace(word, "")
    return not rest


def _target_layer(ops: list) -> str:
    """人审意见的最浅（离链路最近）目标层：反馈落在哪层就从哪层重做成，下游全失效。"""
    idx = len(LAYERS) - 1
    for op in ops:
        ttype, value = str(op.target.type or ""), str(op.target.value or "")
        if ttype == "node":
            layer = _layer_of_id(value)
            if layer is not None:
                idx = min(idx, LAYERS.index(layer))
        elif ttype == "seam":
            idx = min(idx, LAYERS.index(STORY))
        elif ttype == "matrix_cell":
            idx = min(idx, LAYERS.index(POINT))
        else:                                      # outside（树外遗漏）：从链路层保守回溯
            idx = min(idx, LAYERS.index(CHAIN))
    return LAYERS[idx]


def h_gate_interpret(ctx: Ctx) -> Any:
    """人审门续步：审人话 → 结构化意见 → 目标层及其下各层失效 → 交人工优化环。

    回写是不可逆写，所以**批准必须明示**：解读子返回空 opinions 只代表「没提取到意见」，
    不等于人说了通过；两者都不是时本轮不做任何决定（不回写、不回溯），把待决状态交回主智能体转述。
    """
    led, gate = ctx.led, ctx.led.data["gate"]
    k = int(gate.get("int_round") or 0) + 1
    gate["int_round"] = k
    human_text = _human_text(ctx.state_messages)
    brief = "\n".join([
        "【人审解读·大纲门】人审是最权威的评审。把人审原话转成结构化意见（纯批准或没有要改的内容 → opinions 留空）：",
        "人审原文：",
        human_text or "（空）",
        "大纲 design/outline.md 与各层草稿 design/drafts/ 可只读核对；证据栏填人审原话。",
        '{"opinions": [{"target": {"type": "node|seam|outside", "value": "节点 id / 声称 id"}, '
        '"kind": "漏测|颗粒度|边界归属|命名漂移|失效", "ask": "怎么改", "evidence": "人审原话"}], '
        '"resolutions": []}',
    ])
    call_id = f"gate-int-r{k}"
    out, raw = run_reviewer(ctx.task_tool, CASE_REVIEW_AGENT_ID, brief, model_cls=ReviewOut,
                            call_id=call_id, title=f"人审解读·r{k}", config=ctx.config)
    _archive_review(ctx, call_id, raw)
    if out.opinions:
        gate["unclear"] = 0                        # 人给了意见：待决计数清零
    elif _explicit_approval(human_text):
        gate["approved_at"] = _now()
        gate["unclear"] = 0
        _go(ctx, "writeback")
        return None
    else:
        gate["unclear"] = int(gate.get("unclear") or 0) + 1
        if gate["unclear"] > NUDGE_CAP:            # 连续待决：失败模式收口在 halted，绝不猜批准
            raise _Halt("人审门连续未给出可执行意见也未明示批准")
        # 待决＝本轮不做任何决定：游标回 gate（_boot 的 awaiting_review 分支才认得出人审续步），
        # 指令只教主智能体向人转述；状态保持 awaiting_review，回写仍然零次。
        led.status = "awaiting_review"
        _go(ctx, "gate")
        return ctx.ask(_GATE_UNDECIDED)
    target = _target_layer(out.opinions)
    for layer in LAYERS[LAYERS.index(target) + 1:]:
        if ctx.led.layer(layer)["state"] != "skipped":
            ctx.led.layer(layer)["state"] = "stale_pending"
    st = ctx.led.layer(target)
    if st["mode"] == "skipped":
        st["mode"] = "update"                      # 人审可把本不在窗口的层拉回来重做
    st["state"] = "pending"
    _register(ctx, target,
              [{"opinion": op, "key": f"{op.target.type}:{op.target.value}:{op.kind}"}
               for op in out.opinions],
              source="human", block="")
    _write_in_file(ctx.env.reviews_dir / f"human-{target}-in-r{k}.json",
                   _open_of(ctx, target, source="human"))
    _go(ctx, "opt", layer=target, block="", round=k, source="human")
    return None


# ---- 回写（只回写本 run 过审层；人审通过之前零 KB 写入） ----

def _patch_ids(ctx: Ctx, layer: str, block: str) -> None:
    """优化/回溯阶段新添的节点仍可能留空 id：以账本序分配并就地改写草稿。

    顺带撤回 no_change 记账：块先以「判定无变化」过账、后来被优化/人工回溯写进了真实节点时，
    「本次无变化块」清单必须跟着缩——否则大纲说没动、回写却在推，两处自相矛盾。
    """
    blocks = [block] if block else [str(e["id"]) for e in ctx.led.layer(layer)["blocks"]]
    for bid in blocks:
        path = ctx.env.drafts_dir(layer) / f"{bid}.json"
        raw = _read_json(path)
        if not isinstance(raw, dict):
            continue
        changed = False
        for item in raw.get("nodes") or []:
            if (isinstance(item, dict) and item.get("op") != "delete"
                    and not str(item.get("id") or "")):
                item["id"] = ctx.led.next_seq(layer)
                changed = True
        if changed:
            _write_json(path, raw)
        if raw.get("nodes"):
            ctx.led.data["no_change"] = [
                e for e in (ctx.led.data.get("no_change") or [])
                if not (e.get("layer") == layer and str(e.get("block") or "") == bid)]


_BANNED_PAYLOAD_KEYS = ("op", "block", "state", "in_scope", "round", "reason")


def _collect_writeback_items(ctx: Ctx) -> list[tuple[str, str, dict | None]]:
    """待写项 = 过审层各块草稿节点，按 (layer, id) 去重；payload=None 表示删除。

    同一个节点（尤其 delete）会被同层多个块草稿重复表达：不去重就按文件重复下发，
    幂等所以不坏，但人在唯一的那道门上看到 N 条一模一样的删除。
    """
    items: list[tuple[str, str, dict | None]] = []
    seen: set[tuple[str, str]] = set()
    for layer in LAYERS:
        if ctx.led.layer(layer)["state"] != "audited":
            continue
        for path in sorted(ctx.env.drafts_dir(layer).glob("*.json")):
            if _is_no_change(_read_json(path)):
                continue                   # 无变化块：零节点零写入（合法终态）
            nodes, errors = parse_draft_file(layer, path)
            if errors:
                raise KbClientError(f"{layer}/{path.name} 不可解析：{errors[0]}")
            for node in nodes:
                nid = str(node.id or "")
                if node.is_delete():
                    if not nid or (layer, nid) in seen:
                        continue
                    seen.add((layer, nid))
                    items.append((layer, nid, None))
                    continue
                payload = node.model_dump()
                for banned in _BANNED_PAYLOAD_KEYS:
                    payload.pop(banned, None)
                if nid:
                    if (layer, nid) in seen:
                        continue
                    seen.add((layer, nid))
                items.append((layer, nid, payload))
    return items


def h_writeback(ctx: Ctx) -> Any:
    """回写：只写 state=="audited" 层（stale/skipped 一律不写）；失败自动重试 1+2 次。

    再入授权闸门（R-27 纵深防御）：这条路有两个入口——大纲门明示批准（h_gate_interpret）与
    writeback_failed 的续跑（_boot 已按本轮人话分流：无授权的消息根本走不到这里，B-F1）。
    闸门仍放在唯一真正下笔的地方原样保留：任何漂移把未授权消息送进来，依旧零写入、状态原样退回。
    """
    led, wb = ctx.led, ctx.led.data["writeback"]
    if not _writeback_authorized(_human_text(ctx.state_messages)):
        led.status = "writeback_failed"                        # _boot 已置 active：原样退回
        return ctx.end("回写未执行：本轮没有重试授权——回复「重试」继续回写；"
                       "把意见写在消息里即可退回大纲门重做。")
    for layer in LAYERS:
        if led.layer(layer)["state"] == "audited":
            _patch_ids(ctx, layer, "")             # 兜底：任何空 id 在写库前补齐
    kb = KbClient(ctx.env.kb)
    ok = False
    for attempt in range(1, WRITEBACK_FIX_CAP + 2):
        try:
            for layer, node_id, payload in _collect_writeback_items(ctx):
                if payload is None:
                    kb.delete_node(layer, node_id)
                else:
                    kb.upsert_node(layer, payload)
            ok = True
            break
        except GraphBubbleUp:
            raise                                # 中断/暂停语义原样上抛，不占回写预算
        except Exception as exc:                 # 单次失败即整轮重来（upsert 幂等、delete 幂等）；
            # KbClient 只包 TimeoutError，KbUnavailableError 等 manager 侧异常在重试预算内
            # 一并收敛（T6 评审 I-1），不许以未处理异常形式掀翻图。
            wb["log"].append(f"第 {attempt} 次回写失败：{exc}")
    if not ok:
        led.status = "writeback_failed"
        return ctx.end("回写失败（已自动重试 3 次）：知识库暂不可写；回复「重试」可再次尝试。")
    for layer in LAYERS:
        if led.layer(layer)["state"] == "audited":
            led.layer(layer)["state"] = "done"
    wb["done"] = True
    led.status = "done"
    return ctx.end("回写完成：本次过审节点已写入知识库。")


_STAGE_HANDLERS: dict[str, Any] = {
    "plan": h_plan, "gen": h_gen, "opt": h_opt, "attribute": h_attribute,
    "audit": h_audit, "gate": h_gate, "gate_interpret": h_gate_interpret,
    "writeback": h_writeback,
}


# ---- 驱动主循环（T9 driver 节点的唯一入口） ----

def drive_turn(state: dict, config: Any, *, env: Any, task_tool: Any, writer: Any) -> dict:
    """一次驱动激活：把游标能走完的内部阶段全走完，产出 {messages, case}。

    case["route"]=="agent" 时 messages 为一条指令（HumanMessage），图去跑主智能体；=="end"
    时为终局（终帧已由 ctx.end 经 writer 发出，stream_graph 折成 reply）。env=None 直通：
    零副作用，只按「最后一条是不是用户消息」判 route（free/echo/单测与存量测试的兼容缝）。
    """
    messages = list(state.get("messages") or [])
    last = messages[-1] if messages else None
    case = dict(state.get("case") or {})
    instr_id = str(case.get("instr_id") or "")
    last_id = str(getattr(last, "id", None) or "")
    fresh = (not case.get("boot")) and isinstance(last, HumanMessage) \
        and (not instr_id or last_id != instr_id)
    ticks = int(case.get("ticks") or 0) + 1
    if env is None:
        route = "agent" if isinstance(last, HumanMessage) else "end"
        return {"messages": [], "case": {"route": route, "boot": True, "ticks": ticks,
                                         "instr_id": instr_id}}
    ctx = Ctx(env=env, task_tool=task_tool, writer=writer, config=config,
              state_messages=messages, case=case, ticks=ticks)
    try:
        env.ensure_dirs()
        _boot(ctx, fresh)
        while ctx.transitions < MAX_TRANSITIONS:
            ctx.transitions += 1
            result = _STAGE_HANDLERS[ctx.cur["stage"]](ctx)
            if isinstance(result, dict):
                return result                      # ctx.end 已发终帧并存盘
            if result is not None:
                ctx.led.save()
                return ctx.turn([result], "agent")
            ctx.led.save()
        raise _Halt(f"单次驱动转场超过上限（{MAX_TRANSITIONS}）")
    except GraphBubbleUp:
        raise
    except _Halt as exc:
        if ctx.led is not None:
            ctx.led.status = "halted"
        return ctx.end(f"测试设计任务中止：{exc}")
    except Exception as exc:                       # A3：其余异常收敛 halted，不炸图
        logger.exception("case_design 驱动失败")
        if ctx.led is not None:
            ctx.led.status = "halted"
        return ctx.end(f"测试设计任务中止（内部错误：{exc}）")
