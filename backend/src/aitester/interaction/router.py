from fastapi import APIRouter, HTTPException, Request

from aitester.adapters.llm import ProviderConfigError, ProviderError
from aitester.interaction.schemas import (
    EchoRequest,
    EchoResponse,
    SendRequest,
    SendResponse,
)
from aitester.services import ChatService
from aitester.services import chat as chat_module

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": "aitester-backend",
        "llm_provider": chat_module.get_settings().llm_provider,
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
        result = service.send(req.session_id, req.message)
    except ProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    return SendResponse(
        reply=result["reply"],
        trace=["interaction"] + result["trace"],
        model=result["model"],
    )
