"""Mock Northstar outbound API."""

import logging
from typing import Any

from fastapi import FastAPI, HTTPException

logger = logging.getLogger(__name__)

app = FastAPI(title="Mock Northstar", version="0.1.0")


@app.post("/messages")
async def receive_message(payload: dict[str, Any]) -> dict[str, bool]:
    """
    Accept outbound SMS from middleware.

    Text containing ``FORCE_FAIL`` returns HTTP 500 (harness / deterministic tests).
    """
    logger.info("Outbound SMS: %s", payload)

    text = str(payload.get("text", ""))
    if "FORCE_FAIL" in text:
        raise HTTPException(status_code=500, detail="Simulated carrier failure (FORCE_FAIL)")

    return {"accepted": True}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
