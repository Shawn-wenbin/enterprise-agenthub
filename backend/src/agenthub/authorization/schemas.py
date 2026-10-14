"""不暴露凭据或其他用户信息的权限视图。"""

import re

from pydantic import BaseModel, ConfigDict

from agenthub.auth.schemas import UserIdentity
from agenthub.authorization.policy import Role


def normalize_slug(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", normalized):
        raise ValueError("Invalid slug")
    return normalized


class MembershipView(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: str
    tenant_slug: str
    tenant_name: str
    membership_id: str
    roles: list[Role]


class CurrentUserView(UserIdentity):
    memberships: list[MembershipView]


class ProjectView(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)
    id: str
    tenant_id: str
    slug: str
    name: str
