from fastapi import APIRouter

from aitester.interaction.schemas import EchoRequest, EchoResponse
from aitester.services import ChatService

router = APIRouter(prefix="/api")
_chat_service = ChatService()


@router.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "service": "aitester-backend"}


@router.post("/chat/echo", response_model=EchoResponse)
def chat_echo(req: EchoRequest) -> EchoResponse:
    result = _chat_service.echo(req.session_id, req.message)
    return EchoResponse(reply=result["reply"], trace=["interaction"] + result["trace"])
