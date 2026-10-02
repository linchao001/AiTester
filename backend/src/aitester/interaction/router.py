from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from aitester.adapters.llm import ProviderConfigError, ProviderError
from aitester.interaction.schemas import (
    AgentDefaultUpdate,
    AgentToolsUpdate,
    CapabilityResponse,
    DefaultUpdate,
    EchoRequest,
    EchoResponse,
    EnabledUpdate,
    KbDraft,
    KbInboxMergeRequest,
    KbInboxStemRequest,
    KbResponse,
    KbSaveRequest,
    KbSearchRequest,
    KeyUpdate,
    ModelsResponse,
    ProviderTestRequest,
    ProviderTestResponse,
    SendRequest,
    SendResponse,
    StepInfo,
    ToolEnabledUpdate,
)
from aitester.services import ChatService
from aitester.services.capability_config import (
    CapabilityConfigError,
    CapabilityConfigService,
)
from aitester.services.kb.manager import KbUnavailableError
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
from aitester.services.session_store import SessionStoreError

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    model_config: ModelConfigService = request.app.state.model_config
    return {
        "ok": True,
        "service": "aitester-backend",
        "llm_provider": model_config.default_uid or "mock",
    }


@router.post("/chat/echo", response_model=EchoResponse)
def chat_echo(req: EchoRequest, request: Request) -> EchoResponse:
    service: ChatService = request.app.state.chat_service
    result = service.echo(req.session_id, req.message)
    return EchoResponse(reply=result["reply"], trace=["interaction"] + result["trace"])


@router.post("/chat/send", response_model=SendResponse)
def chat_send(req: SendRequest, request: Request) -> SendResponse:
    service: ChatService = request.app.state.chat_service
    try:
        result = service.send(req.session_id, req.message, req.agent_id)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except SessionStoreError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except ProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    # 草案容错（终审项 5）：模型偶发产出缺必填键的畸形 dict，逐条丢弃而非整响应 500，
    # 回复与其余合法草案照常返回
    drafts: list[KbDraft] = []
    for d in result.get("drafts", []):
        try:
            drafts.append(KbDraft.model_validate(d))
        except ValidationError:
            continue
    # steps 严格构造、不做逐条容错：send 的 steps 恒为本轮 run_graph 新产物，
    # 此处若畸形是装配 bug，须响亮失败（裁定 3：落盘行容错只留在读路径 sessions.py）
    steps = [StepInfo(**s) for s in result.get("steps", [])]
    return SendResponse(
        reply=result["reply"],
        trace=["interaction"] + result["trace"],
        model=result["model"],
        drafts=drafts,
        session_id=str(result.get("session_id", "")),
        title=str(result.get("title", "")),
        steps=steps,
    )


def _view(request: Request) -> ModelsResponse:
    model_config: ModelConfigService = request.app.state.model_config
    return ModelsResponse(**model_config.get_view())


@router.get("/models", response_model=ModelsResponse)
def models(request: Request) -> ModelsResponse:
    return _view(request)


@router.put("/models/providers/{pid}/key", response_model=ModelsResponse)
def models_update_key(pid: str, req: KeyUpdate, request: Request) -> ModelsResponse:
    model_config: ModelConfigService = request.app.state.model_config
    try:
        model_config.update_api_key(pid, req.api_key)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    return _view(request)


@router.post("/models/providers/{pid}/test", response_model=ProviderTestResponse)
def models_test_provider(
    pid: str, req: ProviderTestRequest, request: Request
) -> ProviderTestResponse:
    model_config: ModelConfigService = request.app.state.model_config
    try:
        result = model_config.probe_provider(pid, req.api_key)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    return ProviderTestResponse(**result)


@router.put("/models/providers/{pid}/models/{mid}/enabled", response_model=ModelsResponse)
def models_update_enabled(
    pid: str, mid: str, req: EnabledUpdate, request: Request
) -> ModelsResponse:
    model_config: ModelConfigService = request.app.state.model_config
    try:
        model_config.set_model_enabled(pid, mid, req.enabled)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    return _view(request)


@router.put("/models/default", response_model=ModelsResponse)
def models_update_default(req: DefaultUpdate, request: Request) -> ModelsResponse:
    model_config: ModelConfigService = request.app.state.model_config
    try:
        model_config.set_default(req.uid)
    except ProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    return _view(request)


def _cap_view(request: Request) -> CapabilityResponse:
    capability: CapabilityConfigService = request.app.state.capability_config
    return CapabilityResponse(**capability.get_view())


@router.get("/capabilities", response_model=CapabilityResponse)
def capabilities(request: Request) -> CapabilityResponse:
    return _cap_view(request)


@router.put("/capabilities/agents/{aid}/default-model", response_model=CapabilityResponse)
def capabilities_agent_default_model(
    aid: str, req: AgentDefaultUpdate, request: Request
) -> CapabilityResponse:
    capability: CapabilityConfigService = request.app.state.capability_config
    try:
        capability.set_agent_default_model(aid, req.uid)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except CapabilityConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return _cap_view(request)


@router.put("/capabilities/agents/{aid}/tools", response_model=CapabilityResponse)
def capabilities_agent_tools(
    aid: str, req: AgentToolsUpdate, request: Request
) -> CapabilityResponse:
    capability: CapabilityConfigService = request.app.state.capability_config
    try:
        capability.set_agent_tools(aid, req.tool_ids)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except CapabilityConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return _cap_view(request)


@router.put("/capabilities/tools/{tid}/enabled", response_model=CapabilityResponse)
def capabilities_tool_enabled(
    tid: str, req: ToolEnabledUpdate, request: Request
) -> CapabilityResponse:
    capability: CapabilityConfigService = request.app.state.capability_config
    try:
        capability.set_tool_enabled(tid, req.enabled)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except CapabilityConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return _cap_view(request)


def _kb(request: Request):
    return request.app.state.kb_manager


def _kb_payload(resp) -> dict:
    return {"success": resp.success, "answer": resp.answer, "metadata": resp.metadata or {}}


@router.get("/kb/status", response_model=KbResponse)
async def kb_status(request: Request):
    try:
        return _kb_payload(await _kb(request).run_job("status"))
    except KbUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/kb/bases", response_model=KbResponse)
async def kb_bases(request: Request):
    try:
        return _kb_payload(await _kb(request).run_job("list_knowledge_bases"))
    except KbUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kb/search", response_model=KbResponse)
async def kb_search(request: Request, body: KbSearchRequest):
    try:
        return _kb_payload(await _kb(request).run_job(
            "knowledge_search", query=body.query, limit=body.limit, bucket=body.bucket,
        ))
    except KbUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kb/save", response_model=KbResponse)
async def kb_save(request: Request, body: KbSaveRequest):
    try:
        resp = await _kb(request).run_job(
            "save_to_knowledge", title=body.title, content=body.content, bucket=body.bucket,
        )
    except KbUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # 控制端裁定：save 后不再同步补跑 reindex——写方实例由后台
    # index_update_loop 收敛，跨实例最终一致已有专项测试覆盖
    return _kb_payload(resp)


@router.get("/kb/inbox", response_model=KbResponse)
async def kb_inbox(request: Request):
    try:
        return _kb_payload(await _kb(request).run_job("list_knowledge_inbox"))
    except KbUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kb/inbox/promote", response_model=KbResponse)
async def kb_inbox_promote(request: Request, body: KbInboxStemRequest):
    try:
        return _kb_payload(await _kb(request).run_job("promote_knowledge_inbox", stem=body.stem))
    except KbUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kb/inbox/merge", response_model=KbResponse)
async def kb_inbox_merge(request: Request, body: KbInboxMergeRequest):
    try:
        return _kb_payload(await _kb(request).run_job(
            "merge_knowledge_inbox", stem=body.stem, target_path=body.target_path, mode=body.mode,
        ))
    except KbUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kb/inbox/reject", response_model=KbResponse)
async def kb_inbox_reject(request: Request, body: KbInboxStemRequest):
    try:
        return _kb_payload(await _kb(request).run_job("reject_knowledge_inbox", stem=body.stem))
    except KbUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
