"""Session listing, detail, and lazy expiry."""

from datetime import datetime, timezone

from middleware.app.config import get_settings
from middleware.app.models import (
    AgencyTag,
    MessageDirection,
    PhoneSummary,
    Session,
    SessionBlock,
    SessionDetail,
    SessionStatus,
    SessionSummary,
)
from middleware.app.store import InMemoryStore, get_store
from shared.session_policy import as_utc, expires_at, is_expired


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_session_expired(session: Session, at: datetime, ttl_seconds: int) -> bool:
    return is_expired(
        status=session.status,
        last_activity_at=session.last_activity_at,
        at=at,
        ttl_seconds=ttl_seconds,
    )


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


def session_started_at(session: Session) -> datetime:
    if session.messages:
        return min(as_utc(m.timestamp) for m in session.messages)
    return as_utc(session.last_activity_at)


def session_expired_at(session: Session, ttl_seconds: int) -> datetime:
    return expires_at(session.last_activity_at, ttl_seconds)


def last_inbound_at(session: Session) -> datetime | None:
    latest: datetime | None = None
    for msg in session.messages:
        if msg.direction != MessageDirection.INBOUND:
            continue
        ts = as_utc(msg.timestamp)
        if latest is None or ts > latest:
            latest = ts
    return latest


def get_sessions_for_phone(store: InMemoryStore, phone: str) -> list[Session]:
    return [s for s in store.list_all_sessions() if s.phone == phone]


def resolve_inbound_session(
    store: InMemoryStore, phone: str, at: datetime, ttl_seconds: int
) -> Session | None:
    """Active session for inbound, healing stale phone pointers after races."""
    current = store.get_session_by_phone(phone)
    if current is not None and not is_session_expired(current, at, ttl_seconds):
        return current

    for session in sorted(
        get_sessions_for_phone(store, phone),
        key=lambda s: s.last_activity_at,
        reverse=True,
    ):
        if not is_session_expired(session, at, ttl_seconds):
            store.sessions_by_phone[phone] = session
            return session
    return None


def _pick_current_session_for_phone(
    store: InMemoryStore, phone: str, at: datetime, ttl_seconds: int
) -> Session | None:
    """Current conversation for a phone (reply target or newest)."""
    resolved = resolve_inbound_session(store, phone, at, ttl_seconds)
    if resolved is not None:
        return resolved
    sessions = get_sessions_for_phone(store, phone)
    if not sessions:
        return None
    return max(sessions, key=lambda s: s.last_activity_at)


def _to_block(session: Session, ttl_seconds: int) -> SessionBlock:
    return SessionBlock(
        id=session.id,
        status=session.status,
        started_at=session_started_at(session),
        expired_at=session_expired_at(session, ttl_seconds),
        messages=list(session.messages),
        agency_tags=list(session.agency_tags),
    )


def _to_summary(session: Session) -> SessionSummary:
    last = session.messages[-1] if session.messages else None
    return SessionSummary(
        id=session.id,
        from_number=session.phone,
        status=session.status,
        preview=last.text if last else None,
        timestamp=last.timestamp if last else session.last_activity_at,
        last_activity_at=session.last_activity_at,
    )


def _to_detail(
    session: Session,
    *,
    previous_sessions: list[SessionBlock],
    is_reply_target: bool,
) -> SessionDetail:
    return SessionDetail(
        id=session.id,
        from_number=session.phone,
        status=session.status,
        last_activity_at=session.last_activity_at,
        messages=session.messages,
        previous_sessions=previous_sessions,
        is_reply_target=is_reply_target,
        agency_tags=list(session.agency_tags),
    )


async def list_sessions_by_phone() -> list[PhoneSummary]:
    """One sidebar row per phone; points at the current conversation session."""
    store = get_store()
    now = _utc_now()
    ttl = get_settings().session_ttl_seconds

    async with store.lock:
        phones: dict[str, list[Session]] = {}
        for session in store.list_all_sessions():
            session = expire_if_needed(session, store, now)
            phones.setdefault(session.phone, []).append(session)

        summaries: list[PhoneSummary] = []
        for phone, _sessions in phones.items():
            current = _pick_current_session_for_phone(store, phone, now, ttl)
            if current is None:
                continue
            last = current.messages[-1] if current.messages else None
            inbound_at = last_inbound_at(current)
            summaries.append(
                PhoneSummary(
                    from_number=phone,
                    current_session_id=current.id,
                    status=current.status,
                    preview=last.text if last else None,
                    timestamp=last.timestamp if last else current.last_activity_at,
                    last_inbound_at=inbound_at,
                    last_activity_at=current.last_activity_at,
                    agency_tags=list(current.agency_tags),
                )
            )
        summaries.sort(key=lambda s: s.timestamp or now, reverse=True)
        return summaries


async def list_sessions(*, include_expired: bool = False) -> list[SessionSummary]:
    """List sessions sorted by latest activity (newest first)."""
    store = get_store()
    now = _utc_now()

    async with store.lock:
        summaries: list[SessionSummary] = []
        source = store.list_all_sessions() if include_expired else store.list_sessions()
        for session in source:
            session = expire_if_needed(session, store, now)
            if include_expired or session.status == SessionStatus.ACTIVE:
                summaries.append(_to_summary(session))
        summaries.sort(key=lambda s: s.timestamp or now, reverse=True)
        return summaries


async def get_session_detail(session_id: str) -> SessionDetail | None:
    """Session detail with older same-phone sessions for context."""
    store = get_store()
    settings = get_settings()
    now = _utc_now()
    ttl = settings.session_ttl_seconds

    async with store.lock:
        session = store.get_session_by_id(session_id)
        if session is None:
            return None

        for s in get_sessions_for_phone(store, session.phone):
            expire_if_needed(s, store, now)

        session = store.get_session_by_id(session_id)
        if session is None:
            return None
        session = expire_if_needed(session, store, now)

        phone_sessions = sorted(
            (expire_if_needed(s, store, now) for s in get_sessions_for_phone(store, session.phone)),
            key=session_started_at,
        )
        idx = next((i for i, s in enumerate(phone_sessions) if s.id == session_id), 0)
        previous = [_to_block(s, ttl) for s in phone_sessions[:idx]]

        current = store.get_session_by_phone(session.phone)
        is_reply_target = (
            current is not None
            and current.id == session.id
            and session.status == SessionStatus.ACTIVE
            and not is_session_expired(session, now, ttl)
        )

        return _to_detail(
            session,
            previous_sessions=previous,
            is_reply_target=is_reply_target,
        )


async def update_session_tags(
    session_id: str, tags: list[AgencyTag]
) -> SessionDetail | None:
    """Set agency tags on a session (current reply target only)."""
    store = get_store()
    settings = get_settings()
    now = _utc_now()
    ttl = settings.session_ttl_seconds
    unique = list(dict.fromkeys(tags))

    async with store.lock:
        session = store.get_session_by_id(session_id)
        if session is None:
            return None

        session = expire_if_needed(session, store, now)
        current = store.get_session_by_phone(session.phone)
        is_reply_target = (
            current is not None
            and current.id == session.id
            and session.status == SessionStatus.ACTIVE
            and not is_session_expired(session, now, ttl)
        )
        if not is_reply_target:
            return None

        session = session.model_copy(update={"agency_tags": unique})
        store.put_session(session)

        phone_sessions = sorted(
            get_sessions_for_phone(store, session.phone),
            key=session_started_at,
        )
        idx = next((i for i, s in enumerate(phone_sessions) if s.id == session_id), 0)
        previous = [_to_block(s, ttl) for s in phone_sessions[:idx]]

        return _to_detail(
            session,
            previous_sessions=previous,
            is_reply_target=True,
        )
