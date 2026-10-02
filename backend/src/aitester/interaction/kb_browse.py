"""知识库文件浏览接口：逐条移植 prototype/serve.js:93-198 的六 handler 语义。

错误体统一 FastAPI 的 detail 键（serve.js 用 error，这是唯一键名偏差）；
状态码、文案、限额与 serve.js 一致。安全：路径锁死 KB 实体根内、隐藏目录/点文件
不可见、仅白名单文本类型、2MB 上限。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aitester.services.kb.paths import (
    hidden_segment,
    is_hidden,
    mtime_ms,
    resolve_kb_root,
)

router = APIRouter(prefix="/api/kb/browse")

TEXT_EXT = {".md", ".markdown", ".txt", ".json", ".jsonl", ".py", ".js", ".ts", ".yaml", ".yml",
            ".sql", ".sh", ".bat", ".ini", ".cfg", ".csv", ".html", ".css", ".xml", ".toml",
            ".gitignore", ".log"}
MAX_TEXT = 2 * 1024 * 1024
SCAN_MD_MAX = 512 * 1024
WALK_DEPTH = 12

_FM_RE = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---")
_KV_RE = re.compile(r"^([A-Za-z0-9_\-.]+):\s*(.*)$")


def _root(request: Request) -> Path:
    settings = request.app.state.settings
    if not settings.kb_enabled:
        raise HTTPException(status_code=503, detail="知识库未启用或未启动")
    root = resolve_kb_root(settings)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="知识库实体目录不存在")
    return root


def _hidden(name: str) -> bool:
    # 隐藏名判据收敛到 paths.is_hidden（读写两侧共用同一谓词，终审修复项 2/9）
    return is_hidden(name)


def _resolve(root: Path, rel: str) -> Path:
    """serve.js kbPath 平移：拒 NUL、越界（resolve 后必须锁在根内）、隐藏段命中一律 403。"""
    if rel and "\0" in rel:
        raise HTTPException(status_code=403, detail="路径超出知识库范围")
    clean = rel.replace("\\", "/").lstrip("/")
    root_r = root.resolve()
    cand = (root_r / clean).resolve() if clean else root_r
    if cand != root_r and root_r not in cand.parents:
        raise HTTPException(status_code=403, detail="路径超出知识库范围")
    if hidden_segment(cand.relative_to(root_r).parts):
        raise HTTPException(status_code=403, detail="路径超出知识库范围")
    return cand


def _rel_of(root: Path, abs: Path) -> str:
    return abs.relative_to(root).as_posix()


def _is_text(abs: Path) -> bool:
    ext = abs.suffix.lower()
    return ext in TEXT_EXT or ext == ""


def _stat_d(p: Path) -> dict[str, Any]:
    st = p.stat()
    return {"size": st.st_size, "mtime": mtime_ms(st)}


def _parse_fm(text: str) -> dict[str, str] | None:
    """只解析 frontmatter 顶层 key: value（serve.js parseFm 同款，够原型用）。"""
    m = _FM_RE.match(text)
    if not m:
        return None
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        kv = _KV_RE.match(line)
        if not kv:
            continue
        v = kv.group(2).strip()
        if len(v) > 1 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1]
        out[kv.group(1)] = v
    return out


def _walk_abs(root: Path, depth: int = 0) -> list[Path]:
    out: list[Path] = []
    stack = [(root, depth)]
    while stack:
        cur, d = stack.pop()
        try:
            entries = sorted(cur.iterdir(), key=lambda e: e.name.lower())
        except OSError:
            # 瞬时列举失败按空目录收敛（与 _stat_d 守卫同口径韧性）
            continue
        for e in entries:
            if _hidden(e.name):
                continue
            out.append(e)
            if e.is_dir() and d + 1 < WALK_DEPTH:
                stack.append((e, d + 1))
    return out


@router.get("/tree")
def browse_tree(request: Request, path: str = "") -> dict[str, Any]:
    root = _root(request)
    target = _resolve(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")
    items = []
    try:
        entries = list(target.iterdir())
    except OSError:
        # 瞬时列举失败按空目录收敛（与 _stat_d 守卫同口径韧性）
        entries = []
    for e in entries:
        if _hidden(e.name):
            continue
        info: dict[str, Any] = {"name": e.name, "rel": _rel_of(root, e), "dir": e.is_dir()}
        try:
            info.update(_stat_d(e))
        except OSError:
            info.update({"size": 0, "mtime": 0})
        items.append(info)
    # dirs-first；zh 序用 lower() 近似（Global Constraints 偏差②）
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    return {"root": str(root), "rel": _rel_of(root, target), "items": items}


@router.get("/file")
def browse_file(request: Request, path: str = "") -> Any:
    root = _root(request)
    target = _resolve(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="是目录，不是文件")
    if not _is_text(target):
        # spec §A 错误表：415/413 附 editable:false（前端据此不进取编辑态）
        return JSONResponse(status_code=415, content={"detail": "暂不支持预览该文件类型", "editable": False})
    st = target.stat()
    if st.st_size > MAX_TEXT:
        return JSONResponse(status_code=413, content={"detail": "文件超过 2MB，只读不加载", "editable": False})
    return {
        "rel": _rel_of(root, target), "name": target.name, "ext": target.suffix.lower(),
        "content": target.read_text(encoding="utf-8", errors="replace"),
        "size": st.st_size, "mtime": mtime_ms(st), "editable": True,
    }


@router.get("/search")
def browse_search(request: Request, q: str = "", limit: int = 120) -> dict[str, Any]:
    root = _root(request)
    kw = q.strip().lower()
    if not kw:
        return {"root": str(root), "total": 0, "truncated": False, "hits": []}
    limit = max(1, min(limit, 400))
    hits = []
    for abs in _walk_abs(root):
        if kw not in abs.name.lower():
            continue
        try:
            info = {"name": abs.name, "rel": _rel_of(root, abs), "dir": abs.is_dir()}
            info.update(_stat_d(abs))
        except OSError:
            continue
        hits.append(info)
        if len(hits) >= limit:
            break
    hits.sort(key=lambda h: h["rel"].lower())
    return {"root": str(root), "total": len(hits), "truncated": len(hits) >= limit, "hits": hits}


@router.get("/scan")
def browse_scan(request: Request, path: str = "", limit: int = 800, md: str = "1") -> dict[str, Any]:
    root = _root(request)
    target = _resolve(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    limit = max(1, min(limit, 3000))
    only_md = md != "0"
    docs = []
    for abs in _walk_abs(target):
        if not abs.is_file():
            continue
        if only_md and abs.suffix.lower() != ".md":
            continue
        try:
            st = abs.stat()
        except OSError:
            continue
        if st.st_size > SCAN_MD_MAX:
            continue
        head = abs.read_text(encoding="utf-8", errors="replace")[:4000]
        docs.append({"rel": _rel_of(root, abs), "name": abs.name, "size": st.st_size,
                     "mtime": mtime_ms(st), "fm": _parse_fm(head)})
        if len(docs) >= limit:
            break
    return {"root": str(root), "scanned": len(docs), "truncated": len(docs) >= limit, "docs": docs}


class KbBrowseWriteBody(BaseModel):
    content: str = Field(description="文件全文")


@router.put("/file")
def browse_put(request: Request, body: KbBrowseWriteBody,
               path: str = Query(""), mtime: int = Query(0)) -> Any:
    # body 必传（终审项 7）：无请求体由 FastAPI 直接 422，而非 None 解引用 500
    root = _root(request)
    target = _resolve(root, path)
    if not _is_text(target):
        # spec §A 错误表：415/413 附 editable:false（与 409 同款 JSONResponse 形态）
        return JSONResponse(status_code=415, content={"detail": "只允许写入文本文件", "editable": False})
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在，请用新建接口")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="是目录")
    cur = mtime_ms(target.stat())
    if mtime and cur != mtime:
        # 对齐 serve.js：乐观锁冲突不覆盖，回磁盘当前 mtime 供前端重载
        return JSONResponse(status_code=409,
                            content={"detail": "文件在页面打开后被外部修改，未覆盖", "mtime": cur})
    data = body.content.encode("utf-8")
    if len(data) > MAX_TEXT:
        return JSONResponse(status_code=413, content={"detail": "内容过大", "editable": False})
    target.write_bytes(data)
    return {"rel": _rel_of(root, target), "size": len(data),
            "mtime": mtime_ms(target.stat())}


@router.post("/file")
def browse_post(request: Request, body: KbBrowseWriteBody,
                path: str = Query("")) -> Any:
    # body 必传（终审项 7）：无请求体由 FastAPI 直接 422，而非 None 解引用 500
    root = _root(request)
    target = _resolve(root, path)
    if not _is_text(target):
        return JSONResponse(status_code=415, content={"detail": "只允许写入文本文件", "editable": False})
    if target.exists():
        raise HTTPException(status_code=409, detail="同名文件已存在，请换个名字")
    if not target.parent.is_dir():
        raise HTTPException(status_code=400, detail="父目录不存在")
    data = body.content.encode("utf-8")
    if len(data) > MAX_TEXT:
        return JSONResponse(status_code=413, content={"detail": "内容过大", "editable": False})
    target.write_bytes(data)
    return JSONResponse(status_code=201, content={
        "rel": _rel_of(root, target), "size": len(data),
        "mtime": mtime_ms(target.stat()),
    })
