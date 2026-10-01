from fastapi import APIRouter, HTTPException, Request

from aitester.adapters.llm import ProviderConfigError, ProviderError
from aitester.interaction.schemas import (
    AgentDefaultUpdate,
    AgentToolsUpdate,
    CapabilityResponse,
    DefaultUpdate,
    EchoRequest,
    EchoResponse,
    EnabledUpdate,
    KeyUpdate,
    ModelsResponse,
    ProviderTestRequest,
    ProviderTestResponse,
    SendRequest,
    SendResponse,
    ToolEnabledUpdate,
)
from aitester.services import ChatService
from aitester.services.capability_config import CapabilityConfigError, CapabilityConfigService
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService

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
    except ProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    return SendResponse(
        reply=result["reply"],
        trace=["interaction"] + result["trace"],
        model=result["model"],
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
