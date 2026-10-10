"""通过统一且可缓存的入口加载具有类型约束的配置。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        populate_by_name=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    env: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="ENV"
    )
    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    redis_url: SecretStr = Field(validation_alias="REDIS_URL")
    db_pool_size: int = Field(default=5, ge=1, le=20, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(
        default=5, ge=0, le=20, validation_alias="DB_MAX_OVERFLOW"
    )
    dependency_timeout_seconds: float = Field(
        default=2.0, gt=0, le=30, validation_alias="DEPENDENCY_TIMEOUT_SECONDS"
    )
    artifact_store: Literal["local"] = Field(
        default="local", validation_alias="ARTIFACT_STORE"
    )
    artifact_root: Path = Field(
        default=Path(".data/artifacts"), validation_alias="ARTIFACT_ROOT"
    )
    artifact_max_bytes: int = Field(
        default=16 * 1024 * 1024,
        gt=0,
        le=1024 * 1024 * 1024,
        validation_alias="ARTIFACT_MAX_BYTES",
    )
    artifact_pending_seconds: int = Field(
        default=300, ge=1, validation_alias="ARTIFACT_PENDING_SECONDS"
    )

    @field_validator("database_url", "redis_url")
    @classmethod
    def validate_connection_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except Exception:
            raise ValueError("Invalid dependency URL") from None
        if url.drivername not in {"postgresql+asyncpg", "redis", "rediss"}:
            raise ValueError("Unsupported dependency URL scheme")
        if not url.host:
            raise ValueError("Dependency URL requires an explicit host")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        url = make_url(value.get_secret_value())
        if url.drivername != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL requires postgresql+asyncpg")
        if not url.database:
            raise ValueError("DATABASE_URL requires an explicit database")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        if make_url(value.get_secret_value()).drivername not in {"redis", "rediss"}:
            raise ValueError("REDIS_URL requires redis or rediss")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # 必填字段来自环境变量。
