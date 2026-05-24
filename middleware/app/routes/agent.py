"""Agent console routes (GET/POST /sessions/...)."""

from typing import Union

from fastapi import APIRouter, HTTPException, Query

from middleware.app.config import get_settings
from middleware.app.models import (
    AgentReplyRequest,
    AgentReplyResponse,
    ConsoleConfig,
    ErrorResponse,
    PhoneSummary,
    SessionDetail,
    SessionSummary,
    SessionTagsRequest,
)
from middleware.app.services.outbound import (
    SessionExpiredError,
    SessionNotFoundError,
    send_reply,
)
from middleware.app.services.sessions import (
    get_session_detail,
    list_sessions,
    list_sessions_by_phone,
    update_session_tags,
)

router = APIRouter(tags=["sessions"])


@router.get("/config", response_model=ConsoleConfig)
async def get_config() -> ConsoleConfig:
    settings = get_settings()
    return ConsoleConfig(
        session_ttl_seconds=settings.session_ttl_seconds,
        session_expiring_soon_seconds=settings.session_expiring_soon_seconds,
    )


@router.get(
    "/sessions",
    response_model=list[Union[PhoneSummary, SessionSummary]],
    response_model_by_alias=True,
)
async def list_sessions_route(
    include_expired: bool = Query(default=False),
    group_by_phone: bool = Query(default=False),
) -> list[PhoneSummary] | list[SessionSummary]:
    if group_by_phone:
        return await list_sessions_by_phone()
    return await list_sessions(include_expired=include_expired)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetail,
    response_model_by_alias=True,
)
async def get_session(session_id: str) -> SessionDetail:
    detail = await get_session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@router.patch(
    "/sessions/{session_id}/tags",
    response_model=SessionDetail,
    response_model_by_alias=True,
)
async def patch_session_tags(
    session_id: str, payload: SessionTagsRequest
) -> SessionDetail:
    detail = await update_session_tags(session_id, payload.tags)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail="Session not found or tags cannot be updated",
        )
    return detail


@router.post(
    "/sessions/{session_id}/reply",
    response_model=AgentReplyResponse,
    responses={
        404: {"description": "Session not found"},
        409: {"model": ErrorResponse},
    },
)
async def reply(session_id: str, payload: AgentReplyRequest) -> AgentReplyResponse:
    try:
        return await send_reply(session_id, payload.text, payload.timestamp)
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
