"""Outbound idempotency (same session + text + timestamp)."""

from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from middleware.app.main import app
from middleware.app.models import Message, MessageDirection, Session, SessionStatus
from middleware.app.store import reset_store


def test_duplicate_outbound_same_timestamp_is_idempotent():
    reset_store()
    now = datetime.now(timezone.utc)
    session = Session(
        id="idem-session",
        phone="+15550007777",
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
    payload = {"text": "Units are en route.", "timestamp": now.isoformat()}
    with patch(
        "middleware.app.services.outbound.deliver_to_northstar",
        return_value=(True, 1, None),
    ):
        first = client.post(f"/sessions/{session.id}/reply", json=payload)
        second = client.post(f"/sessions/{session.id}/reply", json=payload)

    assert first.status_code == 200
    assert first.json()["success"] is True
    assert first.json()["duplicate"] is False

    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["success"] is True

    detail = client.get(f"/sessions/{session.id}").json()
    outbound = [m for m in detail["messages"] if m["direction"] == "outbound"]
    assert len(outbound) == 1
