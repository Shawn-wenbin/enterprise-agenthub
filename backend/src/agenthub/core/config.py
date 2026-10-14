"""通过统一且可缓存的入口加载具有类型约束的配置。"""

import ipaddress
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
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

    session_ttl_seconds: int = Field(
        default=28800, ge=60, le=604800, validation_alias="SESSION_TTL_SECONDS"
    )
    session_cookie_name: str = Field(default="", validation_alias="SESSION_COOKIE_NAME")
    session_cookie_secure: bool | None = Field(
        default=None, validation_alias="SESSION_COOKIE_SECURE"
    )
    session_cookie_httponly: bool = Field(
        default=True, validation_alias="SESSION_COOKIE_HTTPONLY"
    )
    session_cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax", validation_alias="SESSION_COOKIE_SAMESITE"
    )
    trusted_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
        validation_alias="TRUSTED_ORIGINS",
    )
    csrf_secret: SecretStr | None = Field(default=None, validation_alias="CSRF_SECRET")
    login_csrf_ttl_seconds: int = Field(
        default=600, ge=30, le=1800, validation_alias="LOGIN_CSRF_TTL_SECONDS"
    )
    password_concurrency: int = Field(
        default=2, ge=1, le=8, validation_alias="PASSWORD_CONCURRENCY"
    )
    login_rate_window_seconds: int = Field(
        default=300, ge=1, le=3600, validation_alias="LOGIN_RATE_WINDOW_SECONDS"
    )
    login_rate_source_limit: int = Field(
        default=60, ge=1, le=1000, validation_alias="LOGIN_RATE_SOURCE_LIMIT"
    )
    login_rate_account_limit: int = Field(
        default=10, ge=1, le=100, validation_alias="LOGIN_RATE_ACCOUNT_LIMIT"
    )
    login_rate_namespace: str = Field(
        default="agenthub:login:v1",
        pattern=r"^[a-zA-Z0-9:_-]{1,128}$",
        validation_alias="LOGIN_RATE_NAMESPACE",
    )
    demo_seed_passwords: dict[str, SecretStr] = Field(
        default_factory=dict, validation_alias="DEMO_SEED_PASSWORDS", repr=False
    )

    @property
    def login_csrf_cookie_name(self) -> str:
        return self.session_cookie_name + "_login_csrf"

    @model_validator(mode="after")
    def validate_auth_security(self) -> "Settings":
        if not self.session_cookie_httponly:
            raise ValueError("Session cookies require HttpOnly")
        production = self.env == "production"
        if not self.session_cookie_name:
            self.session_cookie_name = (
                "__Host-agenthub_session" if production else "agenthub_session"
            )
        if self.session_cookie_secure is None:
            self.session_cookie_secure = production
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.session_cookie_name):
            raise ValueError("Invalid session cookie name")
        if (
            self.session_cookie_name.startswith("__Host-")
            and not self.session_cookie_secure
        ):
            raise ValueError("Host cookies require Secure")
        if self.session_cookie_samesite == "none" and not self.session_cookie_secure:
            raise ValueError("SameSite=None requires Secure")
        if not self.trusted_origins:
            raise ValueError("At least one trusted origin is required")
        for origin in self.trusted_origins:
            try:
                url = urlsplit(origin)
                valid = (
                    url.scheme in {"http", "https"}
                    and url.hostname
                    and not url.username
                    and not url.password
                    and not url.path
                    and not url.query
                    and not url.fragment
                    and url.port != 0
                )
                if (
                    not valid
                    or url.hostname is None
                    or origin != origin.lower()
                    or "*" in origin
                    or origin.endswith(":")
                ):
                    raise ValueError()
                try:
                    address = ipaddress.ip_address(url.hostname)
                except ValueError:
                    address = None
                    if not re.fullmatch(
                        r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", url.hostname
                    ):
                        raise ValueError() from None
                local = (
                    url.hostname == "localhost"
                    or url.hostname.endswith(".localhost")
                    or (address is not None and address.is_loopback)
                )
                if not self.session_cookie_secure and not local:
                    raise ValueError()
                if production:
                    if (
                        url.scheme != "https"
                        or url.hostname in {"localhost", "testserver"}
                        or url.hostname.endswith(".localhost")
                    ):
                        raise ValueError()
                    try:
                        address = ipaddress.ip_address(url.hostname)
                    except ValueError:
                        pass
                    else:
                        if address.is_loopback or address.is_unspecified:
                            raise ValueError()
            except ValueError:
                raise ValueError(
                    "Trusted origins require exact safe scheme/host/port"
                ) from None
        if self.csrf_secret is not None:
            secret = self.csrf_secret.get_secret_value()
            if not re.fullmatch(r"[0-9a-f]{64}", secret) or len(set(secret)) < 8:
                raise ValueError(
                    "CSRF_SECRET requires 32 random bytes encoded as lowercase hex"
                )
        if production:
            if (
                not self.session_cookie_secure
                or not self.session_cookie_name.startswith("__Host-")
                or self.csrf_secret is None
            ):
                raise ValueError(
                    "Production requires Secure host cookie and explicit CSRF_SECRET"
                )
            for value in (self.database_url, self.redis_url):
                password = make_url(value.get_secret_value()).password or ""
                if len(password) < 16 or password.lower() in {
                    "replace_with_local_password",
                    "development-password",
                    "change-me-in-production",
                }:
                    raise ValueError(
                        "Production dependencies require non-default credentials"
                    )
        return self

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
