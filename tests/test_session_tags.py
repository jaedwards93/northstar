"""Session agency tags."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from middleware.app.main import app
from middleware.app.models import Message, MessageDirection, Session, SessionStatus
from middleware.app.store import reset_store


def test_patch_session_tags():
    reset_store()
    now = datetime.now(timezone.utc)
    session = Session(
        id="tag-session",
        phone="+15550005555",
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
    res = client.patch(
        "/sessions/tag-session/tags",
        json={"tags": ["fire", "medical"]},
    )
    assert res.status_code == 200
    assert set(res.json()["agency_tags"]) == {"fire", "medical"}

    listed = client.get("/sessions?group_by_phone=true").json()[0]
    assert set(listed["agency_tags"]) == {"fire", "medical"}
