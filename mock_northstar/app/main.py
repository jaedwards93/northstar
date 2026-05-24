"""Mock Northstar outbound API."""

import logging
import os
import random
from typing import Any

from fastapi import FastAPI, HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mock_northstar")

app = FastAPI(title="Mock Northstar", version="0.1.0")

_attempt_count = 0
_random_fail_rate = float(os.environ.get("MOCK_RANDOM_FAIL_RATE", "0"))


@app.post("/messages")
async def receive_message(payload: dict[str, Any], fail_times: int = 0) -> dict[str, bool]:
    """
    Accept outbound SMS from middleware.

    - Text containing ``FORCE_FAIL`` always returns HTTP 500 (harness / deterministic tests).
    - Query ``fail_times=N`` returns 503 for the first N requests (manual retry testing).
    - Optional env ``MOCK_RANDOM_FAIL_RATE`` (0.0–1.0) for ad-hoc manual chaos only.
    """
    global _attempt_count
    _attempt_count += 1
    logger.info("Outbound SMS (attempt %s): %s", _attempt_count, payload)

    text = str(payload.get("text", ""))
    if "FORCE_FAIL" in text:
        raise HTTPException(status_code=500, detail="Simulated carrier failure (FORCE_FAIL)")

    if _attempt_count <= fail_times:
        raise HTTPException(status_code=503, detail="Simulated carrier failure")

    if _random_fail_rate > 0 and random.random() < _random_fail_rate:
        raise HTTPException(status_code=503, detail="Simulated random carrier failure")

    return {"accepted": True}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
