"""进程健康检查，不依赖外部服务。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from agenthub.core.errors import AppError, ErrorResponse
from agenthub.db.database import Database

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


class ReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"
    dependencies: dict[str, Literal["ok"]]


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ErrorResponse}},
)
async def readiness(request: Request) -> ReadinessResponse:
    database: Database = request.app.state.database
    redis: Redis = request.app.state.redis
    timeout: float = request.app.state.settings.dependency_timeout_seconds

    async def check_database() -> None:
        async with database.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def check_redis() -> None:
        await redis.ping()

    async def check(operation: Callable[[], Awaitable[None]]) -> str:
        try:
            async with asyncio.timeout(timeout):
                await operation()
            return "ok"
        except Exception:
            return "unavailable"

    db_state, redis_state = await asyncio.gather(
        check(check_database), check(check_redis)
    )
    if db_state != "ok" or redis_state != "ok":
        raise AppError(
            "dependency_unavailable",
            "Service dependency is unavailable",
            503,
            {"dependencies": {"postgresql": db_state, "redis": redis_state}},
        )
    return ReadinessResponse(dependencies={"postgresql": "ok", "redis": "ok"})
