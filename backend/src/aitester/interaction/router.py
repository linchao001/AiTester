from fastapi import APIRouter, Request

from aitester.interaction.schemas import EchoRequest, EchoResponse
from aitester.services import ChatService

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "service": "aitester-backend"}


@router.post("/chat/echo", response_model=EchoResponse)
def chat_echo(req: EchoRequest, request: Request) -> EchoResponse:
    service: ChatService = request.app.state.chat_service
    result = service.echo(req.session_id, req.message)
    return EchoResponse(reply=result["reply"], trace=["interaction"] + result["trace"])
