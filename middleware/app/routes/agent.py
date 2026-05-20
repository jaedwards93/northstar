"""Agent console routes (GET/POST /sessions/...)."""

from fastapi import APIRouter, HTTPException

from middleware.app.models import (
    AgentReplyRequest,
    AgentReplyResponse,
    ErrorResponse,
    SessionDetail,
    SessionSummary,
)
from middleware.app.services.outbound import (
    SessionExpiredError,
    SessionNotFoundError,
    send_reply,
)
from middleware.app.services.sessions import get_session_detail, list_active_sessions

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionSummary], response_model_by_alias=True)
async def list_sessions() -> list[SessionSummary]:
    return await list_active_sessions()


@router.get(
    "/{session_id}",
    response_model=SessionDetail,
    response_model_by_alias=True,
)
async def get_session(session_id: str) -> SessionDetail:
    detail = await get_session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@router.post(
    "/{session_id}/reply",
    response_model=AgentReplyResponse,
    responses={
        404: {"description": "Session not found"},
        409: {"model": ErrorResponse},
    },
)
async def reply(session_id: str, payload: AgentReplyRequest) -> AgentReplyResponse:
    try:
        return await send_reply(session_id, payload.text)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found") from None
    except SessionExpiredError:
        raise HTTPException(
            status_code=409,
            detail=ErrorResponse(
                code="SESSION_EXPIRED",
                message="Session has expired; start a new session by texting in again",
            ).model_dump(),
        ) from None
