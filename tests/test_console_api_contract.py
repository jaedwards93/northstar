"""API contract used by the agent console (console/app.js).

The console is not executed in pytest; these tests lock the middleware responses
the UI polls and renders.
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from middleware.app.main import app
from middleware.app.models import Message, MessageDirection, Session, SessionStatus
from middleware.app.store import reset_store
from shared.session_policy import (
    SESSION_EXPIRING_SOON_SECONDS,
    SESSION_TTL_SECONDS,
)


def test_config_matches_session_policy_defaults():
    client = TestClient(app)
    res = client.get("/config")
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "session_ttl_seconds": SESSION_TTL_SECONDS,
        "session_expiring_soon_seconds": SESSION_EXPIRING_SOON_SECONDS,
    }


def test_grouped_sessions_row_shape_for_sidebar():
    store = reset_store()
    now = datetime.now(timezone.utc)
    session = Session(
        id="console-sidebar",
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
    store.put_session(session)

    row = TestClient(app).get("/sessions?group_by_phone=true").json()[0]
    assert row["from"] == "+15550007777"
    assert row["current_session_id"] == "console-sidebar"
    assert "status" in row
    assert "preview" in row
    assert "timestamp" in row
    assert "last_inbound_at" in row
    assert "last_activity_at" in row
    assert "last_message_direction" in row
    assert "agency_tags" in row


def test_session_detail_fields_for_conversation_panel():
    store = reset_store()
    now = datetime.now(timezone.utc)
    session = Session(
        id="console-detail",
        phone="+15550006666",
        status=SessionStatus.ACTIVE,
        last_activity_at=now,
        messages=[
            Message(
                id="m1",
                direction=MessageDirection.INBOUND,
                text="911",
                timestamp=now,
            )
        ],
    )
    store.put_session(session)

    body = TestClient(app).get("/sessions/console-detail").json()
    assert body["from"] == "+15550006666"
    assert body["is_reply_target"] is True
    assert isinstance(body["messages"], list)
    assert isinstance(body["previous_sessions"], list)
    assert "agency_tags" in body
    assert "latest_outbound_delivery_status" in body
    assert "outbound_delivery_failure" in body


def test_reply_409_matches_console_session_expired_handling():
    store = reset_store()
    now = datetime.now(timezone.utc)
    session = Session(
        id="expired-reply",
        phone="+15550005555",
        status=SessionStatus.EXPIRED,
        last_activity_at=now,
        messages=[],
    )
    store.put_session(session)

    res = TestClient(app).post(
        "/sessions/expired-reply/reply",
        json={"text": "too late"},
    )
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "SESSION_EXPIRED"
    assert "message" in detail
