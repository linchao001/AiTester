"""制品与判决的数据形状：草稿节点、结构化意见、评审/枚举/对照/矩阵判决。

判据（spec §2）：意见没有等级字段；target 必须表达「节点 / 接缝 / 树外遗漏 / 矩阵空格」
四类落点；点节点 entities 与 directions 必填（③ 矩阵组装前提 + P-6 方向不占点数）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from aitester.case_design.constants import (
    CASE_ID_RE, DIRECTIONS, ID_RE, INTENTS, LAYERS, PRIORITY_RANK, TYPE_PREFIX,
)

_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def parse_json_fence(text: str) -> Any:
    """唯一围栏 JSON 解析：0 个或多个代码块都响亮失败（不允许模型蒙混）。"""
    blocks = _FENCE_RE.findall(text or "")
    if len(blocks) != 1:
        raise ValueError(f"要求恰好一个 ```json 代码块，实得 {len(blocks)} 个")
    return json.loads(blocks[0])


def validate_intent(raw: Any) -> tuple[str, list[str]]:
    """校验 design/intent.json（首触意向门）：只认 task/chat 两个字面量，错误表非空即重问。"""
    if not isinstance(raw, dict):
        return "", ["intent.json 根必须是对象 {intent: ...}"]
    intent = str(raw.get("intent") or "")
    errors: list[str] = []
    if intent not in INTENTS:
        errors.append(f"intent「{intent}」非法（task/chat）")
    return (intent if not errors else ""), errors


def is_draft_row(row: dict) -> bool:
    """这一行是不是**草稿**（写库侧唯一会写的来源）：生产侧 `_outline_nodes` 给 KB 存量行标
    `state=存量`、给草稿标 新增／更新／删除；测试夹具与本仓快照另用 `op`（存量 noop／草稿
    upsert）表达同一区分。两个信号都认——先 `state`，`state` 缺失才落到 `op`。
    delete 草稿在 `_tree_lines` 已被过滤，不进这里。
    R-59：同源判别式只许有一处实现，取舍规则见下方 `dedup_written`（呈递／分母／清单／写库四侧
    共用）；生产侧的行由 `stages._rows_of` 自报来源（草稿行补 `state`），故这里不必再猜。
    """
    state = str(row.get("state") or "")
    if state == "存量":
        return False
    if state:
        return True
    return str(row.get("op") or "noop") == "upsert"


def dedup_written(rows: list[dict]) -> list[dict]:
    """同 id 只留**将被写库的那一份**，位置仍取该 id 首次出现处（树形顺序不漂）。

    W3-3（走查三呈报、R-58 并入本片）：呈递侧的行序是「先 KB 存量、后本 run 草稿」，
    不去重 ⇒ 同一节点在唯一人审门里呈双行（实测 38 行 / 19 唯一 id）。
    取舍必须和写库侧**同一个口径**，否则人看的是后一块、库里进的是前一块：
    `_collect_writeback_items`（stages.py）只遍历草稿、且 `seen` 首见即留 ⇒
    ① 有草稿就不呈存量行（KB 存量永不进写库侧）；
    ② 草稿之间取**先出现**的那一条（按 sorted 文件名序，与写库侧同序）。

    终评 I-1：这条规则此前在四侧各写一遍，其中清单侧（`_materialize_case_batches` 的 dict 推导）
    是无条件后见覆盖 ⇒ 同一个 `pt-` id 被两块点草稿重复表达时，末门按后一块的 `story` 记分母、
    库里进的是前一块。现四侧（呈递 `outline._tree_lines`／分母 `writing.denominator_points`／
    清单 `stages._materialize_case_batches`／写库 `stages._collect_writeback_items`）同走本函数。

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


class DraftNode(BaseModel):
    """一个草稿节点（upsert 或 delete）。新增节点 id 留空由驱动分配。"""

    op: Literal["upsert", "delete"] = "upsert"
    type: Literal["chain", "story", "point"]
    id: str = ""
    name: str = ""
    # chain
    level: int = 0
    parent: str = ""
    business_scope: str = ""
    excluded: str = ""
    # story（chains 多父 = 重复的合法表达，裁定 2）
    chains: list[str] = Field(default_factory=list)
    actor: str = ""
    preconditions: str = ""
    trigger: str = ""
    expected: str = ""
    assumptions: list[str] = Field(default_factory=list)
    # point（P-6：一个场景一个点；方向不占点数）
    story: str = ""
    scenario: str = ""
    entities: list[str] = Field(default_factory=list)
    directions: list[str] = Field(default_factory=list)
    # 共用
    priority: str = "P1"
    reason: str = ""                       # delete 的理由（必填）

    def is_delete(self) -> bool:
        return self.op == "delete"


class OpinionTarget(BaseModel):
    type: Literal["node", "seam", "outside", "matrix_cell"]
    value: str = ""                        # node: 节点 id；seam: "a,b"；outside: 空；matrix_cell: "实体,故事id"


class Opinion(BaseModel):
    target: OpinionTarget
    kind: Literal["漏测", "颗粒度", "边界归属", "命名漂移", "失效"]
    ask: str
    evidence: str = ""


class Resolution(BaseModel):
    ref: str
    resolved: bool
    note: str = ""


class ReviewOut(BaseModel):
    """块审 / 全局审（①②）判决。复审轮用 resolutions 逐条回执上一轮意见。"""

    opinions: list[Opinion] = Field(default_factory=list)
    resolutions: list[Resolution] = Field(default_factory=list)


class EnumeratorOut(BaseModel):
    """盲枚举器判决：业务对象 / 角色 / 阶段 三类清单。"""

    items: list[dict[str, str]] = Field(default_factory=list)   # {"kind","name","evidence"}


class CompareOut(BaseModel):
    """① 对照器判决：逐条给落点；landing 为空 = 树外遗漏。"""

    items: list[dict[str, str]] = Field(default_factory=list)   # {"name","kind","landing","note"}


class ClaimRow(BaseModel):
    ref: str
    claimant: str
    claim: str


class ClaimsOut(BaseModel):
    """② 声称核对判决。"""

    claims: list[dict[str, Any]] = Field(default_factory=list)  # {"ref","verdict","owner","note"}
    opinions: list[Opinion] = Field(default_factory=list)


class MatrixOut(BaseModel):
    """③ 矩阵空格判决：判 not_needed 必须写 reason，无理由空格 = 不通过。"""

    cells: list[dict[str, str]] = Field(default_factory=list)   # {"entity","story","verdict","reason"}


def _check_common(node: DraftNode, errors: list[str], where: str, seen_ids: set[str]) -> None:
    if node.op == "upsert":
        if not node.name.strip():
            errors.append(f"{where}: name 不能为空")
        if node.id and not ID_RE.match(node.id):
            errors.append(f"{where}: id「{node.id}」形状非法（应为 {TYPE_PREFIX[node.type]}-四位数字）")
        # I-3：priority 合法性三层同源校验（此前只点层有，chain/story 非法值静默通过，
        # 进门后被 `PRIORITY_RANK.get(..., 1)` 当 P1 参与 hard 判据）。DraftNode.priority 缺省
        # "P1"，故「不写」仍合法。
        if node.priority not in PRIORITY_RANK:
            errors.append(f"{where}: priority「{node.priority}」非法（P0/P1/P2）")
    else:
        if not node.id:
            errors.append(f"{where}: delete 必须带 id")
        elif not ID_RE.match(node.id):
            errors.append(f"{where}: id「{node.id}」形状非法")
        if not node.reason.strip():
            errors.append(f"{where}: delete 必须带 reason")
    # A-M4：同一草稿文件内 id 不得重复（delete 与 upsert 同表）——否则宇宙侧后者覆盖、
    # 大纲侧首条呈递，同一破数据两种静默读法。新增节点 id 留空（驱动后分配）不计入。
    # 跨文件同 id 属 R-29，不在本处处理。
    if node.id:
        if node.id in seen_ids:
            errors.append(f"{where}: id「{node.id}」在本文件内重复（同一草稿文件不得有同名 id）")
        else:
            seen_ids.add(node.id)


def _check_chain(node: DraftNode, errors: list[str], where: str) -> None:
    if node.op != "upsert":
        return
    if node.level < 1:
        errors.append(f"{where}: chain.level 必须 ≥1")
    if not node.business_scope.strip():
        errors.append(f"{where}: chain 必须写 business_scope")
    if node.level > 1 and not node.parent:
        errors.append(f"{where}: level>{1} 的 chain 必须带 parent")


def _check_story(node: DraftNode, errors: list[str], where: str) -> None:
    if node.op != "upsert":
        return
    if not node.chains:
        errors.append(f"{where}: story 必须带 chains（≥1）")
    for field in ("actor", "trigger", "expected"):
        if not str(getattr(node, field)).strip():
            errors.append(f"{where}: story 必须写 {field}")


def _check_point(node: DraftNode, errors: list[str], where: str) -> None:
    if node.op != "upsert":
        return
    if not node.story:
        errors.append(f"{where}: point 必须带 story")
    if not node.scenario.strip():
        errors.append(f"{where}: point 必须写 scenario")
    if not node.entities:
        errors.append(f"{where}: point 必须带 entities（③ 矩阵组装前提）")
    if not node.directions:
        errors.append(f"{where}: point 必须带 directions（正向/负向/边界，不许空）")
    bad = [d for d in node.directions if d not in DIRECTIONS]
    if bad:
        errors.append(f"{where}: directions 含非法值 {bad}（只许 {list(DIRECTIONS)}）")


_CHECKS = {"chain": _check_chain, "story": _check_story, "point": _check_point}


def validate_drafts(layer: str, raw: Any) -> tuple[list[DraftNode], list[str]]:
    """校验一个块草稿文件：形状 + 层字段 + 交叉字段。返回 (节点表, 错误表)。"""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return [], ["草稿根必须是对象 {layer, block, nodes}"]
    if raw.get("layer") != layer:
        errors.append(f"layer 字段应为「{layer}」")
    if not isinstance(raw.get("block"), str):
        errors.append("block 字段缺失")
    raws = raw.get("nodes")
    if not isinstance(raws, list) or not raws:
        return [], [*errors, "nodes 必须是非空数组"]
    nodes: list[DraftNode] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raws):
        where = f"nodes[{i}]"
        try:
            node = DraftNode.model_validate(item)
        except Exception as exc:                       # pydantic 校验失败收敛成错误行
            errors.append(f"{where}: {exc}")
            continue
        if node.type != layer:
            errors.append(f"{where}: type「{node.type}」与本层「{layer}」不符")
            continue
        nodes.append(node)
        _check_common(node, errors, where, seen_ids)
        _CHECKS[layer](node, errors, where)
    return (nodes if not errors else []), errors


def parse_draft_file(layer: str, path: Path) -> tuple[list[DraftNode], list[str]]:
    """读一个草稿文件并校验；文件缺失/JSON 坏都收敛为错误表（不抛）。"""
    if not path.is_file():
        return [], [f"草稿文件不存在：{path.name}"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [], [f"草稿文件不可解析：{exc}"]
    return validate_drafts(layer, raw)


class CaseDraft(BaseModel):
    """一条用例正文（第四层；只活在项目空间 design/cases/，不进 KB——裁定 35）。

    裁定 38：点↔用例数量关系灵活（1:1／一点多条／多点合一条都允许），可核对性全靠 covers
    显式认领：covers 为空 = 这条用例谁都不落实，等于漏测的伪装；认领不存在的点 = 假完整。
    """

    case_id: str = ""                      # 新增留空串，由驱动 next_case_seq 分配并回写
    title: str = ""
    covers: list[str] = Field(default_factory=list)
    preconditions: str = ""
    steps: list[str] = Field(default_factory=list)
    expected: list[str] = Field(default_factory=list)
    priority: str = "P1"
    note: str = ""


def validate_cases(raw: Any) -> tuple[list[CaseDraft], list[str]]:
    """校验一个批次用例草稿文件。错误表非空即重问（批内自检，不进末门修复环）。

    必填缺失在此**拒收**（驱动 nudge 环），因此 `run_case_checks` 不必为它增设 hard code——
    同一条坏输入不该有两个处置出口（裁定 37 的规范侧只留呈递线索）。
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return [], ["用例文件根必须是对象 {chain, batch, cases}"]
    if not isinstance(raw.get("chain"), str) or not raw.get("chain"):
        errors.append("chain 字段缺失（本批归属的链路 id）")
    if not isinstance(raw.get("batch"), str) or not raw.get("batch"):
        errors.append("batch 字段缺失（本批 id，须与文件名一致）")
    items = raw.get("cases")
    if not isinstance(items, list) or not items:
        return [], [*errors, "cases 必须是非空数组（本批一条都没有 = 没做事，不是空批）"]
    cases: list[CaseDraft] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(items):
        where = f"cases[{i}]"
        try:
            case = CaseDraft.model_validate(item)
        except Exception as exc:
            errors.append(f"{where}: {exc}")
            continue
        if case.case_id:
            if not CASE_ID_RE.match(case.case_id):
                errors.append(f"{where}: case_id「{case.case_id}」形状非法（应为 cc-四位数字）")
            elif case.case_id in seen_ids:
                errors.append(f"{where}: case_id「{case.case_id}」在本文件内重复")
            else:
                seen_ids.add(case.case_id)
        if not case.title.strip():
            errors.append(f"{where}: title 不能为空")
        if not case.covers:
            errors.append(f"{where}: covers 不能为空（必须点名这条用例落实哪些测试点 pt-xxxx）")
        bad = [c for c in case.covers if not re.match(r"^pt-\d{4}$", str(c))]
        if bad:
            errors.append(f"{where}: covers 含非测试点 id {bad}（只许 pt-四位数字）")
        if len(set(map(str, case.covers))) != len(case.covers):
            errors.append(f"{where}: covers 内有重复 id")
        if not case.preconditions.strip():
            errors.append(f"{where}: preconditions 不能为空")
        if not [s for s in case.steps if str(s).strip()]:
            errors.append(f"{where}: steps 必须至少一步且非空")
        if not [e for e in case.expected if str(e).strip()]:
            errors.append(f"{where}: expected 必须至少一条硬断言且非空")
        if case.priority not in PRIORITY_RANK:
            errors.append(f"{where}: priority「{case.priority}」非法（P0/P1/P2）")
        cases.append(case)
    return (cases if not errors else []), errors


def parse_case_file(path: Path) -> tuple[list[CaseDraft], list[str]]:
    """读一个批次文件并校验；缺失/坏 JSON 收敛为错误表（不抛，与 parse_draft_file 同形）。"""
    if not path.is_file():
        return [], [f"用例文件不存在：{path.name}"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [], [f"用例文件不可解析：{exc}"]
    return validate_cases(raw)
