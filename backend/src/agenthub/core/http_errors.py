"""HTTP adapter preserving validation without echoing sensitive request inputs."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from agenthub.core.errors import AppError, ErrorResponse
from agenthub.core.request_context import log_event


def install_error_handlers(app: FastAPI) -> None:
    def response(request: Request, error: AppError) -> JSONResponse:
        log_event("request_error", code=error.code)
        return JSONResponse(
            ErrorResponse(
                code=error.code,
                message=error.message,
                request_id=request.state.request_id,
                details=error.details,
            ).model_dump(),
            status_code=error.status_code,
        )

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # Keep field locations/types, omit input, arbitrary messages and ctx.
        return response(
            request,
            AppError(
                "validation_error",
                "Request validation failed",
                422,
                {
                    "errors": [
                        {"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()
                    ]
                },
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        result = response(
            request,
            AppError(
                "http_error",
                "HTTP request failed",
                exc.status_code,
            ),
        )
        # Preserve protocol headers, but correlation is always server-controlled.
        if exc.headers:
            result.headers.update(exc.headers)
        return result

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(RedisError)
    async def dependency_error(
        request: Request, exc: SQLAlchemyError | RedisError
    ) -> JSONResponse:
        return response(
            request,
            AppError(
                "dependency_unavailable",
                "Service dependency is unavailable",
                503,
            ),
        )
