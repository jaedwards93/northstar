"""Inbound SMS handling."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from middleware.app.config import get_settings
from middleware.app.models import (
    InboundWebhookRequest,
    Message,
    MessageDirection,
    Session,
    SessionStatus,
)
from middleware.app.services.sessions import (
    get_sessions_for_phone,
    is_session_expired,
    resolve_inbound_session,
)
from middleware.app.store import InboundProcessResult, get_store


@dataclass(frozen=True)
class InboundResult:
    session_id: str
    message_id: str
    duplicate: bool = False


def make_idempotency_key(from_number: str, text: str, timestamp: datetime) -> str:
    """Stable key for duplicate Northstar deliveries (same payload = duplicate)."""
    return f"{from_number}|{text}|{timestamp.isoformat()}"


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def handle_inbound(payload: InboundWebhookRequest) -> InboundResult:
    store = get_store()
    settings = get_settings()
    phone = payload.from_number.strip()
    at = _as_utc(payload.timestamp)
    idempotency_key = make_idempotency_key(phone, payload.text, payload.timestamp)
    ttl = settings.session_ttl_seconds

    async with store.lock:
        prior = store.get_inbound_result(idempotency_key)
        if prior is not None:
            return InboundResult(
                session_id=prior.session_id,
                message_id=prior.internal_message_id,
                duplicate=True,
            )

        session = resolve_inbound_session(store, phone, at, ttl)
        if session is None:
            for old in get_sessions_for_phone(store, phone):
                if old.status != SessionStatus.EXPIRED:
                    if is_session_expired(old, at, ttl):
                        store.put_session(
                            old.model_copy(update={"status": SessionStatus.EXPIRED})
                        )
            session = Session(
                id=str(uuid.uuid4()),
                phone=phone,
                status=SessionStatus.ACTIVE,
                last_activity_at=at,
                messages=[],
            )

        message_id = str(uuid.uuid4())
        session.messages.append(
            Message(
                id=message_id,
                direction=MessageDirection.INBOUND,
                text=payload.text,
                timestamp=at,
            )
        )
        session.last_activity_at = at
        session.status = SessionStatus.ACTIVE

        store.put_session(session)
        store.record_inbound_result(
            idempotency_key,
            InboundProcessResult(
                session_id=session.id, internal_message_id=message_id
            ),
        )

        return InboundResult(
            session_id=session.id,
            message_id=message_id,
            duplicate=False,
        )
