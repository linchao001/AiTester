import json
import logging
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
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
    StepInfo,
    StreamStopRequest,
    StreamStopResponse,
    ToolEnabledUpdate,
)
from aitester.services import ChatService
from aitester.services.capability_config import (
    CapabilityConfigError,
    CapabilityConfigService,
)
from aitester.services.chat import PreparedRun
from aitester.services.kb.manager import KbUnavailableError
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
from aitester.services.project_config import ProjectConfigError
from aitester.services.run_registry import new_run_id
from aitester.services.session_store import SessionStoreError

router = APIRouter(prefix="/api")

logger = logging.getLogger(__name__)

# 流开始前 = HTTP，流开始后 = error 事件（spec 错误口径表）
_GUARD_MAP = (
    (ConfigNotFoundError, 404),
    (ProjectConfigError, 400),
    (SessionStoreError, 404),
    (ProviderConfigError, 400),
)
# prepare 的全部失败面：_GUARD_MAP 四类 + 其余 ProviderError（→502）
_GUARD_TYPES = tuple(klass for klass, _ in _GUARD_MAP) + (ProviderError,)


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


def _frame(event: str, data: dict[str, Any]) -> str:
    # data 必须单行：SSE 按行切帧，负载里的裸换行会把一帧撕成两帧
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _draft_frame(artifact: Any) -> str | None:
    """草案逐条容错（终审项 5 原口径）：畸形草案丢一条，不砸整条流。"""
    try:
        draft = KbDraft.model_validate(artifact)
    except ValidationError:
        logger.warning("丢弃畸形草案：%r", artifact)
        return None
    return _frame("draft", {"draft": draft.model_dump()})


def _step_payload(step: dict[str, Any]) -> dict[str, Any]:
    # steps 严格构造、不做逐条容错：事件恒为本轮 stream_graph 新产物，畸形即装配 bug，须响亮失败
    return StepInfo(tool=step["tool"], ok=step["ok"], round=step["round"],
                    detail=step["detail"]).model_dump()


@router.post("/chat/send/stream")
def chat_send_stream(req: SendRequest, request: Request) -> StreamingResponse:
    """真实链路的唯一传输：守门同步跑，过后逐事件推流，终态恒为一条 done 或一条 error。"""
    service: ChatService = request.app.state.chat_service
    runs = request.app.state.run_registry
    try:
        prepared: PreparedRun = service.prepare(
            req.session_id, req.message, req.agent_id, req.project_id)
    except _GUARD_TYPES as exc:
        # brief 原码只包 ProviderError：ProjectConfigError/SessionStoreError/
        # ConfigNotFoundError 与它无继承关系（实测），漏包会把守门 4xx 炸成 500；
        # 码值对应与迁移前逐字相同（ProviderConfigError 子类 → 400，其余 ProviderError → 502）
        for klass, code in _GUARD_MAP:
            if isinstance(exc, klass):
                raise HTTPException(status_code=code, detail=exc.detail) from exc
        raise HTTPException(status_code=502, detail=exc.detail) from exc

    run_id = new_run_id()
    control = runs.start(run_id)

    def frames() -> Iterator[str]:
        # Task 5 的欠条：finish 必须在每条退出路径上跑（正常收尾、error 帧、客户端断开），
        # 否则在途条目永久泄漏，/chat/stop 会对已结束的 run 恒回成功
        try:
            yield _frame("start", {"run_id": run_id, "session_id": prepared.session_id})
            try:
                for event in service.stream_turn(prepared, control=control):
                    kind = event["type"]
                    if kind == "draft":
                        frame = _draft_frame(event["draft"])
                        if frame is not None:
                            yield frame
                    elif kind == "step":
                        yield _frame(kind, _step_payload(event))
                    elif kind == "done":
                        yield _frame(kind, {
                            "reply": event["reply"],
                            "steps": [_step_payload(s) for s in event["steps"]],
                            "session_id": event["session_id"],
                            "title": event["title"],
                            "stopped": event["stopped"],
                        })
                    else:                                   # delta / call
                        yield _frame(kind, {k: v for k, v in event.items() if k != "type"})
            except ProviderError as exc:
                # 流中失败：HTTP 已经 200，只能走事件；detail 原样（key 已在 provider 侧打星）
                yield _frame("error", {"detail": exc.detail})
            except Exception:
                # 非 ProviderError 一律是内部异常（含装配 bug）：str(exc) 不是用户文案，
                # 只落固定中文，诊断留在 exc_info 日志（spec「detail 一律中文」）
                logger.warning("流中非 ProviderError 异常", exc_info=True)
                yield _frame("error", {"detail": "流式输出异常，本条回答未完成"})
        finally:
            runs.finish(run_id)

    return StreamingResponse(frames(), media_type="text/event-stream")


@router.post("/chat/stop", response_model=StreamStopResponse)
def chat_stop(req: StreamStopRequest, request: Request) -> StreamStopResponse:
    """服务端真停：置取消位；节点在下一个检查点停笔，截断内容照落盘。"""
    if not request.app.state.run_registry.cancel(req.run_id):
        raise HTTPException(status_code=404, detail="这条回答已经结束")
    return StreamStopResponse(ok=True)


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
