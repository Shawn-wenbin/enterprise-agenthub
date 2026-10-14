"""新建隔离库验证迁移、真实安全测试、Uvicorn 重启与 Vite 代理。

保留数据库和证据，不删除数据/卷，不创建正式演示账号。
运行：uv run python scripts/verify_unit13.py
"""

import asyncio
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx2 as httpx
from sqlalchemy.engine import make_url
from verify_unit12 import create_database, sentinel

from agenthub.auth.models import User
from agenthub.auth.security import PasswordSecurity
from agenthub.core.config import Settings
from agenthub.db.database import Database

REPO = Path(__file__).resolve().parents[1]


@contextmanager
def server(
    command: list[str], environment: dict[str, str], logfile: Path, probe: str
) -> Iterator[None]:
    with logfile.open("w") as output:
        process = subprocess.Popen(
            command,
            cwd=REPO,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        "Server exited; inspect retained verification log"
                    )
                try:
                    if httpx.get(probe, timeout=1).status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.1)
            else:
                raise TimeoutError("Server startup timed out")
            yield
        finally:
            # 只终止本函数创建的进程组，包括 pnpm 启动的 Vite 子进程。
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


async def test_user(settings: Settings) -> tuple[str, str, float]:
    passwords = PasswordSecurity(2)
    password = secrets.token_urlsafe(24)
    began = time.perf_counter()
    encoded = await passwords.hash(password)
    elapsed = (time.perf_counter() - began) * 1000
    database = Database(settings)
    try:
        async with database.session() as db, db.begin():
            login_name = "verification-" + uuid4().hex
            db.add(User(login_name=login_name, password_hash=encoded))
        return login_name, password, elapsed
    finally:
        await database.close()


def main() -> None:
    os.chdir(REPO)
    settings = Settings()  # type: ignore[call-arg]
    if settings.env not in {"development", "test"}:
        raise RuntimeError("Verification requires development/test")
    # 使用系统分配的空闲端口，不占用或终止用户的已有进程。
    ports = []
    for _ in range(2):
        with socket.socket() as check:
            check.bind(("127.0.0.1", 0))
            ports.append(check.getsockname()[1])
    api_port, vite_port = ports
    origin = f"http://127.0.0.1:{vite_port}"
    api_origin = f"http://127.0.0.1:{api_port}"
    name = "agenthub_unit13_test_" + uuid4().hex
    directory = REPO / ".data" / "unit13-verification" / name
    directory.mkdir(parents=True, mode=0o700)
    url = asyncio.run(create_database(settings, name))
    redis_url = (
        make_url(settings.redis_url.get_secret_value())
        .set(database="15")
        .render_as_string(hide_password=False)
    )
    environment = {
        **os.environ,
        "ENV": "test",
        "LC_ALL": "en_US.UTF-8",
        "DATABASE_URL": url,
        "TEST_DATABASE_URL": url,
        "REDIS_URL": redis_url,
        "TEST_REDIS_URL": redis_url,
        "ARTIFACT_ROOT": str(directory / "artifacts"),
        "AGENTHUB_API_PROXY_TARGET": api_origin,
        "CSRF_SECRET": secrets.token_hex(32),
        "TRUSTED_ORIGINS": json.dumps([origin]),
        "LOGIN_RATE_NAMESPACE": "test:unit13:verify:" + uuid4().hex,
    }
    isolated = Settings(
        env="test",
        database_url=url,
        redis_url=redis_url,
        artifact_root=directory / "artifacts",
        _env_file=None,
    )
    print("Created isolated database:", name, flush=True)

    def migrate(target: str) -> None:
        subprocess.run(
            ["uv", "run", "alembic", "-c", "backend/alembic.ini", "upgrade", target],
            cwd=REPO,
            env=environment,
            check=True,
        )

    migrate("0002_constraint_names")
    preserved = asyncio.run(sentinel(isolated))
    migrate("head")
    migrate("head")
    asyncio.run(sentinel(isolated, preserved))
    subprocess.run(
        ["uv", "run", "alembic", "-c", "backend/alembic.ini", "check"],
        cwd=REPO,
        env=environment,
        check=True,
    )
    subprocess.run(
        ["uv", "run", "pytest", "backend/tests", "--integration"],
        cwd=REPO,
        env=environment,
        check=True,
    )
    environment["LOGIN_RATE_SOURCE_LIMIT"] = "3"
    login_name, password, hash_ms = asyncio.run(test_user(isolated))
    api_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "agenthub.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(api_port),
        "--no-proxy-headers",
    ]
    with (
        server(
            api_command,
            environment,
            directory / "api-0.log",
            api_origin + "/health",
        ),
        server(
            ["pnpm", "-C", "frontend", "dev", "--port", str(vite_port)],
            environment,
            directory / "vite.log",
            origin,
        ),
        httpx.Client(base_url=origin, headers={"Origin": origin}) as client,
    ):
        assert httpx.get(api_origin + "/health/ready").status_code == 200
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        response = client.post(
            "/api/v1/auth/login",
            json={"login_name": login_name, "password": password},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert client.get("/api/v1/auth/me").json() == {
            **response.json(),
            "memberships": [],
        }
        token = client.cookies["agenthub_session"]
        assert client.post("/api/v1/auth/logout").status_code == 403
        assert client.get("/api/v1/auth/me").status_code == 200
        print("Real Vite proxy login/cookie/me and missing CSRF: passed", flush=True)
    with (
        server(
            api_command,
            environment,
            directory / "api-1.log",
            api_origin + "/health",
        ),
        httpx.Client(
            base_url=api_origin,
            headers={"Origin": origin},
            cookies={"agenthub_session": token},
        ) as client,
    ):
        assert client.get("/api/v1/auth/me").status_code == 200
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        assert (
            client.post(
                "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
            ).status_code
            == 204
        )
        client.cookies.set("agenthub_session", token)
        assert client.get("/api/v1/auth/me").status_code == 401
        assert client.get("/health/ready").status_code == 200
        for index in range(3):
            csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
            response = client.post(
                "/api/v1/auth/login",
                json={"login_name": "missing-" + uuid4().hex, "password": "wrong"},
                headers={
                    "X-CSRF-Token": csrf,
                    "X-Forwarded-For": f"198.51.100.{index + 1}",
                    "Forwarded": f"for=198.51.100.{index + 1}",
                },
            )
            assert response.status_code == (401 if index < 2 else 429)
        print(
            "Real HTTP forged forwarding headers "
            "do not bypass source rate limit: passed"
        )
    asyncio.run(sentinel(isolated, preserved))
    result = {
        "database": name,
        "migration": "0004_tenant_authorization",
        "artifact_preserved": preserved,
        "api_restart": "passed",
        "vite_proxy": "passed",
        "argon2_hash_ms_single_sample": round(hash_ms, 2),
        "note": "Local real PostgreSQL/Redis/HTTP; no production or model acceptance",
    }
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print("Retained evidence:", directory.relative_to(REPO))


if __name__ == "__main__":
    main()
