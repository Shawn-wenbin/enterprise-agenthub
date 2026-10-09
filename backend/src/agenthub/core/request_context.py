"""Server-generated correlation IDs and sanitized structured request logs."""

import json
import logging
import time
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agenthub.core.errors import ErrorResponse

request_id_context: ContextVar[str] = ContextVar("request_id", default="")
logger = logging.getLogger("agenthub")


def configure_logging() -> None:
    """Emit JSON lines in real API processes, with idempotent handler setup."""
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def log_event(event: str, **fields: Any) -> None:
    logger.info(
        json.dumps({"event": event, "request_id": request_id_context.get(), **fields})
    )


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = f"req_{uuid4().hex}"
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        started = False
        status = 500
        began = time.monotonic()

        async def correlated_send(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"x-request-id"
                ]
                message["headers"] = headers + [(b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, correlated_send)
        except Exception as exc:
            # No exception text/stack: these can contain secrets, URLs or raw input.
            log_event("request_failed", exception_type=type(exc).__name__)
            if started:
                raise
            from starlette.responses import JSONResponse

            response = JSONResponse(
                ErrorResponse(
                    code="internal_error",
                    message="Internal server error",
                    request_id=request_id,
                ).model_dump(),
                status_code=500,
            )
            await response(scope, receive, correlated_send)
        finally:
            log_event(
                "request_completed",
                status=status,
                duration_ms=round((time.monotonic() - began) * 1000, 2),
            )
            request_id_context.reset(token)
