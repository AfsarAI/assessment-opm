import os
from typing import List, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000

    # PostgreSQL Database configuration
    POSTGRES_USER: str = "opm_user"
    POSTGRES_PASSWORD: str = "opm_password"
    POSTGRES_DB: str = "opm_db"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    # Database URLs
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://opm_user:opm_password@localhost:5432/opm_db",
        description="Async SQLAlchemy database connection URL (asyncpg)",
    )
    SYNC_DATABASE_URL: str = Field(
        default="postgresql+psycopg://opm_user:opm_password@localhost:5432/opm_db",
        description="Synchronous database connection URL for Alembic & Celery (psycopg3)",
    )

    # Redis & Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    # CORS
    CORS_ORIGINS: Union[List[str], str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, str) and v.startswith("["):
            import json
            try:
                return json.loads(v)
            except Exception:
                return ["*"]
        return v

    # File storage
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: int = 250

    # Webhook delivery settings
    WEBHOOK_TIMEOUT_SECONDS: int = 5
    WEBHOOK_MAX_RETRIES: int = 3
    WEBHOOK_BACKOFF_FACTOR: int = 2


settings = Settings()
