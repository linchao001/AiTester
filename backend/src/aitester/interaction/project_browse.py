"""项目工作区文件接口：浏览项目目录、读文本、mtime 乐观锁写回。

根 = 项目 `dir` 的 expanduser()+resolve() 结果——与 `chat.send` 的 `cwd` 同一消费入口
（第 2 片的 `~` 教训：校验放行的每一种形态，消费侧必须同样处理）。
刻意不做 POST（无「新建文件」UI 消费方）与 search/scan（工作区不是知识库）。
"""

from __future__ import annotations

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
from aitester.services.kb.paths import mtime_ms
from aitester.services.model_config import ConfigNotFoundError
from aitester.services.project_config import ProjectService

router = APIRouter(prefix="/api/projects")

OUTSIDE = "路径超出项目目录范围"
DIR_GONE = "项目目录不存在或已被移动，请到项目页确认路径"


def _svc(request: Request) -> ProjectService:
    return request.app.state.project_config  # type: ignore[return-value]


def _root(request: Request, project_id: str) -> Path:
    try:
        project = _svc(request).get(project_id)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    try:
        root = Path(project["dir"]).expanduser().resolve()
    except (OSError, ValueError):
        # 畸形 dir（NUL/超长）：一律答「目录不可用」，绝不把用户填的路径外泄成 500
        raise HTTPException(status_code=404, detail=DIR_GONE) from None
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=DIR_GONE)
    return root


@router.get("/{project_id}/browse/tree")
def project_tree(request: Request, project_id: str, path: str = "") -> dict[str, Any]:
    root = _root(request, project_id)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")
    return {"root": str(root), "rel": rel_of(root, target), "items": listing(root, target)}


@router.get("/{project_id}/browse/file")
def project_file(request: Request, project_id: str, path: str = "") -> Any:
    root = _root(request, project_id)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="是目录，不是文件")
    if not is_text_file(target):
        return JSONResponse(status_code=415, content={"detail": "暂不支持预览该文件类型", "editable": False})
    st = target.stat()
    if st.st_size > MAX_TEXT:
        # 工作区没有只读视图，文案不能照抄 KB 的「只读不加载」
        return JSONResponse(status_code=413, content={"detail": "文件超过 2MB，无法打开", "editable": False})
    return {
        "rel": rel_of(root, target), "name": target.name, "ext": target.suffix.lower(),
        "content": target.read_text(encoding="utf-8", errors="replace"),
        "size": st.st_size, "mtime": mtime_ms(st), "editable": True,
    }


@router.put("/{project_id}/browse/file")
def project_put(request: Request, project_id: str, body: BrowseWriteBody,
                path: str = Query(""), mtime: int = Query(0)) -> Any:
    root = _root(request, project_id)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not is_text_file(target):
        return JSONResponse(status_code=415, content={"detail": "只允许写入文本文件", "editable": False})
    if not target.exists():
        # 工作区没有「新建接口」可指，文案指「被移动或删除」
        raise HTTPException(status_code=404, detail="保存失败：文件已不存在（可能被移动或删除）")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="是目录")
    cur = mtime_ms(target.stat())
    if mtime and cur != mtime:
        return JSONResponse(status_code=409,
                            content={"detail": "文件在页面打开后被外部修改，未覆盖", "mtime": cur})
    data = body.content.encode("utf-8")
    if len(data) > MAX_TEXT:
        return JSONResponse(status_code=413, content={"detail": "内容过大", "editable": False})
    target.write_bytes(data)
    return {"rel": rel_of(root, target), "size": len(data), "mtime": mtime_ms(target.stat())}
