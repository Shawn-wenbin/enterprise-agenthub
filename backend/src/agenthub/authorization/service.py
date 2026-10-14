"""独立于 HTTP 的授权入口，每次判定重新读取已提交权限。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import Select, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.auth.models import User
from agenthub.auth.schemas import UserIdentity
from agenthub.authorization.policy import (
    ADMIN_METADATA_ACTIONS,
    PROJECT_ACTION_SCOPES,
    TENANT_ACTIONS,
    Action,
    Role,
    role_allows,
)
from agenthub.authorization.schemas import MembershipView, ProjectView
from agenthub.core.errors import AppError
from agenthub.db.database import Database
from agenthub.projects.models import Project, ResourceACL
from agenthub.tenants.models import Membership, MembershipRole, Tenant


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    membership_id: str
    roles: frozenset[Role]


def forbidden() -> AppError:
    return AppError("authorization_denied", "Access denied", 403)


def not_found() -> AppError:
    return AppError("resource_not_found", "Resource not found", 404)


class AuthorizationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self.database.session() as db:
                yield db
        except (OSError, TimeoutError):
            raise AppError(
                "dependency_unavailable", "Service dependency is unavailable", 503
            ) from None

    async def _memberships(
        self, db: AsyncSession, user_id: str, tenant_id: str | None = None
    ) -> list[MembershipView]:
        query = (
            select(
                Membership.id, Tenant.id, Tenant.slug, Tenant.name, MembershipRole.role
            )
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .join(User, User.id == Membership.user_id)
            .outerjoin(MembershipRole, MembershipRole.membership_id == Membership.id)
            .where(
                Membership.user_id == user_id,
                Membership.is_active.is_(True),
                Tenant.is_active.is_(True),
                User.is_active.is_(True),
            )
            .order_by(Tenant.slug, Tenant.id, MembershipRole.role)
        )
        if tenant_id is not None:
            query = query.where(Tenant.id == tenant_id)
        views: dict[str, MembershipView] = {}
        for membership_id, target_id, slug, name, role in (
            await db.execute(query)
        ).all():
            if membership_id not in views:
                views[membership_id] = MembershipView(
                    tenant_id=target_id,
                    tenant_slug=slug,
                    tenant_name=name,
                    membership_id=membership_id,
                    roles=[],
                )
            if role is not None:
                views[membership_id].roles.append(Role(role))
        return list(views.values())

    async def memberships(self, identity: UserIdentity) -> list[MembershipView]:
        async with self._session() as db:
            return await self._memberships(db, identity.id)

    async def _principal(
        self, db: AsyncSession, user_id: str, tenant_id: str
    ) -> Principal:
        views = await self._memberships(db, user_id, tenant_id)
        if not views:
            raise forbidden()
        view = views[0]
        return Principal(
            user_id=user_id,
            tenant_id=view.tenant_id,
            membership_id=view.membership_id,
            roles=frozenset(view.roles),
        )

    async def build_principal(
        self, identity: UserIdentity, tenant_id: str
    ) -> Principal:
        async with self._session() as db:
            return await self._principal(db, identity.id, tenant_id)

    async def _refresh(self, db: AsyncSession, principal: Principal) -> Principal:
        # Principal 只是输入快照；不采信调用方传入的角色，不跨请求缓存权限。
        current = await self._principal(db, principal.user_id, principal.tenant_id)
        if current.membership_id != principal.membership_id:
            raise forbidden()
        return current

    @staticmethod
    def _visible_projects(user_id: str) -> Select[tuple[Project]]:
        role_exists = exists().where(
            MembershipRole.membership_id == Membership.id,
            MembershipRole.role.in_([role.value for role in Role]),
        )
        admin_exists = exists().where(
            MembershipRole.membership_id == Membership.id,
            MembershipRole.role == Role.TENANT_ADMIN.value,
        )
        acl_exists = exists().where(
            ResourceACL.membership_id == Membership.id,
            ResourceACL.tenant_id == Project.tenant_id,
            ResourceACL.project_id == Project.id,
            ResourceACL.scope.in_(
                [
                    scope.value
                    for scope in PROJECT_ACTION_SCOPES[Action.PROJECT_METADATA_READ]
                ]
            ),
        )
        return (
            select(Project)
            .join(Tenant, Tenant.id == Project.tenant_id)
            .join(Membership, Membership.tenant_id == Tenant.id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.user_id == user_id,
                User.is_active.is_(True),
                Membership.is_active.is_(True),
                Tenant.is_active.is_(True),
                Project.is_active.is_(True),
                role_exists,
                admin_exists | acl_exists,
            )
        )

    async def list_projects(
        self, identity: UserIdentity, tenant_id: str | None = None
    ) -> list[ProjectView]:
        query = self._visible_projects(identity.id)
        if tenant_id is not None:
            query = query.where(Project.tenant_id == tenant_id)
        async with self._session() as db:
            projects = (
                await db.scalars(
                    query.order_by(Project.tenant_id, Project.slug, Project.id)
                )
            ).all()
            return [ProjectView.model_validate(project) for project in projects]

    async def require_tenant_access(
        self, principal: Principal, action: Action
    ) -> Principal:
        async with self._session() as db:
            current = await self._refresh(db, principal)
            if action not in TENANT_ACTIONS or not role_allows(current.roles, action):
                raise forbidden()
            return current

    async def require_project_access(
        self, principal: Principal, project_id: str, action: Action
    ) -> ProjectView:
        async with self._session() as db:
            try:
                current = await self._refresh(db, principal)
            except AppError as exc:
                if exc.status_code == 403:
                    raise not_found() from None
                raise
            project = await db.scalar(
                self._visible_projects(current.user_id).where(
                    Project.id == project_id,
                    Project.tenant_id == current.tenant_id,
                    Membership.id == current.membership_id,
                )
            )
            # 无 ACL、跨租户、停用与不存在统一隐藏；可见资源上的能力不足为 403。
            if project is None:
                raise not_found()
            if not role_allows(current.roles, action):
                raise forbidden()
            if action not in PROJECT_ACTION_SCOPES:
                # 尚无具体业务资源，不能把项目准入变成原文读取、自审或 owner 证明。
                raise AppError(
                    "authorization_context_required",
                    "Resource authorization context is required",
                    403,
                )
            if not (
                Role.TENANT_ADMIN in current.roles and action in ADMIN_METADATA_ACTIONS
            ):
                acl = await db.scalar(
                    select(ResourceACL.id).where(
                        ResourceACL.tenant_id == current.tenant_id,
                        ResourceACL.project_id == project_id,
                        ResourceACL.membership_id == current.membership_id,
                        ResourceACL.scope.in_(
                            [scope.value for scope in PROJECT_ACTION_SCOPES[action]]
                        ),
                    )
                )
                if acl is None:
                    raise forbidden()
            return ProjectView.model_validate(project)
