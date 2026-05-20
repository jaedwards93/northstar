"""Northstar inbound routes (POST /inbound)."""

from fastapi import APIRouter

from middleware.app.models import InboundWebhookRequest
from middleware.app.services.inbound import handle_inbound

router = APIRouter(tags=["inbound"])


@router.post("/inbound")
async def inbound(payload: InboundWebhookRequest) -> dict:
    result = await handle_inbound(payload)
    return {
        "session_id": result.session_id,
        "message_id": result.message_id,
        "duplicate": result.duplicate,
    }
