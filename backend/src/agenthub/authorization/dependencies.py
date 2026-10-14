"""HTTP 装配只取服务对象，策略本身不依赖 Request。"""

from fastapi import Request

from agenthub.authorization.service import AuthorizationService


def get_authorization_service(request: Request) -> AuthorizationService:
    service: AuthorizationService = request.app.state.authorization
    return service
