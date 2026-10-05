"""三层节点桶的 reme step：节点 markdown 读写唯一实现 + op=list/upsert/delete。

经 plugin.yaml 以 `aitester_kb_nodes_step` 注册进 reme 的 application-local registry
（不经全局 R），模型不可见——只由 _KB_JOBS 的三个 job 白名单引用。

存储形态：`<kb_root>/<bucket>/<id>.md`；frontmatter 是机器字段（枚举与引用靠它），
正文是人读视图（也是 reindex 后 knowledge_search 的内容面）。写入原子替换（A6）。
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from reme.knowledge.store import kb_root
from reme.steps.base_step import BaseStep

from aitester.case_design.constants import ID_RE, LAYER_BUCKET, TYPE_PREFIX

_FRONT_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

# 层字段（frontmatter 键序即此序）；链路的 parent 空串也必须落键——P-3 靠 "parent" in row 判维护性
# priority 是三层共用字段（schema 标 共用），链路/故事也必须落：④ 的优先级沿树检查直接读
# 宇宙里的 chain/story 行，回写丢字段会让下一轮把 P0 存量当成 P1，从而假报 hard 违例（R-16）。
_NODE_FIELDS: dict[str, tuple[str, ...]] = {
    "chain": ("priority", "level", "parent", "business_scope", "excluded"),
    "story": ("priority", "chains", "actor", "preconditions", "trigger", "expected", "assumptions"),
    "point": ("story", "scenario", "entities", "directions", "priority"),
}
_BODY_LABELS: dict[str, tuple[tuple[str, str], ...]] = {
    "chain": (("priority", "优先级"), ("level", "层级"), ("parent", "上级链路"),
              ("business_scope", "业务范围"), ("excluded", "不含范围")),
    "story": (("priority", "优先级"), ("chains", "所属链路"), ("actor", "主角"),
              ("preconditions", "业务前置"), ("trigger", "触发"), ("expected", "期望结果"),
              ("assumptions", "假设")),
    "point": (("story", "所属故事"), ("scenario", "场景"), ("entities", "涉及实体"),
              ("directions", "方向"), ("priority", "优先级")),
}

# 两张表写死层名——导入期钉死与 constants 的单点定义一致（评审 Minor 8）
assert set(_NODE_FIELDS) == set(LAYER_BUCKET)
assert set(_BODY_LABELS) == set(LAYER_BUCKET)


def node_filename(node_id: str) -> str:
    return f"{node_id}.md"


def _fmt(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value) or "—"
    text = str(value).strip()
    return text if text else "—"


def render_node_markdown(layer: str, node: dict[str, Any]) -> str:
    """渲染节点文件全文；字段顺序固定（读回形状可断言）。"""
    front: dict[str, Any] = {"id": str(node.get("id") or ""), "type": layer,
                             "name": str(node.get("name") or "")}
    for field in _NODE_FIELDS[layer]:
        value = node.get(field)
        front[field] = "" if value is None else value
    front["updated_at"] = datetime.now().isoformat(timespec="seconds")
    head = yaml.safe_dump(front, allow_unicode=True, sort_keys=False,
                          default_flow_style=False).rstrip()
    body = [f"# {front['name']}", ""]
    body += [f"- {label}：{_fmt(front[field])}" for field, label in _BODY_LABELS[layer]]
    return f"---\n{head}\n---\n\n" + "\n".join(body) + "\n"


def parse_node_markdown(text: str, layer: str) -> dict[str, Any]:
    """解析节点文件为行 dict；形状不符（无 frontmatter / id 或 type 不符）响亮失败。"""
    match = _FRONT_RE.match(text or "")
    if match is None:
        raise ValueError("missing YAML frontmatter")
    try:
        front = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:  # 坏 YAML 收敛为 ValueError：list 的坏文件标记行才接得住
        raise ValueError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(front, dict):
        raise ValueError("frontmatter is not a mapping")
    node_id = str(front.get("id") or "")
    if not ID_RE.fullmatch(node_id):
        raise ValueError(f"invalid node id {node_id!r}")
    if front.get("type") != layer:
        raise ValueError(f"node type {front.get('type')!r} != layer {layer!r}")
    if not node_id.startswith(TYPE_PREFIX[layer] + "-"):
        raise ValueError(f"node id {node_id!r} does not belong to layer {layer!r}")
    return front


def _validate_id(layer: str, node_id: Any) -> str:
    # step 是文件系统边界：`.match`+`^…$` 会放走 "ch-0001\n"（POSIX 上留下删不掉的垃圾文件名），
    # 必须 fullmatch 收掉（评审 Minor 3）
    node_id = str(node_id or "")
    if not ID_RE.fullmatch(node_id):
        raise ValueError(f"invalid node id {node_id!r} (expected {TYPE_PREFIX[layer]}-NNNN)")
    if not node_id.startswith(TYPE_PREFIX[layer] + "-"):
        raise ValueError(f"node id {node_id!r} does not belong to layer {layer!r}")
    return node_id


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        # replace 失败不在桶里遗留 .tmp 垃圾（评审 Minor 1）；清理失败不得掩盖原始异常。
        # 瞬时错误（如 Windows PermissionError）刻意不在 step 就地重试——
        # 重试预算归驱动（A7 WRITEBACK_FIX_CAP），失败必须响亮冒泡（评审驳回 Minor 2）。
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class CaseNodesStep(BaseStep):
    """按 op 枚举 / 写入 / 删除三层节点桶（list/upsert/delete）。"""

    async def execute(self):
        # 与下方 app_config 守卫同风格的显式 raise：assert 在 -O 下会被剥掉（评审 Minor 7）
        if self.context is None:
            raise RuntimeError("job context is unavailable")
        response = self.context.response
        op = str(self.kwargs.get("op") or "")
        layer = str(self.context.get("layer") or "")
        if layer not in LAYER_BUCKET:
            raise ValueError(f"unknown layer {layer!r}; expected one of {sorted(LAYER_BUCKET)}")
        if op not in ("list", "upsert", "delete"):
            raise ValueError(f"unknown op {op!r}; expected list/upsert/delete")
        cfg = self.app_context.app_config if self.app_context is not None else None
        if cfg is None:
            raise RuntimeError("application context is unavailable")
        bucket_dir = kb_root(cfg.knowledge_base_id,
                             knowledge_bases_dir=cfg.knowledge_bases_dir or None) / LAYER_BUCKET[layer]

        if op == "list":
            nodes = []
            if bucket_dir.is_dir():
                for path in sorted(bucket_dir.glob("*.md")):
                    try:
                        nodes.append(parse_node_markdown(path.read_text(encoding="utf-8"), layer))
                    except (OSError, ValueError) as exc:   # 坏文件以空标记行进列表：P-3 会判「未维护」
                        nodes.append({"id": "", "type": "", "file": path.name, "error": str(exc)})
            response.metadata = {"layer": layer, "count": len(nodes), "nodes": nodes}
            response.answer = f"listed {len(nodes)} {layer} node(s)"
        elif op == "upsert":
            node = self.context.get("node")
            if not isinstance(node, dict):
                raise ValueError("upsert requires a 'node' dict from the job call")
            node_id = _validate_id(layer, node.get("id"))
            if str(node.get("type") or layer) != layer:
                raise ValueError(f"node type {node.get('type')!r} != layer {layer!r}")
            if not str(node.get("name") or "").strip():
                raise ValueError("upsert requires a non-empty node name")
            bucket_dir.mkdir(parents=True, exist_ok=True)
            path = bucket_dir / node_filename(node_id)
            _atomic_write(path, render_node_markdown(layer, node))
            response.metadata = {"layer": layer, "id": node_id, "path": str(path)}
            response.answer = f"upserted {node_id}"
        else:
            node_id = _validate_id(layer, self.context.get("id"))
            path = bucket_dir / node_filename(node_id)
            deleted = path.is_file()
            if deleted:
                path.unlink()
            response.metadata = {"layer": layer, "id": node_id, "deleted": deleted}
            response.answer = f"deleted {node_id}" if deleted else f"{node_id} not found"
        return response
