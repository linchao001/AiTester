"""KB 浏览与项目工作区共用的文件层原语：路径锁、文本白名单、列一层、条目统计、写体。

`resolve_within` 的三段判据（NUL/越界/隐藏段）取自 kb_browse.py 原 `_resolve` 平移，
403 文案参数化（KB 说「知识库范围」、工作区说「项目目录范围」，行为完全一致）；
隐藏段判据继续消费 services/kb/paths.py——读侧与写侧工具共用同一谓词。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from aitester.services.kb.paths import hidden_segment, is_hidden, mtime_ms

TEXT_EXT = {".md", ".markdown", ".txt", ".json", ".jsonl", ".py", ".js", ".ts", ".yaml", ".yml",
            ".sql", ".sh", ".bat", ".ini", ".cfg", ".csv", ".html", ".css", ".xml", ".toml",
            ".gitignore", ".log"}
MAX_TEXT = 2 * 1024 * 1024


def resolve_within(root: Path, rel: str, *, outside_detail: str) -> Path:
    """路径锁：拒 NUL、越界（resolve 后必须锁在根内）、隐藏段命中一律 403。"""
    if rel and "\0" in rel:
        raise HTTPException(status_code=403, detail=outside_detail)
    clean = rel.replace("\\", "/").lstrip("/")
    root_r = root.resolve()
    cand = (root_r / clean).resolve() if clean else root_r
    if cand != root_r and root_r not in cand.parents:
        raise HTTPException(status_code=403, detail=outside_detail)
    if hidden_segment(cand.relative_to(root_r).parts):
        raise HTTPException(status_code=403, detail=outside_detail)
    return cand


def rel_of(root: Path, abs: Path) -> str:
    return abs.relative_to(root).as_posix()


def is_text_file(abs: Path) -> bool:
    ext = abs.suffix.lower()
    return ext in TEXT_EXT or ext == ""


def stat_item(p: Path) -> dict[str, Any]:
    st = p.stat()
    return {"size": st.st_size, "mtime": mtime_ms(st)}


def listing(root: Path, target: Path) -> list[dict[str, Any]]:
    """列一层：隐藏剔除、逐条 stat（失败按 0 收敛）、dirs-first（lower() 近似 zh 序）。"""
    try:
        entries = list(target.iterdir())
    except OSError:
        # 瞬时列举失败按空目录收敛（与 stat_item 守卫同口径韧性）
        entries = []
    items = []
    for e in entries:
        if is_hidden(e.name):
            continue
        info: dict[str, Any] = {"name": e.name, "rel": rel_of(root, e), "dir": e.is_dir()}
        try:
            info.update(stat_item(e))
        except OSError:
            info.update({"size": 0, "mtime": 0})
        items.append(info)
    # dirs-first；zh 序用 lower() 近似（Global Constraints 偏差②）
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    return items


class BrowseWriteBody(BaseModel):
    content: str = Field(description="文件全文")
