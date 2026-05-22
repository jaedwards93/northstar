"""Inbound should attach rapid messages to one session per phone."""

import asyncio
from datetime import datetime, timezone

from middleware.app.services.inbound import handle_inbound
from middleware.app.models import InboundWebhookRequest
from middleware.app.store import reset_store


def _payload(text: str, phone: str = "+15550009999") -> InboundWebhookRequest:
    return InboundWebhookRequest.model_validate(
        {
            "from": phone,
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


def test_concurrent_inbound_same_phone_one_session():
    reset_store()

    async def run() -> list[str]:
        results = await asyncio.gather(
            handle_inbound(_payload("first")),
            handle_inbound(_payload("second")),
        )
        return [r.session_id for r in results]

    session_ids = asyncio.run(run())
    assert session_ids[0] == session_ids[1]
