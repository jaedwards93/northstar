"""Session listing, detail, and lazy expiry."""

from datetime import datetime, timezone

from middleware.app.config import get_settings
from middleware.app.models import Session, SessionDetail, SessionStatus, SessionSummary
from middleware.app.store import InMemoryStore, get_store


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_session_expired(session: Session, at: datetime, ttl_seconds: int) -> bool:
    if session.status == SessionStatus.EXPIRED:
        return True
    elapsed = (_as_utc(at) - _as_utc(session.last_activity_at)).total_seconds()
    return elapsed > ttl_seconds


def expire_if_needed(
    session: Session, store: InMemoryStore, at: datetime | None = None
) -> Session:
    """Mark session expired when past TTL; persist if changed."""
    at = at or _utc_now()
    ttl = get_settings().session_ttl_seconds
    if is_session_expired(session, at, ttl) and session.status != SessionStatus.EXPIRED:
        session = session.model_copy(update={"status": SessionStatus.EXPIRED})
        store.put_session(session)
    return session


def _to_summary(session: Session) -> SessionSummary:
    last = session.messages[-1] if session.messages else None
    return SessionSummary(
        id=session.id,
        from_number=session.phone,
        status=session.status,
        preview=last.text if last else None,
        timestamp=last.timestamp if last else None,
    )


def _to_detail(session: Session) -> SessionDetail:
    return SessionDetail(
        id=session.id,
        from_number=session.phone,
        status=session.status,
        last_activity_at=session.last_activity_at,
        messages=session.messages,
    )


async def list_active_sessions() -> list[SessionSummary]:
    """Active sessions only (expired sessions omitted from the list)."""
    store = get_store()
    now = _utc_now()

    async with store.lock:
        summaries: list[SessionSummary] = []
        for session in store.list_sessions():
            session = expire_if_needed(session, store, now)
            if session.status == SessionStatus.ACTIVE:
                summaries.append(_to_summary(session))
        summaries.sort(key=lambda s: s.timestamp or now, reverse=True)
        return summaries


async def get_session_detail(session_id: str) -> SessionDetail | None:
    """Full session history; returns None if id is unknown."""
    store = get_store()

    async with store.lock:
        session = store.get_session_by_id(session_id)
        if session is None:
            return None
        session = expire_if_needed(session, store)
        return _to_detail(session)
