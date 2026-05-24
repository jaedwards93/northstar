"""Failed outbound delivery is persisted on the session message."""

from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from middleware.app.main import app
from middleware.app.models import Message, MessageDirection, Session, SessionStatus
from middleware.app.store import reset_store


def test_failed_outbound_persisted_on_message():
    reset_store()
    now = datetime.now(timezone.utc)
    session = Session(
        id="fail-session",
        phone="+15550008888",
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
    with patch(
        "middleware.app.services.outbound.deliver_to_northstar",
        return_value=(False, 4, "Northstar error (HTTP 500)"),
    ):
        res = client.post(
            f"/sessions/{session.id}/reply",
            json={"text": "POLICE en route — FORCE_FAIL"},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is False
    assert body["delivery_attempts"] == 4
    assert "500" in (body["error"] or "")

    detail = client.get(f"/sessions/{session.id}").json()
    outbound = [m for m in detail["messages"] if m["direction"] == "outbound"]
    assert len(outbound) == 1
    assert outbound[0]["delivery_status"] == "failed"
    assert outbound[0]["delivery_attempts"] == 4
    assert outbound[0]["delivery_error"]
    assert detail["outbound_delivery_failure"]
    assert "500" in detail["outbound_delivery_failure"]
    assert detail["latest_outbound_delivery_status"] == "failed"
