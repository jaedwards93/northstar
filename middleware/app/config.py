"""Application settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.session_policy import (
    SESSION_EXPIRING_SOON_SECONDS,
    SESSION_TTL_SECONDS,
)


class Settings(BaseSettings):
    """Middleware configuration (env vars optional; defaults from session_policy)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    session_ttl_seconds: int = SESSION_TTL_SECONDS
    session_expiring_soon_seconds: int = SESSION_EXPIRING_SOON_SECONDS
    outbound_max_retries: int = 4  # initial try + 3 retries
    outbound_retry_backoffs_seconds: tuple[float, float, float] = (0.5, 1.0, 2.0)
    northstar_outbound_url: str = "http://127.0.0.1:8001/messages"
    reply_from_number: str = "+1911"


@lru_cache
def get_settings() -> Settings:
    return Settings()
