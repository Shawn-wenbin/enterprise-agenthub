"""隔离库验证授权、旧数据迁移和真实 TCP HTTP；不清库或删除证据。

运行：uv run python scripts/verify_unit14.py
密码仅在本进程与 seed 子进程环境中使用，不写入结果文件或日志。
"""

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx2 as httpx
from pydantic import SecretStr
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url
from verify_unit12 import create_database, sentinel
from verify_unit13 import server, test_user

from agenthub.artifacts.models import Artifact
from agenthub.auth.security import PasswordSecurity
from agenthub.auth.service import AuthService
from agenthub.cli.seed import DEMO_LOGINS, demo_id
from agenthub.core.config import Settings
from agenthub.db.database import Database
from agenthub.projects.models import ResourceACL
from agenthub.tenants.models import Membership, MembershipRole

REPO = Path(__file__).resolve().parents[1]


async def snapshot(settings: Settings) -> dict[str, list[dict[str, object]]]:
    database = Database(settings)
    try:
        async with database.session() as db:
            result = {}
            for table in ("artifacts", "users", "sessions"):
                rows = (
                    (await db.execute(text(f"SELECT * FROM {table} ORDER BY 1")))
                    .mappings()
                    .all()
                )
                result[table] = [
                    {
                        key: value
                        for key, value in row.items()
                        if key != "authorized_project_id"
                    }
                    for row in rows
                ]
            return result
    finally:
        await database.close()


async def audit(settings: Settings) -> dict[str, object]:
    database = Database(settings)
    try:
        async with database.session() as db:
            return {
                "head": await db.scalar(
                    text("SELECT version_num FROM alembic_version")
                ),
                "artifact_count": await db.scalar(
                    text("SELECT count(*) FROM artifacts")
                ),
                "ownership_groups": [
                    list(row)
                    for row in (
                        await db.execute(
                            text(
                                "SELECT tenant_id, project_id, status, count(*) "
                                "FROM artifacts GROUP BY 1,2,3 ORDER BY 1,2,3"
                            )
                        )
                    ).all()
                ],
            }
    finally:
        await database.close()


async def legacy_records(settings: Settings) -> str:
    preserved = await sentinel(settings)
    database = Database(settings)
    passwords = PasswordSecurity(1)
    try:
        # 旧 User/Session 通过既有认证 service 创建，迁移不得改变账号或撤销会话。
        login_name, password, _ = await test_user(settings)
        await AuthService(database, passwords, settings.session_ttl_seconds).login(
            login_name, password
        )
        async with database.session() as db, db.begin():
            for status in ("pending", "failed"):
                await db.execute(
                    text(
                        "INSERT INTO artifacts (artifact_id, storage_key, tenant_id, "
                        "project_id, media_type, status, created_at, updated_at) "
                        "VALUES (:id, :key, :tenant, :project, 'text/plain', "
                        ":status, now(), now())"
                    ),
                    {
                        "id": str(uuid4()),
                        "key": "obj_" + secrets.token_hex(32),
                        "tenant": "legacy-unmapped-" + status,
                        "project": "old-project",
                        "status": status,
                    },
                )
        return preserved
    finally:
        await database.close()


async def revoke(settings: Settings, change: str) -> None:
    database = Database(settings)
    member_id = demo_id("membership-demo-a-developer")
    try:
        async with database.session() as db, db.begin():
            if change == "acl":
                await db.execute(
                    delete(ResourceACL).where(ResourceACL.membership_id == member_id)
                )
            elif change == "membership":
                await db.execute(
                    update(Membership)
                    .where(Membership.id == member_id)
                    .values(is_active=False)
                )
            elif change == "role":
                await db.execute(
                    delete(MembershipRole).where(
                        MembershipRole.membership_id == member_id
                    )
                )
    finally:
        await database.close()


def main() -> None:
    os.chdir(REPO)
    settings = Settings()  # type: ignore[call-arg]
    if settings.env not in {"development", "test"}:
        raise RuntimeError("Verification requires development/test")
    baseline_audit = asyncio.run(audit(settings))
    suffix = uuid4().hex
    names = ["agenthub_unit14_test_" + uuid4().hex for _ in range(2)]
    directory = REPO / ".data" / "unit14-verification" / suffix
    directory.mkdir(parents=True, mode=0o700)
    urls = [asyncio.run(create_database(settings, name)) for name in names]
    redis_url = (
        make_url(settings.redis_url.get_secret_value())
        .set(database="15")
        .render_as_string(hide_password=False)
    )
    common = {
        **os.environ,
        "ENV": "test",
        "LC_ALL": "en_US.UTF-8",
        "REDIS_URL": redis_url,
        "TEST_REDIS_URL": redis_url,
        "CSRF_SECRET": secrets.token_hex(32),
        "LOGIN_RATE_NAMESPACE": "test:unit14:" + suffix,
    }
    isolated = [
        settings.model_copy(
            update={
                "env": "test",
                "database_url": SecretStr(url),
                "redis_url": SecretStr(redis_url),
                "artifact_root": directory / f"artifacts-{index}",
            }
        )
        for index, url in enumerate(urls)
    ]
    environments = [
        {
            **common,
            "DATABASE_URL": url,
            "TEST_DATABASE_URL": url,
            "ARTIFACT_ROOT": str(isolated[index].artifact_root),
        }
        for index, url in enumerate(urls)
    ]

    def command(
        index: int,
        args: list[str],
        *,
        expected: int = 0,
        overrides: dict[str, str] | None = None,
        capture: bool = False,
        cwd: Path = REPO,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            args,
            cwd=cwd,
            env={**environments[index], **(overrides or {})},
            text=True,
            capture_output=capture,
        )
        if result.returncode != expected:
            if capture:
                print(result.stdout, result.stderr)
            raise RuntimeError("Verification command failed")
        return result

    def migrate(index: int, target: str) -> None:
        command(
            index,
            ["uv", "run", "alembic", "-c", "backend/alembic.ini", "upgrade", target],
        )

    print("Created isolated databases:", *names, flush=True)
    migrate(0, "head")
    migrate(0, "head")
    command(0, ["uv", "run", "alembic", "-c", "backend/alembic.ini", "check"])
    migrate(1, "0003_users_sessions")
    preserved = asyncio.run(legacy_records(isolated[1]))
    before = asyncio.run(snapshot(isolated[1]))
    migrate(1, "head")
    migrate(1, "head")
    assert before == asyncio.run(snapshot(isolated[1]))
    asyncio.run(sentinel(isolated[1], preserved))

    async def verify_legacy_ownership() -> None:
        database = Database(isolated[1])
        try:
            async with database.session() as db:
                records = (await db.scalars(select(Artifact))).all()
                assert len(records) == 3 and all(
                    row.authorized_project_id is None for row in records
                )
                assert {row.status for row in records} == {"ready", "pending", "failed"}
        finally:
            await database.close()

    asyncio.run(verify_legacy_ownership())
    command(1, ["uv", "run", "alembic", "-c", "backend/alembic.ini", "check"])
    command(1, ["uv", "run", "pytest", "backend/tests", "--integration"])

    passwords = {name: secrets.token_urlsafe(24) for name in DEMO_LOGINS}
    seed_config = {"DEMO_SEED_PASSWORDS": json.dumps(passwords)}
    # 环境字典与 .env 的嵌套配置会合并；隔离 cwd，避免本地 Seed 密码填补“缺失配置”。
    seed_command = [sys.executable, "-m", "agenthub.cli", "seed-demo"]
    before_seed = asyncio.run(snapshot(isolated[0]))
    assert (
        "created"
        in command(
            0, seed_command, overrides=seed_config, capture=True, cwd=directory
        ).stdout
    )
    after_seed = asyncio.run(snapshot(isolated[0]))
    assert (
        "unchanged"
        in command(
            0, seed_command, overrides=seed_config, capture=True, cwd=directory
        ).stdout
    )
    assert after_seed == asyncio.run(snapshot(isolated[0]))
    assert (
        before_seed["artifacts"] == after_seed["artifacts"]
        and before_seed["sessions"] == after_seed["sessions"]
    )
    for overrides, error in [
        ({"DEMO_SEED_PASSWORDS": "{}"}, "seed_configuration_required"),
        (
            {
                **seed_config,
                "ENV": "production",
                "TRUSTED_ORIGINS": '["https://demo.example.test"]',
                "SESSION_COOKIE_NAME": "__Host-agenthub_session",
                "SESSION_COOKIE_SECURE": "true",
                "REDIS_URL": "redis://:a2b3c4d5e6f7g8h9@127.0.0.1:1/15",
            },
            "seed_environment_forbidden",
        ),
        ({**seed_config, "ENV": "staging"}, "configuration_error"),
    ]:
        result = command(
            0,
            seed_command,
            expected=1,
            overrides=overrides,
            capture=True,
            cwd=directory,
        )
        assert error in result.stderr
        assert all(
            password not in result.stderr + result.stdout
            for password in passwords.values()
        )
        assert after_seed == asyncio.run(snapshot(isolated[0]))

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    environment = {**environments[0], "TRUSTED_ORIGINS": json.dumps([origin])}
    api_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "agenthub.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-proxy-headers",
    ]
    with server(api_command, environment, directory / "api.log", origin + "/health"):
        assert httpx.get(origin + "/health/ready").status_code == 200
        assert httpx.get(origin + "/api/v1/projects").status_code == 401
        for name, password in passwords.items():
            with httpx.Client(base_url=origin, headers={"Origin": origin}) as client:
                csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
                result = client.post(
                    "/api/v1/auth/login",
                    json={"login_name": name, "password": password},
                    headers={"X-CSRF-Token": csrf},
                )
                assert result.status_code == 200
                label = name.split("-")[1]
                projects = client.get("/api/v1/projects").json()
                assert projects == [
                    {
                        "id": demo_id("project-" + label),
                        "tenant_id": demo_id("tenant-" + label),
                        "slug": "demo-project",
                        "name": "Demo Project",
                    }
                ]
                assert (
                    client.get(
                        "/api/v1/projects",
                        params={
                            "tenant_id": demo_id(
                                "tenant-" + ("b" if label == "a" else "a")
                            ),
                            "role": "tenant_admin",
                        },
                    ).json()
                    == []
                )
                assert len(client.get("/api/v1/auth/me").json()["memberships"]) == 1
                if name == "demo-a-developer":
                    for change in ("acl", "role", "membership"):
                        asyncio.run(revoke(isolated[0], change))
                        assert client.get("/api/v1/projects").json() == []
                        assert client.get("/api/v1/auth/me").status_code == 200
                        result = command(
                            0,
                            seed_command,
                            expected=1,
                            overrides=seed_config,
                            capture=True,
                            cwd=directory,
                        )
                        assert "seed_conflict" in result.stderr
    assert baseline_audit == asyncio.run(audit(settings))
    result = {
        "status": "verified",
        "databases": names,
        "baseline_audit": baseline_audit,
        "migration": "0004_tenant_authorization",
        "legacy_artifacts_preserved": 3,
        "legacy_user_session_preserved": True,
        "postgresql_redis_http": "real",
        "seed_cli": "created, unchanged, configuration/environment/conflict rejected",
        "tcp_http": "four demo logins, cookie projects, tenant forgery, revocation",
        "redis_namespace": common["LOGIN_RATE_NAMESPACE"],
        "redis_database": 15,
        "pytest": "full suite with --integration passed",
    }
    (directory / "result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print("Unit 1.4 verified; retained evidence:", directory, flush=True)


if __name__ == "__main__":
    main()
