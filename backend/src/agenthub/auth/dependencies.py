"""可覆盖认证依赖与统一 API 修改请求防护。"""

from typing import Annotated

from fastapi import Depends, Request

from agenthub.auth.schemas import UserIdentity
from agenthub.auth.security import CsrfSecurity, csrf_error, unauthorized
from agenthub.auth.service import AuthenticatedSession, AuthService
from agenthub.core.config import Settings
from agenthub.core.errors import AppError


def get_auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth
    return service


async def get_current_session(
    request: Request, service: Annotated[AuthService, Depends(get_auth_service)]
) -> AuthenticatedSession:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    if token is None:
        raise unauthorized()
    return await service.authenticate(token)


async def get_current_user(
    current: Annotated[AuthenticatedSession, Depends(get_current_session)],
) -> UserIdentity:
    return current.user


def check_origin(request: Request) -> None:
    settings: Settings = request.app.state.settings
    # 不从 Host、Referer、Forwarded 推断可信 Origin；缺失也拒绝。
    origins = request.headers.getlist("origin")
    if len(origins) != 1 or origins[0] not in settings.trusted_origins:
        raise csrf_error()


async def protect_unsafe_request(
    request: Request, service: Annotated[AuthService, Depends(get_auth_service)]
) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    check_origin(request)
    headers = request.headers.getlist("x-csrf-token")
    if len(headers) != 1:
        raise csrf_error()
    csrf: CsrfSecurity = request.app.state.csrf
    settings: Settings = request.app.state.settings
    if request.url.path == "/api/v1/auth/login":
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            raise csrf_error()
        # 登录前用限时签名 double-submit；已登录时可用当前会话绑定签名。
        try:
            csrf.verify(
                headers[0], cookie=request.cookies.get(settings.login_csrf_cookie_name)
            )
            return
        except AppError:
            if not request.cookies.get(settings.session_cookie_name):
                raise
    current = await get_current_session(request, service)
    csrf.verify(headers[0], session_id=current.session_id)
