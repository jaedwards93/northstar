"""Outbound SMS delivery and retries."""

import asyncio
import uuid
from datetime import datetime, timezone

import httpx

from middleware.app.config import get_settings
from middleware.app.models import (
    AgentReplyResponse,
    Message,
    MessageDirection,
    SessionStatus,
)
from middleware.app.services.sessions import expire_if_needed, is_session_expired
from middleware.app.store import get_store


class SessionNotFoundError(Exception):
    """No session for the given id."""


class SessionExpiredError(Exception):
    """Agent reply rejected because the session is expired."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def deliver_to_northstar(
    *,
    to_number: str,
    from_number: str,
    text: str,
    session_id: str,
) -> tuple[bool, int, str | None]:
    """POST to mock Northstar with retries. Returns (success, attempts, error)."""
    settings = get_settings()
    payload = {
        "to": to_number,
        "from": from_number,
        "text": text,
        "session_id": session_id,
    }

    last_error: str | None = None
    max_attempts = settings.outbound_max_retries

    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    settings.northstar_outbound_url,
                    json=payload,
                    timeout=5.0,
                )
            if response.is_success:
                return True, attempt, None
            if 400 <= response.status_code < 500:
                return (
                    False,
                    attempt,
                    f"Northstar rejected the message (HTTP {response.status_code})",
                )
            last_error = f"Northstar error (HTTP {response.status_code})"
        except httpx.RequestError as exc:
            last_error = str(exc)

        if attempt < max_attempts:
            await asyncio.sleep(settings.outbound_retry_backoff_seconds)

    return False, max_attempts, last_error


async def send_reply(session_id: str, text: str) -> AgentReplyResponse:
    store = get_store()
    settings = get_settings()
    now = _utc_now()

    async with store.lock:
        session = store.get_session_by_id(session_id)
        if session is None:
            raise SessionNotFoundError()

        session = expire_if_needed(session, store, now)
        if session.status == SessionStatus.EXPIRED or is_session_expired(
            session, now, settings.session_ttl_seconds
        ):
            raise SessionExpiredError()

        session.messages.append(
            Message(
                id=str(uuid.uuid4()),
                direction=MessageDirection.OUTBOUND,
                text=text,
                timestamp=now,
            )
        )
        session.last_activity_at = now
        store.put_session(session)

        phone = session.phone
        sid = session.id

    success, attempts, error = await deliver_to_northstar(
        to_number=phone,
        from_number=settings.reply_from_number,
        text=text,
        session_id=sid,
    )
    return AgentReplyResponse(
        success=success, error=error, delivery_attempts=attempts
    )
