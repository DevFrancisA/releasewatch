from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RELEASEWATCH_", env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://releasewatch:releasewatch@localhost:5432/releasewatch",
        validation_alias=AliasChoices("DATABASE_URL", "RELEASEWATCH_DATABASE_URL"),
    )
    # Shared secret used to verify the HMAC signature on incoming CI webhooks.
    webhook_secret: str = "dev-webhook-secret"

    # Post-deployment verification: run `health_check_count` checks spaced
    # `health_check_interval_seconds` apart; the release is marked unhealthy once
    # `health_check_failure_threshold` of them fail.
    health_check_count: int = 3
    health_check_failure_threshold: int = 2
    health_check_initial_delay_seconds: float = 10.0
    health_check_interval_seconds: float = 30.0
    health_check_timeout_seconds: float = 5.0

    worker_poll_interval_seconds: float = 1.0
    worker_stale_job_seconds: float = 300.0
    job_max_attempts: int = 5

    cors_origins: list[str] = ["http://localhost:5173"]
    # Directory containing the built React app; served at / when present.
    static_dir: str | None = None

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        # Hosting providers hand out postgres:// URLs; SQLAlchemy needs the driver named.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
