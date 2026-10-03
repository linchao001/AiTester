"""知识库文件浏览接口：逐条移植 prototype/serve.js:93-198 的六 handler 语义。

错误体统一 FastAPI 的 detail 键（serve.js 用 error，这是唯一键名偏差）；
状态码、文案、限额与 serve.js 一致。安全：路径锁死 KB 实体根内、隐藏目录/点文件
不可见、仅白名单文本类型、2MB 上限——路径锁/白名单/列目录函数体已抽到 browse_common
（项目工作区共用，KB 侧行为一位不变）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from aitester.interaction.browse_common import (
    MAX_TEXT,
    BrowseWriteBody,
    is_text_file,
    listing,
    rel_of,
    resolve_within,
    stat_item,
)
from aitester.services.kb.paths import is_hidden, mtime_ms, resolve_kb_root

router = APIRouter(prefix="/api/kb/browse")

OUTSIDE = "路径超出知识库范围"
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
            # 瞬时列举失败按空目录收敛（与 stat_item 守卫同口径韧性）
            continue
        for e in entries:
            if is_hidden(e.name):
                continue
            out.append(e)
            if e.is_dir() and d + 1 < WALK_DEPTH:
                stack.append((e, d + 1))
    return out


@router.get("/tree")
def browse_tree(request: Request, path: str = "") -> dict[str, Any]:
    root = _root(request)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")
    return {"root": str(root), "rel": rel_of(root, target), "items": listing(root, target)}


@router.get("/file")
def browse_file(request: Request, path: str = "") -> Any:
    root = _root(request)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="是目录，不是文件")
    if not is_text_file(target):
        # spec §A 错误表：415/413 附 editable:false（前端据此不进取编辑态）
        return JSONResponse(status_code=415, content={"detail": "暂不支持预览该文件类型", "editable": False})
    st = target.stat()
    if st.st_size > MAX_TEXT:
        return JSONResponse(status_code=413, content={"detail": "文件超过 2MB，只读不加载", "editable": False})
    return {
        "rel": rel_of(root, target), "name": target.name, "ext": target.suffix.lower(),
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
            info = {"name": abs.name, "rel": rel_of(root, abs), "dir": abs.is_dir()}
            info.update(stat_item(abs))
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
    target = resolve_within(root, path, outside_detail=OUTSIDE)
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
        docs.append({"rel": rel_of(root, abs), "name": abs.name, "size": st.st_size,
                     "mtime": mtime_ms(st), "fm": _parse_fm(head)})
        if len(docs) >= limit:
            break
    return {"root": str(root), "scanned": len(docs), "truncated": len(docs) >= limit, "docs": docs}


@router.put("/file")
def browse_put(request: Request, body: BrowseWriteBody,
               path: str = Query(""), mtime: int = Query(0)) -> Any:
    # body 必传（终审项 7）：无请求体由 FastAPI 直接 422，而非 None 解引用 500
    root = _root(request)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not is_text_file(target):
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
    return {"rel": rel_of(root, target), "size": len(data),
            "mtime": mtime_ms(target.stat())}


@router.post("/file")
def browse_post(request: Request, body: BrowseWriteBody,
                path: str = Query("")) -> Any:
    # body 必传（终审项 7）：无请求体由 FastAPI 直接 422，而非 None 解引用 500
    root = _root(request)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not is_text_file(target):
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
        "rel": rel_of(root, target), "size": len(data),
        "mtime": mtime_ms(target.stat()),
    })
