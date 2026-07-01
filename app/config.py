"""Application settings, loaded from environment / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "dizzaract"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:8000"

    jwt_secret: str = "change-me-in-prod-use-openssl-rand-hex-32"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1_209_600

    database_url: str = "postgresql+asyncpg://chat:chat@localhost:5432/chat"
    redis_url: str = "redis://localhost:6379/0"

    ai_reply_delay_seconds: float = Field(default=0.3, ge=0)

    # Seconds a freshly-accepted socket has to send its auth frame before we close it.
    ws_auth_timeout_seconds: float = 5.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
