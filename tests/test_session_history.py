"""Session history and store behavior for same phone number."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from middleware.app.main import app
from middleware.app.models import (
    AgencyTag,
    Message,
    MessageDirection,
    Session,
    SessionStatus,
)
from middleware.app.store import reset_store


def test_previous_sessions_and_reply_target():
    store = reset_store()
    now = datetime.now(timezone.utc)
    old = Session(
        id="old-session",
        phone="+15550001111",
        status=SessionStatus.EXPIRED,
        last_activity_at=now - timedelta(hours=1),
        agency_tags=[AgencyTag.FIRE, AgencyTag.MEDICAL],
        messages=[
            Message(
                id="m1",
                direction=MessageDirection.INBOUND,
                text="old help",
                timestamp=now - timedelta(hours=1),
            )
        ],
    )
    current = Session(
        id="new-session",
        phone="+15550001111",
        status=SessionStatus.ACTIVE,
        last_activity_at=now,
        messages=[
            Message(
                id="m2",
                direction=MessageDirection.INBOUND,
                text="new help",
                timestamp=now,
            )
        ],
    )
    store.put_session(old)
    store.put_session(current)

    client = TestClient(app)
    grouped = client.get("/sessions?group_by_phone=true")
    assert grouped.status_code == 200
    assert len(grouped.json()) == 1
    row = grouped.json()[0]
    assert row["current_session_id"] == "new-session"
    assert row["last_inbound_at"] is not None

    res = client.get("/sessions/new-session")
    assert res.status_code == 200
    body = res.json()
    assert body["is_reply_target"] is True
    assert len(body["previous_sessions"]) == 1
    assert body["previous_sessions"][0]["id"] == "old-session"
    assert set(body["previous_sessions"][0]["agency_tags"]) == {"fire", "medical"}
    assert body["messages"][0]["text"] == "new help"

    old_detail = client.get("/sessions/old-session")
    assert old_detail.status_code == 200
    assert old_detail.json()["is_reply_target"] is False
    assert old_detail.json()["previous_sessions"] == []


def test_expire_old_session_does_not_replace_phone_pointer():
    store = reset_store()
    now = datetime.now(timezone.utc)
    old = Session(
        id="old-session",
        phone="+15550002222",
        status=SessionStatus.ACTIVE,
        last_activity_at=now - timedelta(minutes=10),
        messages=[],
    )
    current = Session(
        id="new-session",
        phone="+15550002222",
        status=SessionStatus.ACTIVE,
        last_activity_at=now,
        messages=[],
    )
    store.put_session(old)
    store.put_session(current)

    from middleware.app.services.sessions import expire_if_needed

    expire_if_needed(old, store, now)

    assert store.get_session_by_phone("+15550002222").id == "new-session"
