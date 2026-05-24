"""Mock Northstar outbound API."""

from fastapi.testclient import TestClient

from mock_northstar.app.main import app

client = TestClient(app)

_PAYLOAD = {
    "to": "+15550001111",
    "from": "+1911",
    "text": "911 Dispatch: Units en route.",
    "session_id": "test-session-id",
}


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_messages_accepts_outbound():
    res = client.post("/messages", json=_PAYLOAD)
    assert res.status_code == 200
    assert res.json() == {"accepted": True}


def test_messages_force_fail_returns_500():
    res = client.post(
        "/messages",
        json={**_PAYLOAD, "text": "POLICE en route — FORCE_FAIL"},
    )
    assert res.status_code == 500
    assert "FORCE_FAIL" in res.json()["detail"]
