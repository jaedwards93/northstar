"""In-memory storage (POC only — state is lost on restart)."""

import asyncio
from dataclasses import dataclass, field

from middleware.app.models import Session


@dataclass(frozen=True)
class InboundProcessResult:
    """Stored so duplicate inbound deliveries return the same outcome."""

    session_id: str
    internal_message_id: str


@dataclass(frozen=True)
class OutboundProcessResult:
    """Stored so duplicate outbound deliveries return the same outcome."""

    session_id: str
    internal_message_id: str
    success: bool
    error: str | None
    delivery_attempts: int


@dataclass
class InMemoryStore:
    sessions_by_phone: dict[str, Session] = field(default_factory=dict)
    sessions_by_id: dict[str, Session] = field(default_factory=dict)
    seen_inbound: dict[str, InboundProcessResult] = field(default_factory=dict)
    seen_outbound: dict[str, OutboundProcessResult] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def get_session_by_phone(self, phone: str) -> Session | None:
        return self.sessions_by_phone.get(phone)

    def get_session_by_id(self, session_id: str) -> Session | None:
        return self.sessions_by_id.get(session_id)

    def put_session(self, session: Session) -> None:
        self.sessions_by_id[session.id] = session
        current = self.sessions_by_phone.get(session.phone)
        if current is None or current.id == session.id:
            self.sessions_by_phone[session.phone] = session

    def list_sessions(self) -> list[Session]:
        """Current session per phone (inbound/reply target)."""
        return list(self.sessions_by_phone.values())

    def list_all_sessions(self) -> list[Session]:
        """Every stored session, including superseded expired ones."""
        return list(self.sessions_by_id.values())

    def get_inbound_result(self, idempotency_key: str) -> InboundProcessResult | None:
        return self.seen_inbound.get(idempotency_key)

    def record_inbound_result(
        self, idempotency_key: str, result: InboundProcessResult
    ) -> None:
        self.seen_inbound[idempotency_key] = result

    def get_outbound_result(self, idempotency_key: str) -> OutboundProcessResult | None:
        return self.seen_outbound.get(idempotency_key)

    def record_outbound_result(
        self, idempotency_key: str, result: OutboundProcessResult
    ) -> None:
        self.seen_outbound[idempotency_key] = result

    def clear(self) -> None:
        self.sessions_by_phone.clear()
        self.sessions_by_id.clear()
        self.seen_inbound.clear()
        self.seen_outbound.clear()


_store: InMemoryStore | None = None


def get_store() -> InMemoryStore:
    global _store
    if _store is None:
        _store = InMemoryStore()
    return _store


def reset_store() -> InMemoryStore:
    """Replace the global store (tests). Returns the new instance."""
    global _store
    _store = InMemoryStore()
    return _store
