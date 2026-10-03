"""项目接口：CRUD 四端点。校验失败 400、未知 id 404，detail 中文且可照做。

响应只回知识库别名（`kb`），不回 reme 知识库 id 或实体根路径（脱敏裁定）。
`session_count` 与 `dir_exists` 是读侧组合字段：项目真相（ProjectService）× 会话真相
（SessionStore）× 磁盘实况（只读探测），三个真相源都在响应现算，不由服务层伪造。
"""

from logging import getLogger
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from aitester.interaction.schemas import (
    ProjectCreateRequest,
    ProjectInfo,
    ProjectsResponse,
    ProjectUpdateRequest,
)
from aitester.services.kb.aliases import PROJECT_KB_DEFAULT
from aitester.services.model_config import ConfigNotFoundError
from aitester.services.project_config import (
    ProjectConfigError,
    ProjectService,
    dir_exists,
)
from aitester.services.session_store import SessionStore, SessionStoreError

router = APIRouter(prefix="/api/projects")

logger = getLogger(__name__)


def _svc(request: Request) -> ProjectService:
    return request.app.state.project_config  # type: ignore[return-value]


def _sessions(request: Request) -> SessionStore:
    return request.app.state.sessions  # type: ignore[return-value]


def _info(p: dict[str, Any], store: SessionStore) -> ProjectInfo:
    """项目真相 + 会话数 + 目录实况：三个真相源在这里组合成一条对外项目。"""
    return ProjectInfo(
        **p,
        session_count=store.count_by_project(p["id"]),
        dir_exists=dir_exists(p["dir"]),
    )


@router.get("", response_model=ProjectsResponse)
def projects(request: Request) -> ProjectsResponse:
    store = _sessions(request)
    return ProjectsResponse(
        projects=[_info(p, store) for p in _svc(request).list_projects()]
    )


@router.post("", response_model=ProjectInfo, status_code=201)
def projects_create(req: ProjectCreateRequest, request: Request) -> ProjectInfo:
    try:
        created = _svc(request).create(
            name=req.name,
            desc=req.desc,
            dir_=req.dir,
            agents=req.agents,
            kb=req.kb if req.kb is not None else PROJECT_KB_DEFAULT,
        )
    except ProjectConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return _info(created, _sessions(request))


@router.put("/{project_id}", response_model=ProjectInfo)
def projects_update(project_id: str, req: ProjectUpdateRequest, request: Request) -> ProjectInfo:
    try:
        updated = _svc(request).update(
            project_id, name=req.name, desc=req.desc, agents=req.agents,
            dir_=req.dir, kb=req.kb,
        )
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except ProjectConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return _info(updated, _sessions(request))


@router.delete("/{project_id}", status_code=204)
def projects_delete(project_id: str, request: Request) -> None:
    try:
        _svc(request).delete(project_id)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except ProjectConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    # 先删项目再收会话：顺序反了就会在「剩 1 条禁删」这类校验失败时把会话陪葬掉
    try:
        _sessions(request).delete_by_project(project_id)
    except (OSError, SessionStoreError) as exc:
        # 项目已从真相里消失，此刻删不动的会话是孤儿：留话即可，绝不回滚项目删除
        logger.warning("项目 %s 已删除，但其会话回收失败：%s", project_id, exc)
