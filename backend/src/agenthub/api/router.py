"""所有业务 Router 挂到此入口，统一保护 Cookie 认证的修改请求。"""

from fastapi import APIRouter, Depends

from agenthub.api.auth import router as auth_router
from agenthub.auth.dependencies import protect_unsafe_request

router = APIRouter(prefix="/api/v1", dependencies=[Depends(protect_unsafe_request)])
router.include_router(auth_router)
