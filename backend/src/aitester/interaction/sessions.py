"""会话管理三端点：只服务已落盘的 sess_* 会话。

临时键（kb-console 等）在此一律 404——它们的真相不在 sessions 目录（spec 兼容裁定）。
detail 中文且可照做；不泄露 sessions 目录以外的任何绝对路径。
"""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from aitester.interaction.schemas import (
    ChatMessageInfo,
    SessionInfo,
    SessionMessagesResponse,
    SessionsResponse,
    StepInfo,
)
from aitester.services.session_store import (
    MISSING_SESSION_DETAIL,
    ChatMessage,
    SessionStore,
)


def _store(request: Request) -> SessionStore:
    return request.app.state.sessions  # type: ignore[return-value]


def _missing() -> HTTPException:
    return HTTPException(status_code=404, detail=MISSING_SESSION_DETAIL)


router = APIRouter(prefix="/api/chat/sessions")


@router.get("", response_model=SessionsResponse)
def sessions_list(
    request: Request, agent_id: str = Query(min_length=1)
) -> SessionsResponse:
    # 未知或平台智能体 → 空列表 200：列表是「此处没有会话」，不是错误
    # model_validate 而非 **vars：与读路径 steps 同款口径，Session 数据类日后多出字段也不会炸
    return SessionsResponse(
        sessions=[
            SessionInfo.model_validate(vars(s)) for s in _store(request).list(agent_id)
        ]
    )


def _to_message(m: ChatMessage) -> ChatMessageInfo:
    # 只读路径容错（裁定 3）：手工编辑或旧格式的落盘行逐条丢弃畸形 steps，不整响应 500，
    # 其余合法消息照常返回；与 drafts 同款 model_validate 口径——非 dict 元素也归 ValidationError
    steps: list[StepInfo] = []
    for s in m.steps or []:
        try:
            steps.append(StepInfo.model_validate(s))
        except ValidationError:
            continue
    return ChatMessageInfo(
        role=m.role, content=m.content, ts=m.ts, steps=steps or None
    )


@router.get("/{session_id}/messages", response_model=SessionMessagesResponse)
def sessions_messages(request: Request, session_id: str) -> SessionMessagesResponse:
    store = _store(request)
    if store.get(session_id) is None:
        raise _missing()
    return SessionMessagesResponse(
        session_id=session_id,
        messages=[_to_message(m) for m in store.messages(session_id)],
    )


@router.delete("/{session_id}", status_code=204)
def sessions_delete(request: Request, session_id: str) -> None:
    if not _store(request).delete(session_id):
        raise _missing()
