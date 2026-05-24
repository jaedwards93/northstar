"""Outbound SMS delivery and retries."""

import asyncio
import uuid
from datetime import datetime

import httpx

from middleware.app.config import get_settings
from middleware.app.models import (
    AgentReplyResponse,
    DeliveryStatus,
    Message,
    MessageDirection,
    SessionStatus,
)
from middleware.app.services.sessions import expire_if_needed, is_session_expired
from middleware.app.store import OutboundProcessResult, get_store
from shared.session_policy import as_utc, utc_now


class SessionNotFoundError(Exception):
    """No session for the given id."""


class SessionExpiredError(Exception):
    """Agent reply rejected because the session is expired."""


def make_outbound_idempotency_key(
    session_id: str, text: str, timestamp: datetime
) -> str:
    """Stable key for duplicate outbound deliveries (same payload = duplicate)."""
    return f"{session_id}|{text}|{timestamp.isoformat()}"


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
    backoffs = settings.outbound_retry_backoffs_seconds

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
            delay_index = attempt - 1
            delay = backoffs[delay_index] if delay_index < len(backoffs) else backoffs[-1]
            await asyncio.sleep(delay)

    return False, max_attempts, last_error


async def send_reply(
    session_id: str, text: str, timestamp: datetime | None = None
) -> AgentReplyResponse:
    store = get_store()
    settings = get_settings()
    now = utc_now()
    at = as_utc(timestamp or now)
    idempotency_key = make_outbound_idempotency_key(session_id, text, at)

    async with store.lock:
        prior = store.get_outbound_result(idempotency_key)
        if prior is not None:
            return AgentReplyResponse(
                success=prior.success,
                error=prior.error,
                delivery_attempts=prior.delivery_attempts,
                duplicate=True,
            )

        session = store.get_session_by_id(session_id)
        if session is None:
            raise SessionNotFoundError()

        session = expire_if_needed(session, store, now)
        if session.status == SessionStatus.EXPIRED or is_session_expired(
            session, now, settings.session_ttl_seconds
        ):
            raise SessionExpiredError()

        message_id = str(uuid.uuid4())
        session.messages.append(
            Message(
                id=message_id,
                direction=MessageDirection.OUTBOUND,
                text=text,
                timestamp=at,
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

    async with store.lock:
        session = store.get_session_by_id(session_id)
        if session is not None and session.messages:
            last = session.messages[-1]
            if last.direction == MessageDirection.OUTBOUND and last.id == message_id:
                session.messages[-1] = last.model_copy(
                    update={
                        "delivery_status": (
                            DeliveryStatus.DELIVERED if success else DeliveryStatus.FAILED
                        ),
                        "delivery_error": error if not success else None,
                        "delivery_attempts": attempts,
                    }
                )
                store.put_session(session)

        store.record_outbound_result(
            idempotency_key,
            OutboundProcessResult(
                session_id=session_id,
                internal_message_id=message_id,
                success=success,
                error=error,
                delivery_attempts=attempts,
            ),
        )

    return AgentReplyResponse(
        success=success,
        error=error,
        delivery_attempts=attempts,
        duplicate=False,
    )
