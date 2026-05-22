"""Phone list exposes last inbound timestamp for unread tracking."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from middleware.app.main import app
from middleware.app.models import Message, MessageDirection, Session, SessionStatus
from middleware.app.services.sessions import last_inbound_at
from middleware.app.store import reset_store


def test_last_inbound_at_ignores_agent_messages():
    now = datetime.now(timezone.utc)
    session = Session(
        id="s1",
        phone="+15550003333",
        status=SessionStatus.ACTIVE,
        last_activity_at=now,
        messages=[
            Message(
                id="m1",
                direction=MessageDirection.INBOUND,
                text="help",
                timestamp=now - timedelta(minutes=5),
            ),
            Message(
                id="m2",
                direction=MessageDirection.OUTBOUND,
                text="on it",
                timestamp=now,
            ),
        ],
    )
    assert last_inbound_at(session) == now - timedelta(minutes=5)


def test_phone_summary_last_inbound_at():
    reset_store()
    now = datetime.now(timezone.utc)
    session = Session(
        id="s1",
        phone="+15550004444",
        status=SessionStatus.ACTIVE,
        last_activity_at=now,
        messages=[
            Message(
                id="m1",
                direction=MessageDirection.INBOUND,
                text="911",
                timestamp=now - timedelta(seconds=30),
            ),
            Message(
                id="m2",
                direction=MessageDirection.OUTBOUND,
                text="received",
                timestamp=now,
            ),
        ],
    )
    store = reset_store()
    store.put_session(session)

    client = TestClient(app)
    row = client.get("/sessions?group_by_phone=true").json()[0]
    assert row["last_inbound_at"] is not None
    assert "received" in row["preview"]
