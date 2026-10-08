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
    CASE_BATCH_CAP, CASE_DELIVERY_NAME, CASE_PREFIX, CASE_REVIEW_AGENT_ID,
    CASE_REVIEW_BLIND_AGENT_ID, CASES_DIR_NAME, CHAIN, FIX_CAP, HALT_KIND_CN, HALT_KINDS,
    LAYERS, LAYER_CN, MAX_TRANSITIONS, NUDGE_CAP, OUTLINE_NAME, PLAN_NAME, POINT, ROUND_CAP,
    STORY, TYPE_PREFIX, WRITEBACK_FIX_CAP,
)
from aitester.case_design.instructions import (
    attribute_instruction, case_attribute_instruction, case_gate_fix_instruction,
    case_gen_instruction, case_opt_instruction, gate_fix_instruction, gen_instruction,
    opt_instruction, plan_instruction,
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
    dedup_written, parse_case_file, parse_draft_file,
)
from aitester.case_design.writing import compose_case_delivery, plan_case_targets, run_case_checks

logger = logging.getLogger(__name__)

_ARCHIVE_ITEMS = (PLAN_NAME, OUTLINE_NAME, "drafts", "reviews", "attribution", "manifests",
                  CASES_DIR_NAME, CASE_DELIVERY_NAME)


class _Halt(RuntimeError):
    """驱动内的确定性中止。kind 必须是 HALT_KINDS 之一且**由抛点自标**（裁定 43）：
    续跑策略按族分流，靠 reason 文案猜族等于把产品语义押在字符串上。"""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        if kind not in HALT_KINDS:
            raise ValueError(f"非法 halt 族属「{kind}」，允许 {HALT_KINDS}")
        self.kind = kind


class DraftUnparseable(KbClientError):
    """确定性坏草稿（不可解析 / update 无账 no_change）：重试必再炸，不占回写重试预算。

    继承 KbClientError 保证所有既有 `except KbClientError` 语义不变；`h_writeback`
    单独先捕它——把这种输入说成「知识库暂不可写」会把人往「重试」的错方向支（F-6）。
    """


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
    halt_refusal: str = ""       # 非空＝本轮一步都不许走（needs_input，裁定 44），驱动直接终局
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
        """续跑补充语的唯一注入点（裁定 45）：`ask` 与 `h_case_plan` 的裸下发共用这里，用后即清。"""
        assert self.led is not None
        note = str(self.led.data["halt"].get("resume_note") or "")
        if note:
            self.led.data["halt"]["resume_note"] = ""
            text = f"{text}\n【人工补充】{note}"
        return HumanMessage(content=text, id=f"cdinstr-{self.ticks}")

    def ask(self, text: str, cap: int = NUDGE_CAP) -> HumanMessage:
        """带重试上限的下发：首问只置 asked，重问加 nudge；用尽即中止（plan/gen 3、opt/attr 2）。"""
        cur = self.cur
        if cur.get("asked"):
            if int(cur.get("nudge") or 0) >= cap:
                raise _Halt(f"{cur['stage']}/{cur['layer'] or '-'}/{cur['block'] or '-'} 重试超限",
                            "artifact_retry")
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


def _no_change_booked(ctx: Ctx, layer: str, block: str) -> bool:
    """无变化块的采信单点：本 run 的 h_gen 记过账才算「判定过」（R-52，与首建同方向不静默）。"""
    return any(str(e.get("layer")) == layer and str(e.get("block")) == block
               for e in (ctx.led.data.get("no_change") or []))


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


def _book_halt(ctx: Ctx, kind: str, reason: str) -> None:
    """halt 现场落盘的唯一实现处（裁定 43）：游标快照＋停留计数。整节重写，resume_note 随之一清。"""
    halt, cur = ctx.led.data["halt"], ctx.cur
    same = (halt.get("kind") == kind and halt.get("stage") == cur["stage"]
            and halt.get("layer") == cur["layer"] and halt.get("block") == cur["block"])
    halt.update({
        "kind": kind, "reason": reason, "stage": cur["stage"], "layer": cur["layer"],
        "block": cur["block"], "round": int(cur["round"] or 0), "at": _now(),
        "count": (int(halt.get("count") or 0) + 1) if same else 1, "resume_note": ""})


def _go(ctx: Ctx, stage: str, *, layer: str = "", block: str = "", round: int = 0,
        source: str = "block") -> None:
    """转场：游标整体换新（asked/nudge 随新阶段清零）。"""
    cur = ctx.cur
    cur.clear()
    cur.update({"stage": stage, "layer": layer, "block": block, "round": int(round),
                "source": source, "nudge": 0, "asked": False})


def _restart(ctx: Ctx, old: Ledger) -> Ledger:
    """销毁现场、开新账（裁定 42 里唯一该归档的那条路）：done 的新回合，或 halted 被明示重开。"""
    carried = [l for l in LAYERS if old.layer(l)["state"] == "stale_pending"]
    _archive(ctx.env)
    led = Ledger.fresh(ctx.env.design)
    led.data["carried_stale"] = carried
    ctx.led = led
    return led


def _reset_gate_unclear(led: Ledger, stage: str) -> None:
    """续跑复位待决计数（裁定 44）：只复位游标那道门的那一个，另一道门的账不许顺手洗白。"""
    if stage == "gate_interpret":
        led.data["gate"]["unclear"] = 0
    elif stage == "case_gate_interpret":
        led.data["writing"]["gate"]["unclear"] = 0


def _resume_halted(ctx: Ctx) -> bool:
    """halted 的新回合（裁定 42/44）：默认从断点续跑。返回 False＝本轮一步都不该走。

    续跑复用 `_go` 是因为它清整张游标：nudge/asked 归零（该重开的重开），
    而 round 由调用方**原样传回**——断一次白送 5 轮就是换个姿势重烧（裁定 44）。
    """
    led = ctx.led
    halt = led.data["halt"]
    if _wants_restart(_human_text(ctx.state_messages)):
        new = _restart(ctx, led)
        new.data["history"].append(f"restarted-from-halted@{_now()}")
        return True
    kind = str(halt.get("kind") or "transient")
    if kind == "needs_input":
        # 拒绝放行也要把「停在同一处」的读数说实（spec 走查五判据 ③：`halt["count"]` 递增）：
        # 这一族不会重放，但人连着发几句「继续」时，终帧那句「第 N 次」必须跟着涨。
        _book_halt(ctx, kind, str(halt.get("reason") or ""))
        ctx.halt_refusal = _needs_input_text(ctx.led.data["halt"])
        return False
    cur = dict(led.cursor)
    _go(ctx, cur["stage"], layer=cur["layer"], block=cur["block"],
        round=int(cur["round"] or 0), source=str(cur.get("source") or "block"))
    if kind == "human_wait":
        _reset_gate_unclear(led, cur["stage"])
    elif kind in ("artifact_retry", "transient"):
        led.data["halt"]["resume_note"] = _human_text(ctx.state_messages)   # 裁定 45，T31 消费
    led.data["history"].append(f"resumed-from-halted@{_now()} kind={kind}")
    return True


def _needs_input_text(halt: dict) -> str:
    """停在「续跑也无解」那一族：终帧说实话——停在哪、为什么、要人先做什么、本轮什么都没花。"""
    where = f"{halt.get('stage') or '-'}/{halt.get('layer') or '-'}/{halt.get('block') or '-'}"
    return "\n".join([
        f"测试设计任务停在 {where}（第 {int(halt.get('round') or 0)} 轮）：{halt.get('reason') or ''}",
        f"这是第 {int(halt.get('count') or 1)} 次停在同一处。这类停顿续跑也无解——"
        "请先修正上面的业务信息或目标范围，然后明说「重开任务」开启新任务。"
        "本轮未做任何生成、未写入知识库、未归档现场。",
    ])


def _halt_frame(ctx: Ctx, base: str) -> str:
    """中止终帧：基句一字不动（既有逐字断言），尾巴说实话＋给下一步（裁定 42/47、W4-2）。"""
    halt = ctx.led.data["halt"] if ctx.led is not None else {}
    if not halt.get("kind"):
        return base
    where = f"{halt['stage']}/{halt['layer'] or '-'}/{halt['block'] or '-'}"
    return (f"{base}\n现场已保留（停在 {where} 第 {halt['round']} 轮，"
            f"第 {halt['count']} 次停在这一处；{HALT_KIND_CN.get(halt['kind'], halt['kind'])}）。"
            "再发一句将从这里续跑；要放弃这次工作请明说「重开任务」。")


def _boot(ctx: Ctx, fresh: bool) -> None:
    """载入/初始化账本；fresh（新用户回合）时按旧状态决定新任务 / 断点续跑 / 续拼人审 / 重试回写。

    `done` 与 `halted` 分家（裁定 42）：前者是任务已了结，新回合开新账；后者默认从断点续跑，
    只有措辞明示重开才销毁现场。整个 fresh 块一律读 `ctx.led`——`_restart` 会换绑账本，
    局部变量会把旧字典盖回新账本（R-70）。
    """
    env, led = ctx.env, Ledger.load(ctx.env.design)
    loaded = led is not None
    if led is None:
        led = Ledger.fresh(ctx.env.design)
        if env.design.exists() and any(env.design.iterdir()):    # 无账本但有旧工作面：先归位再开新账
            _archive(env)
    ctx.led = led
    if fresh:
        ctx.led.data["halt"]["resume_note"] = ""       # 补充语只活一个回合（裁定 45）
        if ctx.led.status == "done":
            _restart(ctx, ctx.led)
        elif ctx.led.status == "halted":
            if not _resume_halted(ctx):
                ctx.led.save()                         # needs_input：状态原样 halted
                return
        elif ctx.led.status == "awaiting_review":
            if ctx.led.data["writing"].get("status") == "awaiting_review":
                _go(ctx, "case_gate_interpret")    # 末门续步：批准还要过机器账，见 h_case_gate_interpret
            else:
                _go(ctx, "gate_interpret")         # 人审续步：审 gate-int → 回写或优化环
        elif ctx.led.status == "writeback_failed":
            # B-F1：续跑按本轮人话分流——批准+意见混写（「同意，把 st-0002 拆成两条」）
            # 绝不许被当成纯授权直接不可逆回写、意见静默丢弃。只有**裸授权**（重试/批准
            # 词之外零内容）才直回写（不白烧一次评审子调用）；其余一律先过 gate_interpret，
            # 复用现成的意见登记 + 目标层及下游失效 + 优化环 + 回门链路。
            human_now = _human_text(ctx.state_messages)
            if _writeback_authorized(human_now) and _is_bare_authorization(human_now):
                _go(ctx, "writeback")
            else:
                _go(ctx, "gate_interpret")
        elif ctx.led.status == "active" and loaded:
            # B-F2/R-31：上一回合没跑完（取消/崩溃）的续跑留痕。旧写法先赋 "interrupted"
            # 再无条件覆盖回 "active"、中间没有 save——磁盘账本永远看不到 interrupted，
            # 契约 §9「六态可观测」是假闭环。裁定清偿最小形态：删死赋值，往账本既有
            # history 追加带时间戳痕迹，仍由下面 ctx.led.save() 单点落盘；不新增状态词。
            ctx.led.data["history"].append(f"resumed-from-interrupted@{_now()}")
        # 「重入驾驶=active」只在**新回合**成立：非 fresh 的图内重入（待决转述轮等）
        # 必须原样保留 awaiting_review，否则 h_gate 的 B-F4 守卫拿不到等待态事实。
        ctx.led.status = "active"
    ctx.led.save()


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
    """当层活宇宙：KB 存量 ∪ 本层草稿（upsert，id 已由生成阶段补齐），同 id 已并成写库侧那一份。

    终评 I-1：草稿行离开磁盘就没人认得它是草稿了——`DraftNode.op` 有默认值 `upsert`，模型省略
    `op` 时盘上是合法的 upsert，读回字典却只剩「无 state 无 op」，与 KB 存量无从区分；判别式一猜
    就错，方向还正好与写库侧相反（猜成存量 ⇒ 后见覆盖）。来源在这个函数里还是事实（KB 在前、
    草稿在后），所以在此自报：草稿行一律补 `state`（生成侧已标的原样保留），下游不必再猜。
    """
    out = [dict(r) for r in ctx.kb_rows(layer)]
    drafts = ctx.env.drafts_dir(layer)
    if drafts.is_dir():
        for path in sorted(drafts.glob("*.json")):
            raw = _read_json(path) or {}
            for item in raw.get("nodes") or []:
                if isinstance(item, dict) and item.get("op") != "delete":
                    out.append({**item, "state": str(item.get("state") or "更新")})
    return dedup_written(out)


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
    """契约 §7：幻影 target_subtree 显式终止报因，不空转、也不把「0 块」渲染成「已完成」。

    B-F5：非链路形状 id（st-/pt-）单独给改填指引——对**真实存在**的故事/测试点 id 说
    「在链路树里不存在」是误导；链路形状且查无此节点才保持现文。只改文案，预检时机不动。
    """
    if _layer_of_id(str(subtree or "")) not in (None, CHAIN):
        return _Halt(f"目标子树「{subtree}」必须是链路（ch-）id：指向用户故事或测试点时，"
                     f"请填其所属链路 id", "needs_input")
    return _Halt(f"目标子树「{subtree or '全量'}」在链路树里不存在："
                 f"范围内没有任何可生成的块（{LAYER_CN[layer]}层）", "needs_input")


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


# ---- 第四层编写环：计划侧（裁定 40）----

_CASE_WRITING_KINDS = ("case_only", "mixed")


def _writing_enabled(ctx: Ctx) -> bool:
    """编写环是否参与本 run——只吃账本里的 task_kind 事实，不从对话猜。"""
    kind = str(((ctx.led.data.get("task") or {}).get("descriptor") or {}).get("task_kind") or "")
    return kind in _CASE_WRITING_KINDS


def _case_targets(ctx: Ctx) -> list[dict]:
    """编写环的取数单点：活宇宙 = KB 存量 ∪ 本 run 草稿，范围走 `in_scope_targets`（与三层同源）。

    mixed 的同一轮里 `ctx.kb_rows` 缓存的是**回写前**的存量，只用它会漏掉本 run 刚过审的点；
    `_rows_of` 把草稿拼在后面并按 `schema.dedup_written` 并成写库侧那一份（同 id 取先出现的草稿，
    与写库侧 `seen` 首见即留同源），分母就是回写后的现稿。
    """
    descriptor = (ctx.led.data.get("task") or {}).get("descriptor") or {}
    chains, stories, points = (_rows_of(ctx, CHAIN), _rows_of(ctx, STORY), _rows_of(ctx, POINT))
    scope = in_scope_targets(descriptor, chains, stories)
    return plan_case_targets(chains, stories, points, scope)


def _case_ready_or_block(ctx: Ctx) -> str:
    """编写环前置（裁定 40）：三层已维护 + 无失效待重算 + 分母点数 > 0。

    返回空串 = 可开工；非空 = 给人看的拒因。不满足时**明示边界停在设计侧**，不静默重生成三层
    （那会把已过审的制品按旧口径再烧一遍），也不 halted（这是合法的业务状态，不是程序故障）。

    「三层未维护」腿读**活宇宙**（`_rows_of` = KB 存量 ∪ 本 run 草稿），与分母 `_case_targets`
    同一份口径——而不是 `task["probe"]` 那份冻结于计划时刻的快照。mixed 旗舰路径里，三层在计划时
    还是空的（快照全 maintained:False），设计环生成/评审/回写把节点写进 KB 后才转到 `case_plan`；
    若沿用计划时快照，编写环会在同一条任务里永远判为「未开工」，把刚写好的分母挡死。单一事实源
    优先于兼容，故不留 plan-time probe 作 fallback。
    """
    led = ctx.led.data
    probe = {layer: summarize_probe(layer, _rows_of(ctx, layer)) for layer in LAYERS}
    # 事实源以 carried_stale 为主：layers.*.state 会被 init_task 按 modes 覆写（case_only 全成
    # skipped），只读它就永远看不见上一轮留下的失效层。两处取并集，本 run 内标的失效也认。
    stale = {*(led.get("carried_stale") or [])} | {
        layer for layer in LAYERS if ctx.led.layer(layer)["state"] == "stale_pending"}
    if stale:
        return ("、".join(LAYER_CN[l] for l in LAYERS if l in stale)
                + "层标了失效待重算，请先跑一次测试设计任务把它们重做")
    if not all(probe.get(layer, {}).get("maintained") for layer in LAYERS):
        missing = "、".join(LAYER_CN[l] for l in LAYERS if not probe.get(l, {}).get("maintained"))
        return f"知识库缺少{missing}，用例没有可落实的设计分母，请先跑一次测试设计任务"
    targets = _case_targets(ctx)
    if not any(t["batches"] for t in targets):
        return "本次范围内没有任何测试点可作分母（链路/故事/点齐备但点数为 0），用例任务无从开工"
    return ""


def _materialize_case_batches(ctx: Ctx, targets: list[dict]) -> None:
    """批次入账 + 分母清单落盘：清单自带故事上下文，生成侧不必再翻 KB（消费对称性）。

    I-1：三个索引都建在 `_rows_of` 之上，而它已经把同 id 并成写库侧那一份 ⇒ 这里的 dict 推导
    不再可能取到后见的另一块（旧写法是无条件后见覆盖，与写库侧首见即留正相反）。
    """
    story_rows = {str(r.get("id") or ""): r for r in _rows_of(ctx, STORY)}
    point_rows = {str(r.get("id") or ""): r for r in _rows_of(ctx, POINT)}
    chain_rows = {str(r.get("id") or ""): r for r in _rows_of(ctx, CHAIN)}
    batches: list[dict] = []
    for target in targets:
        cid = str(target["chain"])
        for batch in target["batches"] or []:
            items = []
            for pid in batch["points"]:
                row = point_rows.get(pid)
                if row is None:
                    # 分母与点行取自同一份活宇宙（_case_targets 与 point_rows 同源 _rows_of(POINT)）：
                    # 清单里没有这条点行只可能是内部不一致，静默造空名/空场景的行会写假用例。
                    raise _Halt(f"编写环清单缺少点行：{pid}", "needs_input")
                point = dict(row)
                story = story_rows.get(str(point.get("story") or "")) or {}
                items.append({
                    "id": pid, "name": str(point.get("name") or ""),
                    "story": str(point.get("story") or ""),
                    "story_name": str(story.get("name") or ""),
                    "scenario": str(point.get("scenario") or ""),
                    "entities": list(point.get("entities") or []),
                    "directions": list(point.get("directions") or []),
                    "priority": str(point.get("priority") or "P1"),
                    "actor": str(story.get("actor") or ""),
                    "preconditions": str(story.get("preconditions") or ""),
                    "trigger": str(story.get("trigger") or ""),
                    "expected": str(story.get("expected") or ""),
                })
            _write_json(ctx.env.manifests_dir / f"case-{batch['id']}.json",
                        {"chain": cid,
                         "chain_name": str((chain_rows.get(cid) or {}).get("name") or ""),
                         "batch": batch["id"], "points_cap": CASE_BATCH_CAP, "points": items})
            batches.append({"id": batch["id"], "chain": cid, "state": "todo", "round": 0})
    ctx.led.data["writing"]["batches"] = batches


def _case_batch_points(ctx: Ctx, batch: str) -> list[str]:
    for target in ctx.led.data["writing"].get("targets") or []:
        for item in target.get("batches") or []:
            if item.get("id") == batch:
                return [str(p) for p in item.get("points") or []]
    return []


def h_case_plan(ctx: Ctx) -> Any:
    """编写环开账：前置满足 → 物化 targets/batches 并下发首批生成指令；不满足 → 明示边界、零写入、正常收尾。"""
    reason = _case_ready_or_block(ctx)
    led = ctx.led
    if reason:
        # 拒因**追加不覆盖**：mixed 回写后接手时 writing["note"] 已有「设计侧回写完成 @…」留痕，
        # 直接赋值会把这条留痕抹掉，交付物侧就看不见设计侧其实已经回写过了。
        prev = led.data["writing"].get("note")
        led.data["writing"]["note"] = f"{prev}；{reason}" if prev else reason
        led.status = "done"
        return ctx.end("用例任务未开工：" + reason + "。本轮不生成用例、不写知识库。")
    targets = _case_targets(ctx)
    led.data["writing"]["targets"] = targets
    led.data["writing"]["status"] = "active"
    _materialize_case_batches(ctx, targets)
    batches = led.data["writing"]["batches"]
    first = next((b for b in batches if b["state"] == "todo"), None)
    if first is None:                              # 兜底：_case_ready_or_block 已挡，理论不达
        raise _Halt("编写环计划里没有任何可执行批次", "needs_input")
    _go(ctx, "case_gen", layer=first["chain"], block=first["id"])
    # 首批指令与批环重试共用同一取数壳（_case_gen_text：批序/清单路径/应落实点全从账本读）。
    return ctx.instr(_case_gen_text(ctx, first["chain"], first["id"]))


# ---- 第四层编写环：批环（裁定 39：批内自检在评审之前；裁定 36②：批内评审必是子智能体）----

def _cases_file(ctx: Ctx, batch: str) -> Path:
    return ctx.env.cases_dir() / f"{batch}.json"


def _case_manifest_file(ctx: Ctx, batch: str) -> Path:
    """批分母清单的路径单点（与 `_cases_file` 同款缝）：清单文件名只在这里拼一次。"""
    return ctx.env.manifests_dir / f"case-{batch}.json"


def _case_manifest(ctx: Ctx, batch: str) -> dict:
    return _read_json(_case_manifest_file(ctx, batch)) or {}


def _case_batch_entry(ctx: Ctx, chain: str, batch: str) -> dict:
    for entry in ctx.led.data["writing"]["batches"]:
        if entry["id"] == batch:
            return entry
    entry = {"id": batch, "chain": chain, "state": "todo", "round": 0}
    ctx.led.data["writing"]["batches"].append(entry)
    return entry


def _case_gen_text(ctx: Ctx, chain: str, batch: str) -> str:
    """批生成指令的取数单点：批序、清单路径、应落实点全部从账本读，不靠调用方拼。"""
    batches = ctx.led.data["writing"]["batches"]
    idx = next((i for i, b in enumerate(batches) if b["id"] == batch), 0)
    return case_gen_instruction(
        chain=chain, batch=batch,
        manifest_path=ctx.rel(_case_manifest_file(ctx, batch)),
        points=_case_batch_points(ctx, batch), batch_no=idx + 1, batch_total=len(batches))


def _case_batch_errors(ctx: Ctx, chain: str, batch: str) -> list[str]:
    """「这一批正文可不可收」的唯一判定：schema 拒收 → 自报归属核对 → 批内自检 hard。

    三段串成一张错误表，前段非空即原样返回、后段不再跑（与改造前的现形一致）：schema 挡的是坏输入，
    归属不符（裁定 38）时再核覆盖也没意义，而 hard 只在正文可用后才谈。首轮与优化轮共用这一处，
    判定不许在两处分叉——机器可证的漏点比派一次付费复审便宜得多（裁定 39）。
    """
    cases, errors = parse_case_file(_cases_file(ctx, batch))
    if errors:
        return errors
    raw = _read_json(_cases_file(ctx, batch)) or {}
    mismatched: list[str] = []
    if str(raw.get("chain") or "") != chain:
        mismatched.append(f"文件自报链路「{raw.get('chain')}」与本批账本归属「{chain}」不符："
                          "用例必须写在所属链路的批次文件里，根对象 chain 须填该链路 id")
    if str(raw.get("batch") or "") != batch:
        mismatched.append(f"文件自报批次「{raw.get('batch')}」与当前游标批次「{batch}」不符："
                          "文件名与根对象 batch 必须同为该批 id")
    if mismatched:
        return mismatched
    hard = run_case_checks(_case_manifest(ctx, batch).get("points") or [], cases)["hard"]
    return [h["detail"] for h in hard]


def _patch_case_ids(ctx: Ctx, batch: str) -> None:
    """新用例留空 case_id：按账本序补号并就地改写文件（与 `_patch_ids` 同款，不走 TYPE_PREFIX）。"""
    path = _cases_file(ctx, batch)
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return
    changed = False
    for item in raw.get("cases") or []:
        if isinstance(item, dict) and not str(item.get("case_id") or ""):
            item["case_id"] = ctx.led.next_case_seq()
            changed = True
    if changed:
        _write_json(path, raw)


def _case_review_brief(ctx: Ctx, chain: str, batch: str, round_no: int) -> str:
    points = _case_batch_points(ctx, batch)
    open_ops = _w_open_of(ctx, block=batch, source="case_block")
    lines = [
        f"【批评审·用例·链路 {chain}·批次 {batch}·第 {round_no} 轮】",
        f"请只读审阅用例文件 {ctx.rel(_cases_file(ctx, batch))}"
        f"（本批分母清单 {ctx.rel(_case_manifest_file(ctx, batch))}"
        f"，本批应落实点 {('、'.join(points)) or '（无）'}），给出本判决：",
        '{"opinions": [{"target": {"type": "node", "value": "cc-用例 id 或 pt-测试点 id"}, '
        '"kind": "漏测|颗粒度|边界归属|命名漂移|失效", "ask": "怎么改", "evidence": "依据"}], '
        '"resolutions": [{"ref": "op-01", "resolved": true, "note": "为何已消化"}]}',
        "kind 只能取上面列出的五个值之一；target 与 ask 不得省略；"
        "没有意见就两个数组都返回空数组。",
        "看什么：步骤是否可执行、断言是否硬、前置是否自造数据、covers 认领是否属实"
        "（认领了这个点但正文没落实它，同样算漏测）、有没有为凑数写的无用例。",
    ]
    if open_ops:
        lines.append("上一轮尚未销账的意见（逐条给 resolutions 回执）：")
        lines += [f"- {o['ref']}: {o['ask']}" for o in open_ops]
    lines.append("意见要可执行、可核对；不复述用例正文。")
    return "\n".join(lines)


def _after_case_batch(ctx: Ctx) -> None:
    """批次串行推进：下一批（入账顺序即链路顺序，天然「逐链路、链路内逐批」）→ 批齐进末门。"""
    batches = ctx.led.data["writing"]["batches"]
    idx = next((i for i, b in enumerate(batches) if b["id"] == ctx.cur["block"]), len(batches) - 1)
    nxt = next((b for b in batches[idx + 1:] if b["state"] != "done"), None)
    if nxt is None:
        _go(ctx, "case_gate")
        return
    _go(ctx, "case_gen", layer=nxt["chain"], block=nxt["id"])


def _run_case_review(ctx: Ctx, chain: str, batch: str) -> None:
    """批评审一轮（内联驱动，与块评审同纪律）：无在途意见即批收口；有意见交优化环；轮次用尽转归因。"""
    entry = _case_batch_entry(ctx, chain, batch)
    round_no = int(entry["round"])
    call_id = f"case-{batch}-r{round_no}"
    out, raw = run_reviewer(
        ctx.task_tool, CASE_REVIEW_AGENT_ID, _case_review_brief(ctx, chain, batch, round_no),
        model_cls=ReviewOut, call_id=call_id,
        title=f"批评审·用例·{batch}·r{round_no}", config=ctx.config,
        archive=lambda cid, text: _archive_review(ctx, cid, text))
    _archive_review(ctx, call_id, raw)
    _apply_resolutions(ctx, out.resolutions)
    _w_register(ctx, [{"opinion": op, "key": f"{op.target.type}:{op.target.value}:{op.kind}"}
                      for op in out.opinions], block=batch)
    open_ops = _w_open_of(ctx, block=batch, source="case_block")
    if not open_ops:
        _close_case_batch(ctx, entry)
        _after_case_batch(ctx)
        return
    if round_no >= ctx.round_cap():
        _go(ctx, "case_attribute", layer=chain, block=batch, round=round_no, source="case_block")
        return
    _write_in_file(ctx.env.reviews_dir / f"case-{batch}-in-r{round_no}.json", open_ops)
    _go(ctx, "case_opt", layer=chain, block=batch, round=round_no, source="case_block")


def h_case_gen(ctx: Ctx) -> Any:
    """批生成：文件到达 → 批内确定性自检（`_case_batch_errors`）→ 补号 → 批评审 r0。

    自检排在评审之前：漏点是机器可证的，重问比派一次付费评审便宜（裁定 39）；补号也在自检之后，
    免得坏批白烧序号。判定只在 `_case_batch_errors` 一处：schema 拒收、自报归属核对（裁定 38——
    schema 只验过 chain/batch「存在」，而交付表按账本归属、文件头给人看，两处分裂就是交付表与正文
    拆成两张皮）、以及 `run_case_checks` 的批内 hard；不通过一律原地重问，不进补号与评审。
    """
    chain, batch = ctx.cur["layer"], ctx.cur["block"]
    errors = _case_batch_errors(ctx, chain, batch)
    if errors:
        return ctx.ask(_case_gen_text(ctx, chain, batch) + _errors_block(errors), cap=NUDGE_CAP)
    _patch_case_ids(ctx, batch)
    _case_batch_entry(ctx, chain, batch)["state"] = "drafted"
    _run_case_review(ctx, chain, batch)
    return None


def _case_opt_prefix(batch: str, source: str) -> str:
    """在途/处置文件前缀按来源分流（与三层 `_opt_prefix` 同型）：批评审与门后回溯两套文件不得互踩。"""
    return f"human-case-{batch}" if source == "case_human" else f"case-{batch}"


def h_case_opt(ctx: Ctx) -> Any:
    """用例优化：等「更新后的用例文件 + 处置表」→ 校验/销账 → 复审回环（与 `h_opt` 同纪律）。

    优化轮交回的正文与首轮同口径复核（`_case_batch_errors`，机器可证的漏点比派复审便宜，裁定 39）：
    `case_opt_instruction` 授权「改正文、拆条、合并、删掉无用例」，一轮优化就能删掉某点唯一认领、
    或把根归属写歪——自检不过就重问，不补号、不落处置表、不派复审。

    意见簿在 `writing["opinions"]`、未消化项在 `writing["unresolved"]`：字段与三层逐字同形，
    末门与交付物因此只需要换取桶路径（裁定 39 复用纪律）。
    """
    chain, batch = ctx.cur["layer"], ctx.cur["block"]
    round_no, source = int(ctx.cur["round"]), ctx.cur["source"]
    prefix = _case_opt_prefix(batch, source)
    cases_rel = ctx.rel(_cases_file(ctx, batch))
    in_rel = ctx.rel(ctx.env.reviews_dir / f"{prefix}-in-r{round_no}.json")
    fix_rel = ctx.rel(ctx.env.reviews_dir / f"{prefix}-fix-r{round_no}.json")
    text = case_opt_instruction(chain, batch, round_no, cases_path=cases_rel,
                                opinions_path=in_rel, fix_path=fix_rel)
    raw = _read_json(ctx.env.reviews_dir / f"{prefix}-fix-r{round_no}.json")
    all_ops = {op["ref"]: op for op in ctx.led.data["writing"]["opinions"]}
    if raw is None and not ctx.cur.get("asked"):
        return ctx.ask(text, cap=FIX_CAP)           # 首派：等主智能体交回「用例文件 + 处置表」
    errors: list[str] = []
    if not isinstance(raw, dict) or not isinstance(raw.get("dispositions"), list):
        errors.append("处置表缺失或形状不对（须为 JSON 对象且含 dispositions 数组）")
    else:
        for i, row in enumerate(raw["dispositions"]):
            if not isinstance(row, dict) or str(row.get("ref") or "") not in all_ops:
                errors.append(f"dispositions[{i}]: ref 不在意见簿中")
            elif str(row.get("status") or "") not in ("fixed", "covered", "unresolved"):
                errors.append(f"dispositions[{i}]: status 须为 fixed|covered|unresolved")
    if not errors:
        errors = _case_batch_errors(ctx, chain, batch)   # 与首轮同口径的批内确定性自检
    if errors:
        return ctx.ask(text + _errors_block(errors), cap=FIX_CAP)
    _patch_case_ids(ctx, batch)                     # 本轮新增用例补号
    for row in raw["dispositions"]:
        op = all_ops[str(row["ref"])]
        status, note = str(row["status"]), str(row.get("note") or "")
        op["disposition"] = status
        if op["note"] and note:
            # 裁定 28 同款：处置说明不得顶掉既有轨迹，新说明以「处置：」缀在复审回执之后。
            op["note"] = f"{op['note']}；处置：{note}"
        else:
            op["note"] = note or op["note"]
        if status in ("fixed", "covered"):
            op["resolved"] = True                   # 声称已消化；复审再犯即重新出现在途
        else:
            op["escalated"] = True
            ctx.led.data["writing"]["unresolved"].append(
                {"block": batch, "refs": [op["ref"]], "cause": "评审分歧",
                 "note": note or "主智能体判定本轮无法消化"})
    _case_batch_entry(ctx, chain, batch)["round"] = round_no + 1
    _run_case_review(ctx, chain, batch)             # 复审 inline；下一轮 call_id 自然新鲜
    return None


def h_case_attribute(ctx: Ctx) -> Any:
    """用例归因：批轮次用尽仍有在途意见 → 四选一归因 → 批带账收口（不阻塞其他批与末门）。"""
    chain, batch = ctx.cur["layer"], ctx.cur["block"]
    round_no = int(ctx.cur["round"])
    open_ops = _w_open_of(ctx, block=batch, source="case_block")
    open_path = ctx.env.attribution_dir / f"open-case-{batch}-r{round_no}.json"
    out_path = ctx.env.attribution_dir / f"attr-case-{batch}-r{round_no}.json"
    text = case_attribute_instruction(batch, opinions_path=ctx.rel(open_path),
                                      out_path=ctx.rel(out_path))
    raw = _read_json(out_path)
    if raw is None:
        if not open_path.is_file():
            _write_in_file(open_path, open_ops)
        return ctx.ask(text, cap=FIX_CAP)
    cause, note = str(raw.get("cause") or ""), str(raw.get("note") or "").strip()
    if cause not in _CAUSES or not note:
        return ctx.ask(text + _errors_block([f"cause 须为 {'|'.join(_CAUSES)} 之一且 note 非空"]),
                       cap=FIX_CAP)
    ctx.led.data["writing"]["unresolved"].append(
        {"block": batch, "refs": [o["ref"] for o in open_ops], "cause": cause, "note": note})
    for op in open_ops:
        op["escalated"] = True                      # 带账离开在途集：不再触发优化环
    _close_case_batch(ctx, _case_batch_entry(ctx, chain, batch))
    _after_case_batch(ctx)
    return None


# ---- 第四层编写环：末门（裁定 36/37：全片最后一道门；批准零 KB 写）----

def _close_case_batch(ctx: Ctx, entry: dict) -> None:
    """批收口单点：置 done 的同时从「失效待重算」除名——交付物不许列已经重算完的批。

    两个收口出口（复审干净 / 归因带账离场）共用它，口径必须一致：归因离场的批同样不该再挂在
    「待重算」上，否则交付物的「失效待重算批次」段永远在列旧账（裁定 36③ 的反面）。
    """
    entry["state"] = "done"
    writing = ctx.led.data["writing"]
    writing["stale_batches"] = [b for b in writing["stale_batches"] if b != entry["id"]]


def _chain_of_batch(ledger_data: dict, batch: str) -> str:
    """批次 → 链路：交付物的意见落点对照表要按链路呈递，簿本里只存批 id。"""
    for entry in ledger_data["writing"].get("batches") or []:
        if str(entry.get("id") or "") == batch:
            return str(entry.get("chain") or "")
    return ""


def _batch_of_target(ctx: Ctx, value: str) -> str:
    """门后回溯的意见落在哪一批：cc- 查正文归属，pt- 查分母清单，ch- 取该链路首批；其余指向不明。

    返回空串 = 归不了批（人指的是设计侧的东西或没给 id）。调用方绝不许把它塞进随便一个批的
    优化环——那会让「这条意见去哪了」在交付物上撒谎。

    pt- 的取舍（T22 评审 M-4，控制方裁定保持现状）：一点挂两链时按 targets 入账顺序取**第一条
    链路**的批——该点确在那批分母内，归属为真、确定、可解释；返回 "" 看着更保守，却会把一条真能
    落地的意见降级成转述，比现状差。人的本意若在另一条链，由那一轮的交付物与下一句意见纠偏。
    """
    value = str(value or "")
    targets = ctx.led.data["writing"].get("targets") or []
    if value.startswith(CASE_PREFIX + "-"):
        for target in targets:
            for item in target.get("batches") or []:
                bid = str(item.get("id") or "")
                cases, errors = parse_case_file(_cases_file(ctx, bid))
                if not errors and any(str(c.case_id) == value for c in cases):
                    return bid
        return ""
    layer = _layer_of_id(value)
    if layer == POINT:
        for target in targets:
            for item in target.get("batches") or []:
                if value in [str(p) for p in item.get("points") or []]:
                    return str(item.get("id") or "")
        return ""
    if layer == CHAIN:
        for target in targets:
            if str(target.get("chain") or "") == value and (target.get("batches") or []):
                return str(target["batches"][0].get("id") or "")
    return ""


def _case_gate_report(ctx: Ctx) -> dict:
    """末门确定性核对单点：逐链路把「本 run 的正文」与「分母清单」摆到一起核一次。

    三条口径都是 fail-closed：
    - 批次正文读不出来 / 形状不对 ⇒ 记 `broken` 并**照常计入该批分母点**（零用例=全漏测），
      绝不当「无问题」放行；
    - `uncovered` 走全局去重集合，一点挂两链时计数仍是一次（与 `compose_case_delivery` 的实测行同源）；
    - hard 条目自带 `chain`/`batch`，修复指令与挂具都按它定位，不再二次反查。

    单一事实源（C-22a）：`checks_by_chain`、`hard`、`uncovered` 全出自这同一次逐链路
    `run_case_checks`——交付物的表格与计数、门的重核、修复清单因此不可能互相打脸。
    """
    checks_by_chain: dict[str, dict] = {}
    cases_by_chain: dict[str, list] = {}
    hard: list[dict] = []
    broken: list[dict] = []
    uncovered: set[str] = set()
    for target in ctx.led.data["writing"].get("targets") or []:
        chain = str(target.get("chain") or "")
        points: list[dict] = []
        cases: list = []
        batch_of_point: dict[str, str] = {}
        batch_of_case: dict[str, str] = {}
        for item in target.get("batches") or []:
            bid = str(item.get("id") or "")
            listed = _case_manifest(ctx, bid).get("points") or []
            # I-2：分母的事实源是账本，盘上清单只是给生成侧看的副本。两者对不上就是内部不一致，
            # 按同文件 `_materialize_case_batches` 缺点行的先例响亮中止——「清单少一个点、又没人
            # 认领它」在旧写法下会退化成未落实点 0 直接判绿（离线用例复现过）。
            on_ledger = _case_batch_points(ctx, bid)
            if sorted(str(p.get("id") or "") for p in listed) != sorted(on_ledger):
                raise _Halt(
                    f"编写环清单与账本分母不一致：{bid}："
                    f"清单 {sorted(str(p.get('id') or '') for p in listed)} "
                    f"／ 账本 {sorted(on_ledger)}（分母以账本为准，请重跑编写环计划）", "needs_input")
            for point in listed:
                points.append(point)
                batch_of_point[str(point.get("id") or "")] = bid
            file_cases, errors = parse_case_file(_cases_file(ctx, bid))
            if errors:
                broken.append({"batch": bid, "chain": chain, "errors": errors})
                continue
            for case in file_cases:
                cases.append(case)
                batch_of_case[str(case.case_id)] = bid
        checks = run_case_checks(points, cases)
        checks_by_chain[chain] = checks
        cases_by_chain[chain] = cases
        for entry in checks["hard"]:
            where = str(entry.get("where") or "")
            batch = (batch_of_point.get(where) if entry.get("code") == "uncovered_point"
                     else batch_of_case.get(where) or "")
            hard.append({**entry, "chain": chain, "batch": str(batch or "")})
            if entry.get("code") == "uncovered_point":
                uncovered.add(where)
    return {"checks_by_chain": checks_by_chain, "cases_by_chain": cases_by_chain,
            "hard": hard, "uncovered": sorted(uncovered), "broken": broken}


def _patch_batches_case_ids(ctx: Ctx) -> None:
    """末门进门补号：修复指令明说新增用例的 case_id 留空串、由编排层分配，而批环两个补号点
    （`h_case_gen`/`h_case_opt`）都不在末门修复环的路径上。不补号，交付物的「用例清单」按
    `case_id` 过滤会把新用例整条隐藏、「落实于」渲成空串，且空 id 用例在归属表里互相覆写。
    坏批与非 dict 正文由 `_patch_case_ids` 自己早退（它读不到 dict 就返回），此处不重复判。"""
    for entry in ctx.led.data["writing"]["batches"]:
        batch = str(entry.get("id") or "")
        if batch:
            _patch_case_ids(ctx, batch)


def _op_status_cn(op: dict) -> str:
    if op.get("resolved"):
        return "已销账"
    if op.get("escalated"):
        return "未消化"
    return "在途"


def _write_case_delivery(ctx: Ctx, report: dict) -> None:
    """交付物落盘：段落顺序与计数全在 `compose_case_delivery`，这里只负责把账本事实喂给它。

    这是「人在门上看到的东西」的唯一产地，主智能体无从在这里改口径——裁定 36 的实测计数必须
    出自代码而不是出自模型。`chain`/`ask` 账本不存：按批 id 反查 targets、按 ref 回意见簿取。
    """
    led = ctx.led
    writing = led.data["writing"]
    notes = [{"where": f"[{chain}] {n['where']}", "kind": n["kind"], "detail": n["detail"]}
             for chain, checks in report["checks_by_chain"].items()
             for n in checks["report"]["notes"]]
    dispositions = [{"chain": _chain_of_batch(led.data, str(o.get("block") or "")),
                     "ref": o["ref"], "kind": o["kind"],
                     "case": str((o.get("target") or {}).get("value") or ""),
                     "ask": o["ask"], "status": _op_status_cn(o), "note": o.get("note") or ""}
                    for o in writing["opinions"]]
    unresolved = []
    for row in writing["unresolved"]:
        block = str(row.get("block") or "")
        for ref in row.get("refs") or []:
            op = next((o for o in writing["opinions"] if o["ref"] == ref), {})
            unresolved.append({"chain": _chain_of_batch(led.data, block), "ref": ref,
                               "ask": op.get("ask", ""), "cause": str(row.get("cause") or ""),
                               "note": str(row.get("note") or "")})
    ctx.env.delivery_path.write_text(
        compose_case_delivery(led.data, writing.get("targets") or [],
                              report["checks_by_chain"],
                              {"uncovered": report["uncovered"], "notes": notes,
                               "dispositions": dispositions, "unresolved": unresolved,
                               "cases_by_chain": report["cases_by_chain"]}),
        encoding="utf-8")


_CASE_GATE_UNDECIDED = (
    "【用例末门·待决】上面这条人审消息既没有可执行的意见（指向 cc- 用例 / pt- 测试点 / ch- 链路），"
    "也没有明示批准（通过/批准/同意/确认）。本轮不放行、不放回批环，批次状态与游标都不动；"
    "交付物 design/case-delivery.md 已由编排层按当前实测刷新。请把上述状态转述给人并等待其明确答复，"
    "不要代替人给出批准。本轮只输出一条面向人的答复，不要改动任何文件。"
)
_CASE_GATE_UNRESOLVED = (
    "【用例末门·意见指向不明】人审给了内容，但没有落到具体批次：target 的 value 必须是交付物里"
    "真实存在的 cc- 用例 id、pt- 测试点 id 或 ch- 链路 id（seam/树外遗漏属设计侧，请到测试设计任务里提）。"
    "请向人确认指向后再说一次；本轮批次状态与游标都不动，交付物已由编排层按当前实测刷新，"
    "你不要改动任何文件。"
)


def _case_gate_refusal(report: dict) -> str:
    """拒绝放行的面向人文案：逐条摆机器账，让人知道「批了但没过」到底是哪几条点没落实。"""
    lines = ["【用例末门·不予放行】本轮人话是明示批准，但末门的机器账没有清零——"
             "批准不能代替实测计数，批次状态与游标都不动、零放行，"
             "交付物已由编排层按当前实测刷新："]
    cap = 20                                     # 单类最多列 20 条，超出必须报总数（评审 Minor 10）
    hard, broken = report["hard"], report["broken"]
    lines += [f"- {h['detail']}" for h in hard[:cap]]
    if len(hard) > cap:
        lines.append(f"（未落实/假完整项共 {len(hard)} 条，此处只列前 {cap} 条。）")
    lines += [f"- 批次 {b['batch']} 正文不可解析：{b['errors'][0]}" for b in broken[:cap]]
    if len(broken) > cap:
        lines.append(f"（坏批共 {len(broken)} 个，此处只列前 {cap} 个。）")
    lines.append("要改：说一句带 cc-/pt-/ch- id 的意见即可退回批环重做；要放行：把正文修好后重新批准。"
                 "本轮只输出一条面向人的答复，不要改动任何文件。")
    return "\n".join(lines)


def h_case_gate(ctx: Ctx) -> Any:
    """用例末门：待决不重呈 → 每次进门先补号再重渲染实测 → hard/坏批清零（修复环 ≤round_cap）→ 呈递人审。"""
    led, gate = ctx.led, ctx.led.data["writing"]["gate"]
    if led.status == "awaiting_review":
        # B-F4（比三层更严）：人正在等——待决转述轮、或被机器账拒绝放行的那一轮，游标都还停在
        # case_gate。此处零重写、零轮次、零终帧：人本轮的话还没被答复，绝不许再递一遍交付物。
        # 三层用「hard 为空」当守卫条件，是因为它呈递后宇宙不再变；末门的批准拒绝路径会让
        # 「awaiting_review + hard 非零」成为合法现场，条件必须整个去掉。
        return ctx.turn([], "end")
    _patch_batches_case_ids(ctx)                   # 修复环交回的空 id 用例先补号，报告才认得它们
    report = _case_gate_report(ctx)
    _write_case_delivery(ctx, report)                 # 文件永远对得上当前实测，即使本轮不呈递
    if report["hard"] or report["broken"]:
        if int(gate["round"]) >= ctx.round_cap():
            raise _Halt("用例末门履约检查连续未清零（修复环用尽）", "needs_input")
        gate["round"] = int(gate["round"]) + 1
        issues_path = ctx.env.reviews_dir / f"case-gate-issues-r{gate['round']}.json"
        _write_json(issues_path, {"round": gate["round"], "hard": report["hard"],
                                  "broken": report["broken"]})
        # 预算单点同三层 B-F3：这条环的预算就是 gate["round"]/round_cap，ask 的 cap 显式传
        # round_cap，否则 NUDGE_CAP=3 会抢在「修复环用尽」之前把任务收进「重试超限」。
        return ctx.ask(case_gate_fix_instruction(issues_path=ctx.rel(issues_path),
                                                 round_no=gate["round"]),
                       cap=ctx.round_cap())
    _go(ctx, "case_gate")
    led.status = "awaiting_review"
    ctx.led.data["writing"]["status"] = "awaiting_review"
    return ctx.end("用例交付物已生成（design/case-delivery.md），等待人工评审。")


def h_case_gate_interpret(ctx: Ctx) -> Any:
    """末门人审续步：解读人话 → 批准还要过机器账 → 意见按目标批回环，其余批标 stale 不静默丢。

    每一条**答复人**的分支（拒绝放行 / 批准完成 / 待决 / 指向不明）都在答复前把交付物按当前
    实测重渲染一次——唯一人审门上的读物不许说谎（裁定 25/36①）。批准与拒绝复用同一份已算好的
    `report`，不为渲染再跑第二次核对（C-22a）；只有意见回环不渲染（它不回交付物，下一轮
    `h_case_gate` 自然重渲染），B-F4 守卫轮照旧零写、零帧、零计数推进。
    """
    led, writing = ctx.led, ctx.led.data["writing"]
    gate, human_text = writing["gate"], _human_text(ctx.state_messages)
    k = int(gate.get("int_round") or 0) + 1
    gate["int_round"] = k
    brief = "\n".join([
        "【人审解读·用例交付门】人审是最权威的评审。把人审原话转成结构化意见"
        "（纯批准或没有要改的内容 → opinions 留空）：",
        "人审原文：",
        human_text or "（空）",
        f"交付物 {ctx.rel(ctx.env.delivery_path)} 与用例正文所在目录 {ctx.rel(ctx.env.cases_dir())}/ "
        f"可只读核对；证据栏填人审原话。",
        '{"opinions": [{"target": {"type": "node|seam|outside", '
        '"value": "cc-用例 id / pt-测试点 id / ch-链路 id"}, '
        '"kind": "漏测|颗粒度|边界归属|命名漂移|失效", "ask": "怎么改", "evidence": "人审原话"}], '
        '"resolutions": []}',
        "target.type 只能取上面三个值之一；kind 只能取上面五个值之一；"
        "指向某条用例时 type 用 node、value 填 cc- 四位数字 id（交付物的落点对照表里有）；"
        "用例正文不在知识库，指向设计层（st- 故事）的意见不属于本门。没有意见时两个数组都返回空数组。",
        "本门不做销账：resolutions 一律留空数组（回执由批评审逐轮给）。",
    ])
    call_id = f"case-gate-int-r{k}"                # 与大纲门 gate-int-rN 分开：同一 run 里两道门
    out, raw = run_reviewer(ctx.task_tool, CASE_REVIEW_AGENT_ID, brief, model_cls=ReviewOut,
                            call_id=call_id, title=f"用例门解读·r{k}", config=ctx.config,
                            archive=lambda cid, text: _archive_review(ctx, cid, text))
    _archive_review(ctx, call_id, raw)
    if out.opinions:
        gate["unclear"] = 0
    elif _explicit_approval(human_text):
        _patch_batches_case_ids(ctx)                # 门后人改正文新增的空 id 用例，重核前先补号
        report = _case_gate_report(ctx)            # 批准不豁免机器账：hard 非零就是不能放行
        if report["hard"] or report["broken"]:
            # 拒绝放行是**有决定**的轮次，不占待决计数：每轮都要人重新说一次，成本由人控制。
            led.status = "awaiting_review"
            _go(ctx, "case_gate")
            # 文案摆的是当前未落实点，读物必须同步刷新成同一份实测——复用刚算好的 report，
            # 不为渲染再跑一次核对（C-22a：覆盖真相只出自一次 `run_case_checks`）。
            _write_case_delivery(ctx, report)
            return ctx.ask(_case_gate_refusal(report))
        gate["approved_at"] = _now()
        gate["unclear"] = 0
        writing["status"] = "done"
        led.status = "done"
        # 裁定 35：用例正文不进知识库——批准只是人对交付物的确认，本门零 KB 写、零回写授权。
        _write_case_delivery(ctx, report)          # 签字那一瞬的纸必须对得上被批准的正文
        return ctx.end("用例交付确认完成：用例正文只落项目空间 design/cases/，本次零知识库写入。")
    else:
        gate["unclear"] = int(gate.get("unclear") or 0) + 1
        if gate["unclear"] > NUDGE_CAP:            # 连续待决：绝不猜批准
            raise _Halt("用例交付门连续未给出可执行意见也未明示批准", "human_wait")
        led.status = "awaiting_review"
        _go(ctx, "case_gate")
        _patch_batches_case_ids(ctx)               # 待决轮没有 report：补号后自取一次（纯本地零付费）
        _write_case_delivery(ctx, _case_gate_report(ctx))
        return ctx.ask(_CASE_GATE_UNDECIDED)

    # 门后回溯（裁定 36③）：先按目标批分组登记，一条也不许静默丢；登记用 source="case_human"，
    # 与批评审的 case_block 两本分开，复审回执才认得出谁提的。
    by_batch: dict[str, list] = {}
    for op in out.opinions:
        value = str(op.target.value or "")
        by_batch.setdefault(_batch_of_target(ctx, value) if op.target.type == "node"
                            else "", []).append(op)
    for batch, ops in by_batch.items():
        _w_register(ctx, [{"opinion": op,
                           "key": f"{op.target.type}:{op.target.value}:{op.kind}"} for op in ops],
                    block=batch, source="case_human")
    batches = writing["batches"]
    targeted = [b for b in batches if b.get("id") and by_batch.get(str(b["id"]))]
    if not targeted:                               # 全是指向不明：只转述，一个批状态都不动
        led.status = "awaiting_review"
        _go(ctx, "case_gate")
        _patch_batches_case_ids(ctx)               # 与待决同款：补号后自取一次报告，只写文件不动账
        _write_case_delivery(ctx, _case_gate_report(ctx))
        return ctx.ask(_CASE_GATE_UNRESOLVED)
    first = targeted[0]                            # 入账顺序即链路顺序：最早的受害批先重做
    round_no = int(first["round"])
    _write_in_file(ctx.env.reviews_dir / f"{_case_opt_prefix(first['id'], 'case_human')}"
                   f"-in-r{round_no}.json",
                   _w_open_of(ctx, block=first["id"], source="case_human"))
    writing["status"] = "active"
    first["state"] = "drafted"                     # 正文还在：改它，不是重烧整条链路
    for entry in batches:
        if entry is first or str(entry.get("state")) != "done":
            continue
        entry["state"] = "stale"
        # 重算批的评审 call_id 必须新鲜：真实 TaskTool 按 call_id 复用已完结摘要，
        # 同 id 重审等于把上一轮的判决原样递回（走查二在三层踩过同一条缝）。
        entry["round"] = int(entry.get("round") or 0) + 1
        if entry["id"] not in writing["stale_batches"]:
            writing["stale_batches"].append(entry["id"])
    _go(ctx, "case_opt", layer=first["chain"], block=first["id"], round=round_no,
        source="case_human")
    return None


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
    if descriptor["task_kind"] == "case_only":
        # case_only：三层全 skipped —— 三层内容只作只读上下文经 kb_rows 使用，本 run 不写它们。
        modes = {layer: "skipped" for layer in LAYERS}
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
        if _writing_enabled(ctx):
            # 裁定 40：case_only 的三层一律 skipped（只读上下文），空窗不是故障而是交接点。
            _go(ctx, "case_plan")
            return None
        raise _Halt("计划窗口内没有任何需要生成的层", "needs_input")
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
    if _is_no_change(raw):
        if str(led.layer(layer)["mode"]) != "update":
            return ctx.ask(_gen_text(ctx, layer, block, errors=[
                "首建模式不接受无变化块：本层知识库无维护，nodes 为空即漏做——"
                "请按业务信息产出本块节点"]))
        led.data.setdefault("no_change", []).append({
            "layer": layer, "block": block,
            # 折成单行：reason 直接进大纲成行，含换行就能伪造额外大纲行。
            "reason": " ".join(str(raw.get("reason") or "").split())})
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


def _register_ops(ctx: Ctx, ops: list[dict], entries: list[dict], *, source: str,
                  block: str) -> None:
    """登记意见（簿本无关内核）：同（source,key）仍有在途条目则复用其 ref，否则分配新 ref。

    复用而不是新增，是为了让「主智能体声称已改、评审子若再犯」表现为同一 ref 重新出现
    （销账后被再发即为新一轮条目），而不是同一条意见无限复制。
    """
    for entry in entries:
        op, key = entry["opinion"], entry["key"]
        if any(o["source"] == source and o["key"] == key and not _settled(o) for o in ops):
            continue
        ops.append({
            "ref": _next_ref(ctx), "key": key, "source": source, "block": block,
            "target": op.target.model_dump(), "kind": op.kind, "ask": op.ask,
            "evidence": op.evidence, "resolved": False, "escalated": False,
            "disposition": "", "note": "",
        })


def _register(ctx: Ctx, layer: str, entries: list[dict], *, source: str, block: str) -> None:
    """三层意见簿：登记到 `layers.<layer>.opinions`。"""
    _register_ops(ctx, ctx.led.layer(layer)["opinions"], entries, source=source, block=block)


def _w_register(ctx: Ctx, entries: list[dict], *, block: str,
                source: str = "case_block") -> None:
    """第四层意见簿与三层同名键同形（裁定 39 复用纪律）：只换桶与 source，不换字段。"""
    _register_ops(ctx, ctx.led.data["writing"]["opinions"], entries,
                  source=source, block=block)


def _w_open_of(ctx: Ctx, *, block: str, source: str | None = None) -> list[dict]:
    """编写环在途意见：调用点一律显式带 `source`（与三层 `_open_of` 同纪律）。

    同一 `block` 里可能同时躺着批评审（`case_block`）与门后回溯（`case_human`）两来源的意见，
    不按来源过滤就会串味：人类意见被喂给评审子、批被人类意见卡在「仍有在途」、归因把人类意见
    一并 `escalated`。三层侧靠 source 过滤在构造上免疫，第四层不许把这份免疫拆掉。
    """
    out = []
    for op in ctx.led.data["writing"]["opinions"]:
        if _settled(op) or op["block"] != block:
            continue
        if source is not None and op["source"] != source:
            continue
        out.append(op)
    return out


def _op_buckets(ctx: Ctx) -> list[list[dict]]:
    """意见簿全集：三层各一本 + 编写环一本。ref 全局唯一，所以销账可以跨簿查。"""
    return [ctx.led.layer(layer)["opinions"] for layer in LAYERS] \
        + [ctx.led.data["writing"]["opinions"]]


def _apply_resolutions(ctx: Ctx, resolutions: list) -> None:
    """复审回执销账：ref 全局唯一，跨层查；resolved=False 只更新 note 不销账。

    裁定 25（双文保留）：处置说明是「意见落点对照表」里人要看的东西，复审回执不得
    无条件顶掉它——两处都有时处置说明在前、回执说明以「复审：」缀在后。
    """
    for r in resolutions:
        for ops in _op_buckets(ctx):
            for op in ops:
                if op["ref"] != r.ref:
                    continue
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
        title=f"块评审·{LAYER_CN[layer]}·{block}·r{round_no}", config=ctx.config,
        archive=lambda cid, text: _archive_review(ctx, cid, text))
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
    采信是双条件（R-52）：update 模式 **且** 本 run 的 h_gen 记过账（_no_change_booked）；
    内容像 no_change 但无账 ⇒ 与首建同方向走响亮路径，不许静默跳过。
    """
    blocks = [block] if block else [str(e["id"]) for e in ctx.led.layer(layer)["blocks"]]
    errors: list[str] = []
    update_mode = str(ctx.led.layer(layer)["mode"]) == "update"
    for bid in blocks:
        path = ctx.env.drafts_dir(layer) / f"{bid}.json"
        if update_mode and _is_no_change(_read_json(path)):
            if _no_change_booked(ctx, layer, bid):
                continue
            errors.append(f"{bid}.json: 本块草稿标记为无变化但账本无本轮判定记录："
                          "请重新判定本块（不许静默跳过）")
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
                            title="盲枚举·业务对象", config=ctx.config,
                            archive=lambda cid, text: _archive_review(ctx, cid, text))
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
                            config=ctx.config,
                            archive=lambda cid, text: _archive_review(ctx, cid, text))
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


# ② 声称核对简报的 opinions 条目形状（口径与 _block_review_brief 一致）：走查三真机两次
# 输出被 ClaimsOut 拒收，根因是旧简报只写 "opinions": [] 却只字未提条目形状与 kind 枚举，
# 模型只能自造字段。schema 不放宽，补的是简报——形状必须逐字下发到模型可见文案。
_CLAIMS_SHAPE_OPEN = (
    '"opinions": [{"target": {"type": "node|seam|outside", "value": "节点 id 或 声称 id"}, '
    '"kind": "漏测|颗粒度|边界归属|命名漂移|失效", "ask": "怎么改", "evidence": "依据"}]}\n'
    "opinions 没有就写空数组；kind 只能取上面列出的五个值之一，target 与 ask 不得省略。"
)


def _claims_brief(ctx: Ctx, block: str, rows: list[dict], *, register: bool = True) -> str:
    # F-5：尾句必须与本轮语境一致——回扫不登记任何意见，沿用「会被登记为接缝漏测意见」
    # 就是对模型可见文案说谎；其余文案一字不动。
    tail = ("unclaimed 表示没有任何节点认领这个声称（会被登记为接缝漏测意见）。" if register
            else "本轮回扫只补认结论，不登记意见、不重开任何环。")
    return "\n".join([
        f"【声称核对·块 {block}·第 X 轮】用户故事对「由谁覆盖」有一个或多个声称（assumptions）。"
        "请逐条核对每个声称在树内是否真的被覆盖。",
        "待核对声称（ref 原样回填）：",
        json.dumps(rows, ensure_ascii=False),
        "故事与测试点的存量清单见 design/manifests/kb-story.json 与 design/drafts/。",
        # 裁定 32：register=False（点层回扫）只给空数组字面量，不 advertise 条目形状，
        # 否则回扫会开始产意见。
        '输出一个 JSON：{"claims": [{"ref": "...", "verdict": "covered|unclaimed", '
        '"owner": "覆盖它的故事 id 或空", "note": ""}], '
        + (_CLAIMS_SHAPE_OPEN if register else '"opinions": []}'),
        tail,
    ] + ([] if register else ['opinions 必须留空数组：回扫不产意见。']))


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
                                title=f"声称核对·{block}·r{round_no}", config=ctx.config,
                                archive=lambda cid, text: _archive_review(ctx, cid, text))
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


_CLAIM_ID_RE = re.compile(r"\b(?:ch|st|pt)-\d{4}\b")


def _rescan_claims(ctx: Ctx) -> None:
    """点层收口后回扫 ② 的结论（裁定 32）：被声称方常由点层才落成，故事层无法自证。

    两级、都有界：① 确定性查表——声称文本点名了 `ch-/st-/pt-` id 且该 id 已在
    「KB 存量 ∪ 本 run 三层草稿」落成 ⇒ 直接转 covered，零模型调用；
    ② 残余行**最多一次**复核（复用 ② 的声称核对器，只喂残余行）。
    回扫**不登记意见、不重开任何环**——重开环会把「人已看过的过关层」变成无限回溯；
    翻不动的行原样带进大纲，empty_seam 仍只是呈递项，交人裁决。

    R-46：点层收口有两条合法路径（归因收口/过审收口），且大纲门退回→重做后可再次收口。
    确定性查表可随时重跑（零调用）；reviewer 分支以 `claims_rescan` 是否已落账做门控——
    只要某次调用见到残余行，返回前必写 st["claims_rescan"]，此后本 run 一切再入都跳过
    复核分支 ⇒ 每 run 至多一次复核 pass（内含至多一次重试派发，残余行原样带进大纲）。
    """
    st = ctx.led.layer(STORY)
    rows = st.get("claims") or []
    residual = [r for r in rows if str(r.get("verdict") or "") == "unclaimed"]
    if not residual:
        return
    ids: set[str] = set()
    for layer in LAYERS:
        ids |= {str(r.get("id") or "") for r in ctx.kb_rows(layer) if r.get("id")}
        ids |= {str(n.get("id") or "") for n in _draft_nodes(ctx, layer) if n.get("id")}
    still: list[dict] = []
    for row in residual:
        hits = [m.group(0) for m in _CLAIM_ID_RE.finditer(str(row.get("claim") or ""))
                if m.group(0) in ids]
        if hits:
            row["verdict"] = "covered"
            row["owner"] = hits[0]
            row["rescanned"] = "deterministic"
            row["note"] = (str(row.get("note") or "") +
                           f"｜回扫：{hits[0]} 已落成节点").lstrip("｜")
        else:
            still.append(row)
    if still and "claims_rescan" not in st:        # 复核每 run 至多一次（R-46）
        call_id = f"claims-rescan-r{int(ctx.led.layer(POINT).get('audit_round') or 0)}"
        out, raw = run_reviewer(ctx.task_tool, CASE_REVIEW_AGENT_ID,
                                _claims_brief(ctx, "回扫", still, register=False),
                                model_cls=ClaimsOut,
                                call_id=call_id, title="声称核对·点层回扫", config=ctx.config,
                                archive=lambda cid, text: _archive_review(ctx, cid, text))
        _archive_review(ctx, call_id, raw)
        by_ref = {str(r.get("ref") or ""): r for r in out.claims if isinstance(r, dict)}
        for row in still:
            verdict = str((by_ref.get(str(row.get("ref") or "")) or {}).get("verdict") or "")
            if verdict == "covered":
                row["verdict"] = "covered"
                row["owner"] = str((by_ref.get(str(row.get("ref") or "")) or {})
                                   .get("owner") or row.get("owner") or "")
                row["rescanned"] = "reviewer"
    # 计数口径 =「本张表里被回扫补认的行数」：写账前按当前 claims 行现算，与紧邻其上的逐行标记对账
    st["claims_rescan"] = {
        "deterministic": sum(1 for r in rows if r.get("rescanned") == "deterministic"),
        "reviewer": sum(1 for r in rows if r.get("rescanned") == "reviewer"),
        "at": _now(),
    }


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
                                title=f"矩阵复核·{cid}·r{round_no}", config=ctx.config,
                                archive=lambda cid2, text: _archive_review(ctx, cid2, text))
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
    if layer == POINT:
        _rescan_claims(ctx)        # 被声称方到这一层才可能落成；gate 重入不重花钱（裁定 33）
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
    # mixed 的设计侧仍走大纲门（三层质量内核不因带用例而缩）；编写环在回写成功后接手，见 h_writeback。
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
    # F-2（R-51）：回扫补认后的旧「声称未认领」归因项——不销账、不重开（裁定 32），
    # 只在呈递侧交叉标注。映射在这份 extras 里现算（不改账本、不新增账本键）：
    # 故事层 opinions 中 key 以 claims: 开头、且对应 claims 行现在 verdict==covered 的
    # op ref → owner；精确 join（op["key"] == f"claims:{row['ref']}"），不拿 ask 文本模糊匹配。
    claims_rows = layers[STORY].get("claims") or []
    rescan_owner_by_op: dict[str, str] = {}
    for op in layers[STORY]["opinions"]:
        key = str(op.get("key") or "")
        if not key.startswith("claims:"):
            continue
        for row in claims_rows:
            if key == f"claims:{row.get('ref')}" and str(row.get("verdict") or "") == "covered":
                rescan_owner_by_op[str(op["ref"])] = str(row.get("owner") or "")
    unresolved: list[dict] = []
    dispositions: list[dict] = []
    for layer in LAYERS:
        asks = {op["ref"]: op["ask"] for op in layers[layer]["opinions"]}
        for u in layers[layer]["unresolved"]:
            refs = [str(r) for r in (u.get("refs") or [])]
            item = {"layer": layer, "ref": ",".join(refs),
                    "ask": "；".join(asks.get(r, r) for r in refs),
                    "cause": u.get("cause", ""), "note": u.get("note", "")}
            notes = [f"已由回扫补认 owner={rescan_owner_by_op[r]}，此处仍带账供人裁决"
                     for r in refs if r in rescan_owner_by_op]
            if notes:
                item["rescan_note"] = "；".join(notes)
            unresolved.append(item)
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
        "claims_rescan": layers[STORY].get("claims_rescan") or {},
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
            raise _Halt("大纲门结构检查连续未清零（修复环用尽）", "needs_input")
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
# 否定/延后标记：批准措辞**所在分句**出现任一即不算明示批准（W3-2 前扫的是全句）。「不通过」
# 「先别回写」里都含着批准词，字面匹配会把一句拒绝读成授权——而这条路径的失败代价是不可逆的
# KB 写入，方向只能偏保守。
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


def _approval_clause(text: str, words: tuple[str, ...]) -> bool:
    """本轮人话是否构成授权：**任一分句**把批准措辞与否定/延后标记写在一起 ⇒ 整条消息否决。

    W3-2（走查三呈报、R-58 并入本片）要修的是"连坐"：旧写法拿全句扫否定标记，
    「整体看没什么问题，同意通过」里前一分句的「没」把后一分句的明示批准作废。
    收窄到分句之后，C-1（评审实测、控制方复现）暴露出另一半：
    「不通过，同意」旧写法是 False，纯分句写法却是 True——拒绝被读成授权，
    而这条路径的代价是不可逆的 KB 写入。所以判定分两趟，方向只许偏保守：
      第一趟**否决**：只要有一个分句**自己**同时出现批准措辞与否定/延后标记
        （「不通过」「先别回写」「先不批准」），整条消息就不是授权——直接 False。
      第二趟**采信**：否则只要有分句含批准措辞且该分句无否定/延后标记 ⇒ 授权。
    残余风险如实登记，不许后来者当成已消除：拒绝/保留**不带批准措辞**成句时
    （「暂缓，通过」「同意通过，但 st-0002 先不合并」）本函数仍返回 True——
    纯字符串规则分不开「整体看没什么问题」（表态正常）与「先放一放」（表态保留），
    再往上加启发词就是在猜人话。这两句形成本片登记为走查四观察项：
    真机上这类话必须先被评审子提取成 `out.opinions`（`gate_interpret` 的 elif 顺序：
    有意见就进优化环，永不到达批准判定），若提取不到则按本函数放行、由人复核。
    """
    clauses = [c for c in _CLAUSE_SPLIT_RE.split(text or "") if c]
    if any(any(w in c for w in words) and any(m in c for m in _NEGATION_MARKS) for c in clauses):
        return False
    return any(any(w in c for w in words) for c in clauses)


def _explicit_approval(text: str) -> bool:
    """本轮人话是否是明示批准：某个分句表了批准，且那个分句里没有否定/延后标记。"""
    return _approval_clause(text, _APPROVAL_WORDS)


# 授权/重试措辞之外的「杂质」剥离面：标点/空白（判裸授权用），同面兼作 W3-2 的分句面。
_CLAUSE_SPLIT_RE = re.compile(r"[\s，。、！!？?；;：:,.~〜「」『』\"'`（）()]+")


def _writeback_authorized(text: str) -> bool:
    """回写再入授权（R-27）：某个分句表了批准或给了「重试」，且那个分句没有否定/延后标记。

    与 `_explicit_approval` 的差别只多认一个「重试」：那条路是**首次**批准（大纲门），
    措辞必须是批准词；这条路是裁定 19 的续跑支路——人已经在更早的回合过了一次门，
    本轮只需要一个不带否定的重试信号即可把不可逆写接着做完。否定句仍然一律不算授权。
    """
    return _approval_clause(text, _APPROVAL_WORDS + _RETRY_WORDS)


# 重开措辞（裁定 46）：销毁现场是不可逆动作，判据与批准同一条形状——某分句含措辞且该分句无否定/延后标记。
_RESTART_WORDS: tuple[str, ...] = ("重开", "重新开始", "从头开始", "从零开始", "作废",
                                   "放弃这次", "放弃本次", "开新任务", "换新任务")


def _wants_restart(text: str) -> bool:
    """本轮人话是否明示「作废这次工作、开新任务」。未命中一律续跑——方向偏「少销毁」。

    与批准共用 `_approval_clause`（裁定 19／R-27／W3-2 同族）：一条措辞规则只有一个实现处。
    误判面如实登记：「重开」在无关语境里出现会被判成重开（真值表钉住），走查五实测第 ④ 判据。
    """
    return _approval_clause(text, _RESTART_WORDS)


def _is_bare_authorization(text: str) -> bool:
    """本轮人话是否是**裸授权**：剥掉标点与批准/重试措辞后不剩任何内容。

    B-F1：writeback_failed 续跑只有裸授权（「重试」「同意。」这类）才允许跳过解读
    直回写——「同意，把 st-0002 拆成两条」剥完措辞还剩「拆成两条」，意见必须先去
    gate_interpret 登记，绝不许被当成纯授权消费掉再按旧大纲做不可逆回写。
    """
    if not text:
        return False
    rest = _CLAUSE_SPLIT_RE.sub("", text)
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
                            call_id=call_id, title=f"人审解读·r{k}", config=ctx.config,
                            archive=lambda cid, text: _archive_review(ctx, cid, text))
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
            raise _Halt("人审门连续未给出可执行意见也未明示批准", "human_wait")
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
        skip_no_change = str(ctx.led.layer(layer)["mode"]) == "update"
        for path in sorted(ctx.env.drafts_dir(layer).glob("*.json")):
            if skip_no_change and _is_no_change(_read_json(path)):
                # 采信双条件（R-52）：update 模式 **且** 本 run 记过账才零节点零写入；
                # 无账 ⇒ 响亮（DraftUnparseable，不占重试预算——重试必再炸）。
                if _no_change_booked(ctx, layer, path.stem):
                    continue               # 无变化块：零节点零写入（合法终态）
                raise DraftUnparseable(
                    f"{layer}/{path.name} 本块草稿标记为无变化但账本无本轮判定记录："
                    "请重新判定本块（不许静默跳过）")
            nodes, errors = parse_draft_file(layer, path)
            if errors:
                raise DraftUnparseable(f"{layer}/{path.name} 不可解析：{errors[0]}")
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
    written = untouched = 0
    # F-6：确定性坏草稿的事实原文（终帧如实报因用）；瞬时错误仍走原 3 次重试预算。
    draft_error: str | None = None
    # 本轮真下过笔的节点（跨尝试记账）：重试整轮重来时它们必然报 unchanged，
    # 但「未重写」是对人说这一整轮没动过文件——上一尝试写过就不能算未触碰（复审 T14 I-1）。
    rewrote: set[tuple[str, str]] = set()
    for attempt in range(1, WRITEBACK_FIX_CAP + 2):
        try:
            written = untouched = 0                       # 重试整轮重来：跨尝试累加会虚报写入数
            for layer, node_id, payload in _collect_writeback_items(ctx):
                if payload is None:
                    kb.delete_node(layer, node_id)          # 删除没有"等值"可言，原样执行
                    rewrote.add((layer, node_id))
                    written += 1
                else:
                    meta = kb.upsert_node(layer, payload)
                    if meta.get("unchanged") and (layer, node_id) not in rewrote:
                        untouched += 1
                    else:
                        written += 1
                        rewrote.add((layer, node_id))
            ok = True
            break
        except GraphBubbleUp:
            raise                                # 中断/暂停语义原样上抛，不占回写预算
        except DraftUnparseable as exc:          # F-6：确定性坏输入不占重试预算——
            # 重试必再炸；「知识库暂不可写＋回复『重试』」会把人往错方向支。break 出循环、
            # ok 保持 False、终帧保留原始中文事实；writeback_failed 后人仍可在大纲门退回。
            wb["log"].append(f"回写未执行：{exc}")
            draft_error = str(exc)
            break
        except Exception as exc:                 # 单次失败即整轮重来（upsert 幂等、delete 幂等）；
            # KbClient 只包 TimeoutError，KbUnavailableError 等 manager 侧异常在重试预算内
            # 一并收敛（T6 评审 I-1），不许以未处理异常形式掀翻图。
            wb["log"].append(f"第 {attempt} 次回写失败：{exc}")
    if not ok:
        led.status = "writeback_failed"
        if draft_error is not None:
            return ctx.end(f"回写未执行：{draft_error}")
        return ctx.end("回写失败（已自动重试 3 次）：知识库暂不可写；回复「重试」可再次尝试。")
    wb["written"], wb["untouched"] = written, untouched
    for layer in LAYERS:
        if led.layer(layer)["state"] == "audited":
            led.layer(layer)["state"] = "done"
    wb["done"] = True
    if _writing_enabled(ctx) and not led.data["writing"].get("targets"):
        # 裁定 40：mixed 在同一任务内续跑编写环。设计侧不回终帧（回写事实写进 writing["note"]，
        # 末门交付物的「前置判定」段呈递）——done 状态会把工作区归档，若在此收尾用例环就没了。
        led.data["writing"]["note"] = f"设计侧回写完成 @{_now()}"
        _go(ctx, "case_plan")
        return None
    led.status = "done"
    # 等值判定的单点在 step（裁定 30）：这里不作第二次比较，只把「几个节点其实没被触碰」如实呈递。
    tail = f"（{untouched} 个节点内容与库内一致，未重写。）" if untouched else ""
    return ctx.end("回写完成：本次过审节点已写入知识库。" + tail)


_STAGE_HANDLERS: dict[str, Any] = {
    "plan": h_plan, "gen": h_gen, "opt": h_opt, "attribute": h_attribute,
    "audit": h_audit, "gate": h_gate, "gate_interpret": h_gate_interpret,
    "writeback": h_writeback,
    "case_plan": h_case_plan, "case_gen": h_case_gen, "case_opt": h_case_opt,
    "case_attribute": h_case_attribute, "case_gate": h_case_gate,
    "case_gate_interpret": h_case_gate_interpret,
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
        if ctx.halt_refusal:
            return ctx.end(ctx.halt_refusal)            # 零转场、零子调用（裁定 44）
        while ctx.transitions < MAX_TRANSITIONS:
            ctx.transitions += 1
            result = _STAGE_HANDLERS[ctx.cur["stage"]](ctx)
            if isinstance(result, dict):
                return result                      # ctx.end 已发终帧并存盘
            if result is not None:
                ctx.led.save()
                return ctx.turn([result], "agent")
            ctx.led.save()
        raise _Halt(f"单次驱动转场超过上限（{MAX_TRANSITIONS}）", "transient")
    except GraphBubbleUp:
        raise
    except _Halt as exc:
        if ctx.led is not None:
            ctx.led.status = "halted"
            _book_halt(ctx, exc.kind, str(exc))
        return ctx.end(_halt_frame(ctx, f"测试设计任务中止：{exc}"))
    except Exception as exc:                       # A3：其余异常收敛 halted，不炸图
        logger.exception("case_design 驱动失败")
        if ctx.led is not None:
            ctx.led.status = "halted"
            _book_halt(ctx, "transient", f"内部错误：{exc}")
        return ctx.end(_halt_frame(ctx, f"测试设计任务中止（内部错误：{exc}）"))
