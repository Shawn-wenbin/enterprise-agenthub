"""装配 HTTP 应用，不建立外部连接。"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from agenthub.api.health import router as health_router
from agenthub.api.router import router as api_router
from agenthub.artifacts.local import LocalFileArtifactStore
from agenthub.artifacts.service import ArtifactService
from agenthub.auth.body_limit import LoginBodyLimitMiddleware
from agenthub.auth.rate_limit import LoginRateLimiter
from agenthub.auth.security import CsrfSecurity, PasswordSecurity
from agenthub.auth.service import AuthService
from agenthub.authorization.service import AuthorizationService
from agenthub.core.config import Settings, get_settings
from agenthub.core.http_errors import install_error_handlers
from agenthub.core.request_context import RequestContextMiddleware, configure_logging
from agenthub.db.database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        current = settings if settings is not None else get_settings()
        current = Settings.model_validate(current.model_dump())
        application.state.settings = current
        database = Database(current)
        redis = Redis.from_url(
            current.redis_url.get_secret_value(),
            socket_connect_timeout=current.dependency_timeout_seconds,
            socket_timeout=current.dependency_timeout_seconds,
            retry_on_timeout=False,
        )
        store = LocalFileArtifactStore(
            current.artifact_root, current.artifact_max_bytes
        )
        application.state.database = database
        application.state.authorization = AuthorizationService(database)
        application.state.redis = redis
        application.state.artifacts = ArtifactService(
            database,
            store,
            current.artifact_max_bytes,
            current.artifact_pending_seconds,
        )
        try:
            await store.open()
            passwords = PasswordSecurity(current.password_concurrency)
            await passwords.initialize()
            application.state.auth = AuthService(
                database, passwords, current.session_ttl_seconds
            )
            application.state.csrf = CsrfSecurity(
                current.csrf_secret.get_secret_value()
                if current.csrf_secret
                else secrets.token_hex(32),
                current.session_ttl_seconds,
                current.login_csrf_ttl_seconds,
            )
            application.state.login_limiter = LoginRateLimiter(
                redis,
                current.login_rate_window_seconds,
                current.login_rate_source_limit,
                current.login_rate_account_limit,
                current.login_rate_namespace,
            )
            yield
        finally:
            try:
                await store.close()
            finally:
                try:
                    await redis.aclose()
                finally:
                    await database.close()

    application = FastAPI(title="Enterprise AgentHub", lifespan=lifespan)
    application.add_middleware(LoginBodyLimitMiddleware)
    application.add_middleware(RequestContextMiddleware)
    install_error_handlers(application)
    application.include_router(health_router)
    application.include_router(api_router)
    return application


app = create_app()
