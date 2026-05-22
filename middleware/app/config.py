"""Application settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Middleware configuration (env vars optional; defaults for local POC)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    session_ttl_seconds: int = 300  # 5 minutes
    session_expiring_soon_seconds: int = 120  # yellow state when less than this remains
    outbound_max_retries: int = 3
    outbound_retry_backoff_seconds: float = 0.5
    northstar_outbound_url: str = "http://127.0.0.1:8001/messages"
    reply_from_number: str = "+1911"


@lru_cache
def get_settings() -> Settings:
    return Settings()
