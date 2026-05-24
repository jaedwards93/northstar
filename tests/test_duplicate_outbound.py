"""Outbound dedup: idempotent replay only (same session + text + timestamp)."""

from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from middleware.app.main import app
from middleware.app.models import Message, MessageDirection, Session, SessionStatus
from middleware.app.store import reset_store


def test_same_text_new_timestamp_creates_second_outbound():
    reset_store()
    now = datetime.now(timezone.utc)
    session = Session(
        id="dup-session",
        phone="+15550003333",
        status=SessionStatus.ACTIVE,
        last_activity_at=now,
        messages=[
            Message(
                id="m1",
                direction=MessageDirection.INBOUND,
                text="help",
                timestamp=now,
            )
        ],
    )
    store = reset_store()
    store.put_session(session)

    client = TestClient(app)
    text = "Please send your address."
    ts1 = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 5, 22, 12, 0, 1, tzinfo=timezone.utc)
    with patch(
        "middleware.app.services.outbound.deliver_to_northstar",
        return_value=(True, 1, None),
    ):
        first = client.post(
            f"/sessions/{session.id}/reply",
            json={"text": text, "timestamp": ts1.isoformat()},
        )
        second = client.post(
            f"/sessions/{session.id}/reply",
            json={"text": text, "timestamp": ts2.isoformat()},
        )

    assert first.status_code == 200
    assert first.json()["success"] is True
    assert first.json()["duplicate"] is False

    assert second.status_code == 200
    assert second.json()["success"] is True
    assert second.json()["duplicate"] is False

    detail = client.get(f"/sessions/{session.id}").json()
    outbound = [m for m in detail["messages"] if m["direction"] == "outbound"]
    assert len(outbound) == 2
