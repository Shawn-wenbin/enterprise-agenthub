"""真实 PostgreSQL/Redis + HTTP Cookie，覆盖当前项目授权完整链路。"""

from dataclasses import dataclass, field
from typing import Annotated
from uuid import uuid4

import httpx2 as httpx
import pytest
import test_auth_integration as auth_fixtures
from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from test_auth_integration import login

from agenthub.artifacts.models import Artifact
from agenthub.artifacts.service import InternalArtifactScope
from agenthub.auth.dependencies import get_current_user, protect_unsafe_request
from agenthub.auth.models import Session, User
from agenthub.auth.schemas import UserIdentity
from agenthub.authorization.policy import Action, ResourceScope, Role
from agenthub.authorization.service import AuthorizationService, Principal
from agenthub.cli.seed import DEMO_LOGINS, DemoSeedService, demo_id, manifest
from agenthub.core.errors import AppError
from agenthub.db.database import Database
from agenthub.projects.models import Project, ResourceACL
from agenthub.tenants.models import Membership, MembershipRole, Tenant

pytestmark = pytest.mark.integration
app = auth_fixtures.app
client = auth_fixtures.client
user = auth_fixtures.user


@dataclass
class World:
    tenants: list[Tenant]
    projects: list[Project]
    members: list[Membership]
    users: list[User]
    password: str = field(repr=False)


@pytest.fixture
async def world(app: FastAPI, user: tuple[User, str]) -> World:
    db: Database = app.state.database
    tenants = [Tenant(slug="test-" + uuid4().hex, name="Same Tenant") for _ in range(2)]
    users = [
        user[0],
        User(login_name="bob-" + uuid4().hex, password_hash=user[0].password_hash),
    ]
    async with db.session() as session, session.begin():
        session.add_all(tenants + users[1:])
        await session.flush()
        projects = [
            Project(tenant_id=t.id, slug="same-project", name="Same Project")
            for t in tenants
        ]
        projects.append(
            Project(
                tenant_id=tenants[0].id, slug="hidden", name="Sensitive hidden project"
            )
        )
        members = [
            Membership(tenant_id=t.id, user_id=u.id)
            for t, u in zip(tenants, users, strict=True)
        ]
        session.add_all(projects + members)
        await session.flush()
        session.add_all(
            [
                MembershipRole(membership_id=m.id, role=Role.DEVELOPER.value)
                for m in members
            ]
        )
        session.add_all(
            [
                ResourceACL(
                    tenant_id=m.tenant_id,
                    membership_id=m.id,
                    project_id=p.id,
                    scope=ResourceScope.DEVELOP.value,
                )
                for m, p in zip(members, projects[:2], strict=True)
            ]
        )
    return World(tenants, projects, members, users, user[1])


@pytest.fixture
def project_probe(app: FastAPI) -> None:
    # 仅测试路由，沿用真实认证与统一安全入口，不进入产品 API。
    router = APIRouter(
        prefix="/api/v1/_test", dependencies=[Depends(protect_unsafe_request)]
    )

    @router.get("/projects/{project_id}")
    async def probe(
        project_id: str,
        tenant_id: str,
        identity: Annotated[UserIdentity, Depends(get_current_user)],
    ) -> dict[str, str]:
        service: AuthorizationService = app.state.authorization
        principal = await service.build_principal(identity, tenant_id)
        project = await service.require_project_access(
            principal, project_id, Action.PROJECT_METADATA_READ
        )
        return {"id": project.id, "tenant_id": project.tenant_id}

    app.include_router(router)


async def test_http_two_tenants_same_slug_forgery_and_hidden_ids(
    app: FastAPI, client: httpx.AsyncClient, world: World, project_probe: None
) -> None:
    assert (await client.get("/api/v1/projects")).status_code == 401
    for index in (0, 1):
        client.cookies.clear()
        assert (
            await login(client, (world.users[index], world.password))
        ).status_code == 200
        result = await client.get("/api/v1/projects")
        assert result.status_code == 200
        assert result.json() == [
            {
                "id": world.projects[index].id,
                "tenant_id": world.tenants[index].id,
                "slug": "same-project",
                "name": "Same Project",
            }
        ]
        assert result.headers["cache-control"] == "no-store"
        own = await client.get(
            f"/api/v1/_test/projects/{world.projects[index].id}",
            params={"tenant_id": world.tenants[index].id},
        )
        assert own.status_code == 200
        assert own.json() == {
            "id": world.projects[index].id,
            "tenant_id": world.tenants[index].id,
        }
        other = world.tenants[1 - index]
        assert (
            await client.get(
                "/api/v1/projects",
                params={
                    "tenant_id": other.id,
                    "role": "tenant_admin",
                    "user_id": world.users[1 - index].id,
                },
            )
        ).json() == []
        assert (
            await client.get(
                "/api/v1/projects",
                params={"role": "tenant_admin"},
                headers={"X-Tenant-ID": other.id, "X-Role": "tenant_admin"},
            )
        ).json() == result.json()
        errors = []
        for project_id in (
            world.projects[1 - index].id,
            str(uuid4()),
            world.projects[2].id,
        ):
            response = await client.get(
                f"/api/v1/_test/projects/{project_id}",
                params={"tenant_id": world.tenants[index].id, "role": "tenant_admin"},
            )
            assert response.status_code == 404
            errors.append((response.json()["code"], response.json()["message"]))
            assert (
                "Same Project" not in response.text and "Sensitive" not in response.text
            )
        assert len(set(errors)) == 1
        assert (
            await client.get(
                f"/api/v1/_test/projects/{world.projects[index].id}",
                params={"tenant_id": other.id},
            )
        ).status_code == 403


async def test_multi_tenant_me_union_and_roles_do_not_mix(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    db: Database = app.state.database
    async with db.session() as session, session.begin():
        member = Membership(tenant_id=world.tenants[1].id, user_id=world.users[0].id)
        session.add(member)
        await session.flush()
        session.add(MembershipRole(membership_id=member.id, role=Role.REVIEWER.value))
        session.add(
            ResourceACL(
                tenant_id=member.tenant_id,
                membership_id=member.id,
                project_id=world.projects[1].id,
                scope="review",
            )
        )
    assert (await login(client, (world.users[0], world.password))).status_code == 200
    assert {p["id"] for p in (await client.get("/api/v1/projects")).json()} == {
        p.id for p in world.projects[:2]
    }
    me = (await client.get("/api/v1/auth/me")).json()
    assert (
        me["id"] == world.users[0].id and me["login_name"] == world.users[0].login_name
    )
    assert {m["tenant_id"]: m["roles"] for m in me["memberships"]} == {
        world.tenants[0].id: ["developer"],
        world.tenants[1].id: ["reviewer"],
    }
    for secret in (
        world.password,
        world.users[0].password_hash,
        "token_hash",
        "csrf_secret",
    ):
        assert secret not in str(me)
    service: AuthorizationService = app.state.authorization
    identity = UserIdentity(id=world.users[0].id, login_name=world.users[0].login_name)
    first = await service.build_principal(identity, world.tenants[0].id)
    second = await service.build_principal(identity, world.tenants[1].id)
    assert first.roles == frozenset({Role.DEVELOPER})
    assert second.roles == frozenset({Role.REVIEWER})
    with pytest.raises(AppError) as denied:
        await service.require_project_access(
            second, world.projects[1].id, Action.RUN_CREATE
        )
    assert denied.value.code == "authorization_denied"


@pytest.mark.parametrize("change", ["acl", "membership", "tenant", "project", "role"])
async def test_revocation_same_cookie_and_stale_principal(
    app: FastAPI, client: httpx.AsyncClient, world: World, change: str
) -> None:
    await login(client, (world.users[0], world.password))
    assert len((await client.get("/api/v1/projects")).json()) == 1
    service: AuthorizationService = app.state.authorization
    principal = await service.build_principal(
        UserIdentity(id=world.users[0].id, login_name=world.users[0].login_name),
        world.tenants[0].id,
    )
    db: Database = app.state.database
    async with db.session() as session, session.begin():
        if change == "acl":
            await session.execute(
                delete(ResourceACL).where(
                    ResourceACL.membership_id == world.members[0].id
                )
            )
        elif change == "role":
            await session.execute(
                delete(MembershipRole).where(
                    MembershipRole.membership_id == world.members[0].id
                )
            )
        else:
            model, target = {
                "membership": (Membership, world.members[0].id),
                "tenant": (Tenant, world.tenants[0].id),
                "project": (Project, world.projects[0].id),
            }[change]
            await session.execute(
                update(model).where(model.id == target).values(is_active=False)
            )
    assert (await client.get("/api/v1/projects")).json() == []
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    with pytest.raises(AppError) as denied:
        await service.require_project_access(
            principal, world.projects[0].id, Action.PROJECT_METADATA_READ
        )
    assert denied.value.status_code == 404


async def test_admin_metadata_only_and_multi_roles_future_fail_closed(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    db: Database = app.state.database
    async with db.session() as session, session.begin():
        await session.execute(
            delete(MembershipRole).where(
                MembershipRole.membership_id == world.members[0].id
            )
        )
        await session.execute(
            delete(ResourceACL).where(ResourceACL.membership_id == world.members[0].id)
        )
        session.add(
            MembershipRole(membership_id=world.members[0].id, role="tenant_admin")
        )
    await login(client, (world.users[0], world.password))
    projects = (await client.get("/api/v1/projects")).json()
    assert {p["id"] for p in projects} == {world.projects[0].id, world.projects[2].id}
    assert all(set(p) == {"id", "tenant_id", "slug", "name"} for p in projects)
    service: AuthorizationService = app.state.authorization
    identity = UserIdentity(id=world.users[0].id, login_name=world.users[0].login_name)
    principal = await service.build_principal(identity, world.tenants[0].id)
    assert (
        await service.require_project_access(
            principal, world.projects[2].id, Action.PROJECT_MANAGE
        )
    ).id == world.projects[2].id
    await service.require_tenant_access(principal, Action.MEMBERS_MANAGE)
    for action in (Action.EVIDENCE_READ, Action.RUN_CREATE, Action.APPROVAL_DECIDE):
        with pytest.raises(AppError) as denied:
            await service.require_project_access(
                principal, world.projects[2].id, action
            )
        assert denied.value.status_code == 403
    async with db.session() as session, session.begin():
        await session.execute(
            delete(MembershipRole).where(
                MembershipRole.membership_id == world.members[0].id
            )
        )
        for role in ("developer", "reviewer"):
            session.add(MembershipRole(membership_id=world.members[0].id, role=role))
        for scope in ("develop", "review"):
            session.add(
                ResourceACL(
                    tenant_id=world.tenants[0].id,
                    project_id=world.projects[0].id,
                    membership_id=world.members[0].id,
                    scope=scope,
                )
            )
    # 旧 Admin 快照不能管理；新多角色不能凭项目 ACL 通过自审/owner 等尚不存在的上下文。
    with pytest.raises(AppError):
        await service.require_project_access(
            principal, world.projects[0].id, Action.PROJECT_MANAGE
        )
    principal = await service.build_principal(identity, world.tenants[0].id)
    assert principal.roles == frozenset({Role.DEVELOPER, Role.REVIEWER})
    for action in (Action.APPROVAL_DECIDE, Action.RUN_CANCEL_OWN, Action.EVIDENCE_READ):
        with pytest.raises(AppError) as denied:
            await service.require_project_access(
                principal, world.projects[0].id, action
            )
        assert denied.value.code == "authorization_context_required"


async def test_no_membership_roleless_viewer_and_forged_principal(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    db: Database = app.state.database
    service: AuthorizationService = app.state.authorization
    async with db.session() as session, session.begin():
        outsider = User(
            login_name="outsider-" + uuid4().hex,
            password_hash=world.users[0].password_hash,
        )
        session.add(outsider)
    await login(client, (outsider, world.password))
    assert (await client.get("/api/v1/projects")).json() == []
    with pytest.raises(AppError):
        await service.build_principal(
            UserIdentity(id=outsider.id, login_name=outsider.login_name),
            world.tenants[0].id,
        )
    await login(client, (world.users[0], world.password))
    async with db.session() as session, session.begin():
        await session.execute(
            delete(MembershipRole).where(
                MembershipRole.membership_id == world.members[0].id
            )
        )
    assert (await client.get("/api/v1/projects")).json() == []
    async with db.session() as session, session.begin():
        session.add(MembershipRole(membership_id=world.members[0].id, role="viewer"))
    assert len((await client.get("/api/v1/projects")).json()) == 1
    forged = Principal(
        world.users[0].id,
        world.tenants[0].id,
        world.members[0].id,
        frozenset({Role.TENANT_ADMIN}),
    )
    with pytest.raises(AppError) as denied:
        await service.require_project_access(
            forged, world.projects[0].id, Action.PROJECT_MANAGE
        )
    assert denied.value.status_code == 403


@pytest.mark.parametrize(
    "invalid",
    [
        "cross_project",
        "cross_member",
        "scope",
        "duplicate",
        "role",
        "duplicate_role",
        "duplicate_member",
        "duplicate_slug",
        "slug",
    ],
)
async def test_database_rejects_invalid_grants_and_duplicates(
    app: FastAPI, world: World, invalid: str
) -> None:
    db: Database = app.state.database
    record = ResourceACL(
        tenant_id=world.tenants[0].id,
        membership_id=world.members[0].id,
        project_id=world.projects[0].id,
        scope="read",
    )
    if invalid == "cross_project":
        record.project_id = world.projects[1].id
    elif invalid == "cross_member":
        record.membership_id = world.members[1].id
    elif invalid == "scope":
        record.scope = "all"
    elif invalid == "duplicate":
        record.scope = "develop"
    elif invalid in {"role", "duplicate_role"}:
        record = MembershipRole(
            membership_id=world.members[0].id,
            role="super_admin" if invalid == "role" else "developer",
        )
    elif invalid == "duplicate_member":
        record = Membership(tenant_id=world.tenants[0].id, user_id=world.users[0].id)
    elif invalid in {"duplicate_slug", "slug"}:
        record = Project(
            tenant_id=world.tenants[0].id,
            slug="same-project" if invalid == "duplicate_slug" else "Bad SLUG",
            name="duplicate",
        )
    with pytest.raises(IntegrityError):
        async with db.session() as session, session.begin():
            session.add(record)


async def test_artifact_confirmed_fk_and_project_acl_does_not_grant_content(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    scope = InternalArtifactScope(
        tenant_id=world.tenants[0].id, project_id=world.projects[0].id
    )
    service = app.state.artifacts
    saved = await service.save(scope, b"private artifact")
    db: Database = app.state.database
    async with db.session() as session:
        record = await session.get(Artifact, saved.artifact_id)
        assert record.authorized_project_id == world.projects[0].id
    for target in (world.projects[1].id, world.projects[2].id):
        with pytest.raises(IntegrityError):
            async with db.session() as session, session.begin():
                await session.execute(
                    update(Artifact)
                    .where(Artifact.artifact_id == saved.artifact_id)
                    .values(authorized_project_id=target)
                )
    # 同时伪造两个项目字段以满足 CHECK，仍必须被跨租户复合 FK 拒绝。
    with pytest.raises(IntegrityError):
        async with db.session() as session, session.begin():
            await session.execute(
                update(Artifact)
                .where(Artifact.artifact_id == saved.artifact_id)
                .values(
                    project_id=world.projects[1].id,
                    authorized_project_id=world.projects[1].id,
                )
            )
    await login(client, (world.users[0], world.password))
    assert (
        await client.get(f"/api/v1/artifacts/{saved.artifact_id}/content")
    ).status_code == 404
    assert await service.read(scope, saved.artifact_id) == b"private artifact"
    with pytest.raises(AppError) as denied:
        await service.read(
            InternalArtifactScope(
                tenant_id=world.tenants[1].id, project_id=world.projects[0].id
            ),
            saved.artifact_id,
        )
    assert denied.value.code == "artifact_not_found"


async def test_seed_idempotence_login_revocation_conflicts_and_preservation(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    import asyncio
    import secrets

    from pydantic import SecretStr
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session as ORMSession

    settings = app.state.settings.model_copy(
        update={
            "demo_seed_passwords": {
                name: SecretStr(secrets.token_urlsafe(24)) for name in DEMO_LOGINS
            }
        }
    )
    db: Database = app.state.database
    seed = DemoSeedService(settings, db, app.state.auth.passwords)
    async with db.session() as session:
        before_artifacts = (await session.scalars(select(Artifact))).all()
        before_sessions = (await session.scalars(select(Session))).all()
        before_hashes = dict(
            (await session.execute(select(User.id, User.password_hash))).all()
        )
    commits = 0

    def fail_seed_commit(session) -> None:
        nonlocal commits
        commits += 1
        if commits == 2:
            raise OperationalError("injected seed commit", {}, Exception("unavailable"))

    event.listen(ORMSession, "before_commit", fail_seed_commit)
    try:
        with pytest.raises(OperationalError):
            await seed.run()
    finally:
        event.remove(ORMSession, "before_commit", fail_seed_commit)

    async def counts() -> list[int]:
        async with db.session() as session:
            return [
                await session.scalar(
                    select(func.count()).select_from(group.model).where(group.condition)
                )
                for group in manifest()
            ]

    assert await counts() == [0, 0, 0, 0, 0, 0]
    assert set(await asyncio.gather(seed.run(), seed.run())) == {"created", "unchanged"}
    assert await counts() == [2, 4, 2, 4, 4, 4]
    async with db.session() as session:
        seeded_hashes = dict(
            (
                await session.execute(
                    select(User.login_name, User.password_hash).where(
                        User.login_name.in_(DEMO_LOGINS)
                    )
                )
            ).all()
        )
    assert await seed.run() == "unchanged"
    assert await counts() == [2, 4, 2, 4, 4, 4]
    changed_config = settings.model_copy(
        update={
            "demo_seed_passwords": {
                name: SecretStr(secrets.token_urlsafe(24)) for name in DEMO_LOGINS
            }
        }
    )
    assert (
        await DemoSeedService(changed_config, db, app.state.auth.passwords).run()
        == "unchanged"
    )
    async with db.session() as session:
        assert (
            dict(
                (
                    await session.execute(
                        select(User.login_name, User.password_hash).where(
                            User.login_name.in_(DEMO_LOGINS)
                        )
                    )
                ).all()
            )
            == seeded_hashes
        )
    async with db.session() as session:
        for record in before_artifacts:
            current = await session.get(Artifact, record.artifact_id)
            assert {
                c.name: getattr(current, c.name) for c in Artifact.__table__.columns
            } == {c.name: getattr(record, c.name) for c in Artifact.__table__.columns}
        for record in before_sessions:
            current = await session.get(Session, record.id)
            assert {
                c.name: getattr(current, c.name) for c in Session.__table__.columns
            } == {c.name: getattr(record, c.name) for c in Session.__table__.columns}
        for target_id, encoded in before_hashes.items():
            assert (await session.get(User, target_id)).password_hash == encoded
    for name in DEMO_LOGINS:
        client.cookies.clear()
        csrf = (
            await client.get(
                "/api/v1/auth/csrf", headers={"Origin": "http://127.0.0.1:5173"}
            )
        ).json()["csrf_token"]
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "login_name": name,
                "password": settings.demo_seed_passwords[name].get_secret_value(),
            },
            headers={"Origin": "http://127.0.0.1:5173", "X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        label = name.split("-")[1]
        assert (await client.get("/api/v1/projects")).json() == [
            {
                "id": demo_id("project-" + label),
                "tenant_id": demo_id("tenant-" + label),
                "slug": "demo-project",
                "name": "Demo Project",
            }
        ]
    name = DEMO_LOGINS[0]
    member_id = demo_id("membership-" + name)
    # 每个变更单独验证拒绝；测试恢复自身记录，seed 从不写回撤权。
    for change in ("membership", "acl", "role"):
        async with db.session() as session, session.begin():
            if change == "membership":
                await session.execute(
                    update(Membership)
                    .where(Membership.id == member_id)
                    .values(is_active=False)
                )
            elif change == "acl":
                await session.execute(
                    delete(ResourceACL).where(ResourceACL.membership_id == member_id)
                )
            else:
                await session.execute(
                    delete(MembershipRole).where(
                        MembershipRole.membership_id == member_id
                    )
                )
        with pytest.raises(AppError) as denied:
            await seed.run()
        assert denied.value.code == "seed_conflict"
        async with db.session() as session, session.begin():
            if change == "membership":
                assert not (await session.get(Membership, member_id)).is_active
                await session.execute(
                    update(Membership)
                    .where(Membership.id == member_id)
                    .values(is_active=True)
                )
            else:
                group = manifest()[5 if change == "acl" else 4]
                assert not (
                    await session.scalars(
                        select(group.model).where(
                            group.model.membership_id == member_id
                        )
                    )
                ).all()
                for values in group.records:
                    if values["membership_id"] == member_id:
                        session.add(group.model(**values))
    # 先移除本测试的 ACL/role，再删除 Membership；重复 seed 不重建。
    async with db.session() as session, session.begin():
        await session.execute(
            delete(ResourceACL).where(ResourceACL.membership_id == member_id)
        )
        await session.execute(delete(Membership).where(Membership.id == member_id))
    with pytest.raises(AppError) as denied:
        await seed.run()
    assert denied.value.code == "seed_conflict"
    async with db.session() as session:
        assert await session.get(Membership, member_id) is None
