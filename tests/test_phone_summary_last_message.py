"""Phone summary includes last message direction for sidebar read state."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from middleware.app.main import app
from middleware.app.models import Message, MessageDirection, Session, SessionStatus
from middleware.app.store import reset_store


def test_phone_summary_last_message_outbound():
    reset_store()
    now = datetime.now(timezone.utc)
    session = Session(
        id="out-last",
        phone="+15550004444",
        status=SessionStatus.ACTIVE,
        last_activity_at=now,
        messages=[
            Message(
                id="m1",
                direction=MessageDirection.INBOUND,
                text="help",
                timestamp=now,
            ),
            Message(
                id="m2",
                direction=MessageDirection.OUTBOUND,
                text="On the way",
                timestamp=now,
            ),
        ],
    )
    store = reset_store()
    store.put_session(session)

    row = TestClient(app).get("/sessions?group_by_phone=true").json()[0]
    assert row["last_message_direction"] == "outbound"
