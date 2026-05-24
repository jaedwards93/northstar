"""Shared session expiry rules (middleware + console via /config)."""

from datetime import datetime, timedelta, timezone

# Demo / POC defaults (middleware Settings and harness use these).
SESSION_TTL_SECONDS = 300  # 5 minutes
SESSION_EXPIRING_SOON_SECONDS = 120  # "Expiring Soon" when less than this remains


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def expires_at(last_activity_at: datetime, ttl_seconds: int) -> datetime:
    return as_utc(last_activity_at) + timedelta(seconds=ttl_seconds)


def is_expired(
    *,
    status: str,
    last_activity_at: datetime,
    at: datetime,
    ttl_seconds: int,
) -> bool:
    if status == "expired":
        return True
    elapsed = (as_utc(at) - as_utc(last_activity_at)).total_seconds()
    return elapsed > ttl_seconds
