"""Shared session expiry and UI status rules (middleware + console via /config)."""

from datetime import datetime, timedelta, timezone
from enum import StrEnum


class UiSessionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRING = "expiring"
    EXPIRED = "expired"


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


def ui_status(
    *,
    status: str,
    last_activity_at: datetime,
    at: datetime,
    ttl_seconds: int,
    expiring_soon_seconds: int,
) -> UiSessionStatus:
    if is_expired(
        status=status,
        last_activity_at=last_activity_at,
        at=at,
        ttl_seconds=ttl_seconds,
    ):
        return UiSessionStatus.EXPIRED
    remaining = (expires_at(last_activity_at, ttl_seconds) - as_utc(at)).total_seconds()
    if remaining < expiring_soon_seconds:
        return UiSessionStatus.EXPIRING
    return UiSessionStatus.ACTIVE
