"""Auth HTTP 合同；只有服务端事务成功后才设置或删除 Cookie。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from agenthub.auth.dependencies import (
    check_origin,
    get_auth_service,
    get_current_session,
    get_current_user,
)
from agenthub.auth.rate_limit import LoginRateLimiter
from agenthub.auth.schemas import CsrfResponse, LoginRequest, UserIdentity
from agenthub.auth.security import CsrfSecurity
from agenthub.auth.service import AuthenticatedSession, AuthService
from agenthub.core.config import Settings
from agenthub.core.errors import AppError, ErrorResponse

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)


def cookie(
    response: Response, settings: Settings, name: str, value: str, ttl: int
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=ttl,
        path="/",
        secure=bool(settings.session_cookie_secure),
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )


def clear_cookie(response: Response, settings: Settings, name: str) -> None:
    response.delete_cookie(
        name,
        path="/",
        secure=bool(settings.session_cookie_secure),
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )


@router.get("/csrf", response_model=CsrfResponse)
async def csrf_credential(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> CsrfResponse:
    # 浏览器同源 GET 通常不带 Origin；要求 Fetch Metadata 和自定义头。
    if not request.headers.getlist("origin"):
        if (
            request.headers.get("sec-fetch-site") != "same-origin"
            or request.headers.get("x-csrf-fetch") != "1"
        ):
            from agenthub.auth.security import csrf_error

            raise csrf_error()
    else:
        check_origin(request)
    settings: Settings = request.app.state.settings
    csrf: CsrfSecurity = request.app.state.csrf
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        try:
            current = await service.authenticate(token)
        except AppError as exc:
            if exc.status_code != 401:
                raise
        else:
            return CsrfResponse(
                csrf_token=csrf.issue(current.session_id), purpose="session"
            )
    credential = csrf.issue()
    cookie(
        response,
        settings,
        settings.login_csrf_cookie_name,
        credential,
        settings.login_csrf_ttl_seconds,
    )
    return CsrfResponse(csrf_token=credential, purpose="login")


@router.post(
    "/login", response_model=UserIdentity, responses={429: {"model": ErrorResponse}}
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserIdentity:
    limiter: LoginRateLimiter = request.app.state.login_limiter
    # 只使用 ASGI 连接地址。Uvicorn 必须使用 --no-proxy-headers 启动。
    await limiter.check(
        request.client.host if request.client else "unknown", payload.login_name
    )
    identity, token = await service.login(
        payload.login_name, payload.password.get_secret_value()
    )
    settings: Settings = request.app.state.settings
    cookie(
        response,
        settings,
        settings.session_cookie_name,
        token,
        settings.session_ttl_seconds,
    )
    clear_cookie(response, settings, settings.login_csrf_cookie_name)
    return identity


@router.get("/me", response_model=UserIdentity)
async def me(user: Annotated[UserIdentity, Depends(get_current_user)]) -> UserIdentity:
    return user


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    current: Annotated[AuthenticatedSession, Depends(get_current_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    await service.logout(current)
    settings: Settings = request.app.state.settings
    clear_cookie(response, settings, settings.session_cookie_name)
    clear_cookie(response, settings, settings.login_csrf_cookie_name)
