"""事务化演示数据引导；已有授权变化视为冲突，不修复撤权。"""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import ColumnElement, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.auth.models import User
from agenthub.auth.security import PasswordSecurity
from agenthub.authorization.policy import ResourceScope, Role
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.db.base import Base
from agenthub.db.database import Database
from agenthub.projects.models import Project, ResourceACL
from agenthub.tenants.models import Membership, MembershipRole, Tenant

DEMO_LOGINS = tuple(
    f"demo-{tenant}-{role}"
    for tenant in ("a", "b")
    for role in ("developer", "reviewer")
)


def demo_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, "enterprise-agenthub/demo/v1/" + key))


def conflict() -> AppError:
    return AppError(
        "seed_conflict",
        "Demo data conflicts with existing state; no data was changed",
        409,
    )


@dataclass(frozen=True)
class SeedGroup:
    model: type[Base]
    condition: ColumnElement[bool]
    records: list[dict[str, str | bool]]


def manifest() -> list[SeedGroup]:
    tenants: list[dict[str, str | bool]] = []
    users: list[dict[str, str | bool]] = []
    projects: list[dict[str, str | bool]] = []
    memberships: list[dict[str, str | bool]] = []
    roles: list[dict[str, str | bool]] = []
    acls: list[dict[str, str | bool]] = []
    for label in ("a", "b"):
        tenant_id = demo_id("tenant-" + label)
        project_id = demo_id("project-" + label)
        tenants.append(
            dict(
                id=tenant_id,
                slug="demo-" + label,
                name="Demo Tenant " + label.upper(),
                is_active=True,
            )
        )
        projects.append(
            dict(
                id=project_id,
                tenant_id=tenant_id,
                slug="demo-project",
                name="Demo Project",
                is_active=True,
            )
        )
        for role, scope in (
            (Role.DEVELOPER, ResourceScope.DEVELOP),
            (Role.REVIEWER, ResourceScope.REVIEW),
        ):
            login_name = f"demo-{label}-{role.value}"
            user_id = demo_id(login_name)
            membership_id = demo_id("membership-" + login_name)
            users.append(dict(id=user_id, login_name=login_name, is_active=True))
            memberships.append(
                dict(
                    id=membership_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    is_active=True,
                )
            )
            roles.append(dict(membership_id=membership_id, role=role.value))
            acls.append(
                dict(
                    id=demo_id("acl-" + login_name),
                    tenant_id=tenant_id,
                    project_id=project_id,
                    membership_id=membership_id,
                    scope=scope.value,
                )
            )
    user_ids = [record["id"] for record in users]
    tenant_ids = [record["id"] for record in tenants]
    member_ids = [record["id"] for record in memberships]
    return [
        SeedGroup(
            Tenant,
            Tenant.slug.in_(["demo-a", "demo-b"]) | Tenant.id.in_(tenant_ids),
            tenants,
        ),
        SeedGroup(
            User, User.login_name.in_(DEMO_LOGINS) | User.id.in_(user_ids), users
        ),
        SeedGroup(
            Project,
            Project.tenant_id.in_(tenant_ids) & (Project.slug == "demo-project")
            | Project.id.in_([record["id"] for record in projects]),
            projects,
        ),
        SeedGroup(
            Membership,
            Membership.user_id.in_(user_ids) & Membership.tenant_id.in_(tenant_ids)
            | Membership.id.in_(member_ids),
            memberships,
        ),
        SeedGroup(MembershipRole, MembershipRole.membership_id.in_(member_ids), roles),
        SeedGroup(ResourceACL, ResourceACL.membership_id.in_(member_ids), acls),
    ]


class DemoSeedService:
    def __init__(
        self, settings: Settings, database: Database, passwords: PasswordSecurity
    ) -> None:
        self.settings = settings
        self.database = database
        self.passwords = passwords

    def validate_configuration(self) -> None:
        # 环境白名单先于数据库访问；未知 ENV 在 Settings 构建时已拒绝。
        if self.settings.env not in {"development", "test"}:
            raise AppError(
                "seed_environment_forbidden", "Demo seed requires development/test", 403
            )
        configured = self.settings.demo_seed_passwords
        if set(configured) != set(DEMO_LOGINS) or any(
            not 12 <= len(password.get_secret_value()) <= 256
            for password in configured.values()
        ):
            raise AppError(
                "seed_configuration_required",
                "Configure DEMO_SEED_PASSWORDS for all four demo logins "
                "(12-256 characters)",
                422,
            )

    async def _existing(self, db: AsyncSession) -> bool:
        any_existing = False
        complete = True
        for group in manifest():
            rows = (await db.scalars(select(group.model).where(group.condition))).all()
            any_existing |= bool(rows)
            fields = tuple(group.records[0])
            expected = {
                tuple(record[field] for field in fields) for record in group.records
            }
            actual = {tuple(getattr(row, field) for field in fields) for row in rows}
            complete &= actual == expected and len(rows) == len(group.records)
        if any_existing and not complete:
            raise conflict()
        return any_existing

    async def run(self) -> str:
        self.validate_configuration()
        # 并发 seed 使用同一事务锁。密码计算之前释放连接，创建时再次核对。
        async with self.database.session() as db, db.begin():
            await db.execute(text("SELECT pg_advisory_xact_lock(140004)"))
            if await self._existing(db):
                return "unchanged"
        hashes = {
            login: await self.passwords.hash(
                self.settings.demo_seed_passwords[login].get_secret_value()
            )
            for login in DEMO_LOGINS
        }
        try:
            async with self.database.session() as db, db.begin():
                await db.execute(text("SELECT pg_advisory_xact_lock(140004)"))
                if await self._existing(db):
                    return "unchanged"
                for group in manifest():
                    for record in group.records:
                        values = dict(record)
                        if group.model is User:
                            values["password_hash"] = hashes[str(record["login_name"])]
                        db.add(group.model(**values))
                    await db.flush()
        except IntegrityError:
            raise conflict() from None
        return "created"
