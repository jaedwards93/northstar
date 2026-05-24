"""Outbound retry uses 0.5s, 1s, 2s backoff (3.5s total sleep before giving up)."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from middleware.app.models import Message, MessageDirection, Session, SessionStatus
from middleware.app.services.outbound import deliver_to_northstar
from middleware.app.store import reset_store


def test_outbound_retry_backoff_schedule():
    reset_store()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    mock_response = AsyncMock()
    mock_response.is_success = False
    mock_response.status_code = 503

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post.return_value = mock_response

    with (
        patch("middleware.app.services.outbound.asyncio.sleep", fake_sleep),
        patch("middleware.app.services.outbound.httpx.AsyncClient", return_value=mock_client),
    ):
        success, attempts, _ = asyncio.run(
            deliver_to_northstar(
                to_number="+15550001111",
                from_number="+1911",
                text="test",
                session_id="sid",
            )
        )

    assert success is False
    assert attempts == 4
    assert sleeps == [0.5, 1.0, 2.0]
