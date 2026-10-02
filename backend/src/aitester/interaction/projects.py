"""项目接口：CRUD 四端点。校验失败 400、未知 id 404，detail 中文且可照做。

响应只回知识库别名（`kb`），不回 reme 知识库 id 或实体根路径（脱敏裁定）。
"""

from fastapi import APIRouter, HTTPException, Request

from aitester.interaction.schemas import (
    ProjectCreateRequest,
    ProjectInfo,
    ProjectsResponse,
    ProjectUpdateRequest,
)
from aitester.services.kb.aliases import PROJECT_KB_DEFAULT
from aitester.services.model_config import ConfigNotFoundError
from aitester.services.project_config import ProjectConfigError, ProjectService

router = APIRouter(prefix="/api/projects")


def _svc(request: Request) -> ProjectService:
    return request.app.state.project_config  # type: ignore[return-value]


@router.get("", response_model=ProjectsResponse)
def projects(request: Request) -> ProjectsResponse:
    return ProjectsResponse(
        projects=[ProjectInfo(**p) for p in _svc(request).list_projects()]
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
    return ProjectInfo(**created)


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
    return ProjectInfo(**updated)


@router.delete("/{project_id}", status_code=204)
def projects_delete(project_id: str, request: Request) -> None:
    try:
        _svc(request).delete(project_id)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except ProjectConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
