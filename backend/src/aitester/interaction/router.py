import asyncio
import json
import logging
import threading
from typing import Any, AsyncIterator, Iterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from aitester.adapters.llm import ProviderConfigError, ProviderError
from aitester.interaction.schemas import (
    AgentDefaultUpdate,
    AgentToolsUpdate,
    ApproveRequest,
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
    PendingCallInfo,
    PendingDecidedCall,
    PendingResponse,
    PendingRunInfo,
    ProviderTestRequest,
    ProviderTestResponse,
    ResumeRequest,
    SendRequest,
    StepInfo,
    StreamStopRequest,
    StreamStopResponse,
    ToolEnabledUpdate,
)
from aitester.orchestration.auth_rules import PermModeError
from aitester.services import ChatService
from aitester.services.capability_config import (
    CapabilityConfigError,
    CapabilityConfigService,
)
from aitester.services.chat import PreparedRun
from aitester.services.kb.manager import KbUnavailableError
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
from aitester.services.pending import CallDecidedError, PendingGoneError, ResumeNotReadyError
from aitester.services.project_config import ProjectConfigError
from aitester.services.run_registry import RunRegistry, new_run_id
from aitester.services.session_store import SessionStoreError

router = APIRouter(prefix="/api")

logger = logging.getLogger(__name__)

# 流开始前 = HTTP，流开始后 = error 事件（spec 错误口径表）
# 顺序即判据：子类在前（ProviderConfigError 是 ProviderError 的子类，反过来会把 400 洗成 502）
_GUARD_MAP = (
    (ConfigNotFoundError, 404),
    (ProjectConfigError, 400),
    (SessionStoreError, 404),
    (ProviderConfigError, 400),
    (PermModeError, 400),
    (ProviderError, 502),
)
_GUARD_TYPES = tuple(klass for klass, _ in _GUARD_MAP)
# 续跑独有的两条（pending 表在流前判，口径同「守门同步跑」）
_RESUME_GUARD_MAP = ((PendingGoneError, 404), (ResumeNotReadyError, 400)) + _GUARD_MAP
_RESUME_GUARD_TYPES = tuple(klass for klass, _ in _RESUME_GUARD_MAP)


def _http_guard(exc: Exception, mapping: tuple[tuple[type[Exception], int], ...]) -> HTTPException:
    """守门异常 → HTTPException：send 与 resume 共用同一个映射口（第 2 片「守门只有一段」的传输层版）。"""
    for klass, code in mapping:
        if isinstance(exc, klass):
            return HTTPException(status_code=code, detail=exc.detail)
    raise exc        # 不在表内 = 内部异常：原样抛出去，让它成为 500 而不是伪装成 4xx


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


def _stream_response(runs: RunRegistry, run_id: str, session_id: str,
                     turn: Iterator[dict[str, Any]]) -> StreamingResponse:
    """把一条已装配的事件流接成 SSE：守门已在调用方跑完，这里只负责推与收摊。

    这里的泵线程不是风格选择，是走查第 13 项的修复本体。原形态「同步生成器直接交给
    StreamingResponse」在客户端真断开（SPA 跳页/关页触发的 fetch abort）时会把这一回合
    整个丢掉了：Starlette 1.7 对 spec_version>=2.4 不再派监听断开的任务，同步迭代器经
    iterate_in_threadpool 包装，取消只落在「等下一次 next()」上——既不 close 生成器，
    也不给那个线程再投取消。生成器从此冻结在 yield 上：既不落截断盘也不落全量盘，
    runs.finish 永不执行（真机实测该 run 的 /chat/stop 在 165 s 后仍回 200）。
    现在 async relay 一被取消就在 finally 里置 stop，泵线程据此跳出并显式 close()，
    GeneratorExit 才真正落进 stream_turn 的断开分支（截断落盘）与 frames 的 finally。
    """
    # 内层事件生成器具名持有，且只由泵线程触碰：close() 是唯一能把 GeneratorExit
    # 准时送进 stream_turn 断开分支的通道（等 GC 回收等于不落盘）

    def frames() -> Iterator[str]:
        # Task 5 的欠条：finish 必须在每条退出路径上跑（正常收尾、error 帧、客户端断开），
        # 否则在途条目永久泄漏，/chat/stop 会对已结束的 run 恒回成功
        try:
            yield _frame("start", {"run_id": run_id, "session_id": session_id})
            try:
                for event in turn:
                    kind = event["type"]
                    if kind == "draft":
                        frame = _draft_frame(event["draft"])
                        if frame is not None:
                            yield frame
                    elif kind == "step" and event.get("subagent") is None:
                        # 子步骤帧（带来源标注）落 else 原样透传：_step_payload 会把标注吃掉
                        yield _frame(kind, _step_payload(event))
                    elif kind == "wait":
                        # 折叠已严格取键（Task 4）：这里只把续跑与停止要用的 run_id 附上
                        yield _frame(kind, {**{k: v for k, v in event.items() if k != "type"},
                                            "run_id": run_id})
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

    q: asyncio.Queue[str | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stop = threading.Event()

    def post(item: str | None) -> None:
        try:
            loop.call_soon_threadsafe(q.put_nowait, item)
        except RuntimeError:                    # 事件循环已关（进程退出中）：丢帧即可
            pass

    def pump() -> None:
        gen = frames()
        try:
            for frame in gen:
                post(frame)
                if stop.is_set():               # 客户端已走：下一帧前停笔
                    break
        finally:
            try:
                gen.close()                     # 先让 frames 跑完自己的 finally
            finally:
                if hasattr(turn, "close"):      # 桩测试用 iter() 替身，关闭通道不是它的契约
                    turn.close()                # 再触发 stream_turn 的截断落盘
                post(None)

    threading.Thread(target=pump, daemon=True, name=f"chat-stream-{run_id[:8]}").start()

    async def relay() -> AsyncIterator[str]:
        try:
            while True:
                frame = await q.get()
                if frame is None:
                    return
                yield frame
        finally:
            stop.set()                          # 取消与正常收尾都经此通知泵线程

    return StreamingResponse(relay(), media_type="text/event-stream")


@router.post("/chat/send/stream")
async def chat_send_stream(req: SendRequest, request: Request) -> StreamingResponse:
    """真实链路的唯一传输：守门同步跑，过后逐事件推流，终态恒为一条 done 或一条 error。"""
    service: ChatService = request.app.state.chat_service
    runs = request.app.state.run_registry
    try:
        prepared: PreparedRun = await run_in_threadpool(
            service.prepare, req.session_id, req.message, req.agent_id, req.project_id,
            req.perm_mode)
    except _GUARD_TYPES as exc:
        raise _http_guard(exc, _GUARD_MAP) from exc
    run_id = new_run_id()
    turn = service.stream_turn(prepared, control=runs.start(run_id), run_id=run_id)
    return _stream_response(runs, run_id, prepared.session_id, turn)


@router.post("/chat/approve", status_code=204)
def chat_approve(req: ApproveRequest, request: Request) -> None:
    """登记一条决策：204 无体。404=条目已不在（未知/已停/已重启），409=这条已答过。"""
    service: ChatService = request.app.state.chat_service
    try:
        service.approve(req.run_id, req.call_id, req.decision, req.remember)
    except (PendingGoneError, CallDecidedError) as exc:
        code = 404 if isinstance(exc, PendingGoneError) else 409
        raise HTTPException(status_code=code, detail=exc.detail) from exc


@router.post("/chat/resume/stream")
async def chat_resume_stream(req: ResumeRequest, request: Request) -> StreamingResponse:
    """批准后续跑：守门（含目录守卫）仍在 HTTP 空间，过后走同一条泵通道。

    run_id 不新建：待批条目、图线程、停止键三者始终是同一个 id（P4），
    所以 stop 在「在途」与「待批」两种状态下都能命中同一条回答。
    """
    service: ChatService = request.app.state.chat_service
    runs = request.app.state.run_registry
    control = runs.start(req.run_id)
    try:
        session_id, turn = await run_in_threadpool(service.resume_stream, req.run_id, control)
    except _RESUME_GUARD_TYPES as exc:
        runs.finish(req.run_id)                  # 守门没过就没有在途：不留半条 run
        raise _http_guard(exc, _RESUME_GUARD_MAP) from exc
    return _stream_response(runs, req.run_id, session_id, turn)


def _pending_info(entry) -> PendingRunInfo:
    # 严格构造、不逐条容错：条目由本片服务端自己写，畸形即装配 bug（与 _step_payload 同口径）
    return PendingRunInfo(
        run_id=entry.run_id, session_id=entry.session_id,
        perm_mode=entry.perm_mode, prefix=entry.prefix_text,
        created_at=entry.created_at,
        steps=[StepInfo(tool=s["tool"], ok=s["ok"], round=s["round"], detail=s["detail"])
               for s in entry.steps],
        waiting=[PendingCallInfo(**c) for c in entry.waiting()],
        decided=[PendingDecidedCall(**d) for d in entry.decided])


@router.get("/chat/pending", response_model=PendingResponse)
def chat_pending(request: Request, agent_id: str = Query(min_length=1),
                 project_id: str = Query(min_length=1)) -> PendingResponse:
    """待批表（内存态，后端重启即空）：刷新与重开页面靠它恢复卡片、前缀文本与已答痕迹。

    按「智能体 × 项目」取而不是按会话（R14）：挂起那一轮一个字都没落盘（裁定 8），
    刷新后前端只知道当前智能体与项目，那条会话在项目侧根本查不到。
    """
    service: ChatService = request.app.state.chat_service
    return PendingResponse(runs=[_pending_info(e)
                                 for e in service.pending_view_for(agent_id, project_id)])


@router.post("/chat/stop", response_model=StreamStopResponse)
def chat_stop(req: StreamStopRequest, request: Request) -> StreamStopResponse:
    """服务端真停：先打在途流，再打待批条目（裁定 10 第二条——待批期间停止钮照旧可用）。"""
    if request.app.state.run_registry.cancel(req.run_id):
        return StreamStopResponse(ok=True)
    service: ChatService = request.app.state.chat_service
    if service.cancel_pending(req.run_id):
        return StreamStopResponse(ok=True)
    raise HTTPException(status_code=404, detail="这条回答已经结束")


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
