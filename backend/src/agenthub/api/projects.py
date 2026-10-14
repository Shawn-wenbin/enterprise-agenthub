"""当前用户实际可见的项目元数据并集。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from agenthub.auth.dependencies import get_current_user
from agenthub.auth.schemas import UserIdentity
from agenthub.authorization.dependencies import get_authorization_service
from agenthub.authorization.schemas import ProjectView
from agenthub.authorization.service import AuthorizationService
from agenthub.core.errors import ErrorResponse

router = APIRouter(
    tags=["projects"],
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)


@router.get("/projects", response_model=list[ProjectView])
async def projects(
    user: Annotated[UserIdentity, Depends(get_current_user)],
    authorization: Annotated[AuthorizationService, Depends(get_authorization_service)],
    tenant_id: str | None = None,
) -> list[ProjectView]:
    return await authorization.list_projects(user, tenant_id)
