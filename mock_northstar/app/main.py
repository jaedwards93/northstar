"""Mock Northstar outbound API."""

import logging
from typing import Any

from fastapi import FastAPI, HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mock_northstar")

app = FastAPI(title="Mock Northstar", version="0.1.0")

_attempt_count = 0


@app.post("/messages")
async def receive_message(payload: dict[str, Any], fail_times: int = 0) -> dict[str, bool]:
    """
    Accept outbound SMS from middleware.

    Query `fail_times=N` returns 503 for the first N requests (retry testing).
    """
    global _attempt_count
    _attempt_count += 1
    logger.info("Outbound SMS (attempt %s): %s", _attempt_count, payload)

    if _attempt_count <= fail_times:
        raise HTTPException(status_code=503, detail="Simulated carrier failure")

    return {"accepted": True}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
