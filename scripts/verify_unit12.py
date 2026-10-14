"""创建新的隔离数据库，执行迁移、测试与真实 API 重启验证。

不执行 DROP、TRUNCATE 或卷删除。测试数据库与诊断证据
按生成的名称保留，供后续获得明确授权后检查。
运行：uv run python scripts/verify_unit12.py
"""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import asyncpg
from sqlalchemy import text
from sqlalchemy.engine import make_url

from agenthub.artifacts.local import LocalFileArtifactStore
from agenthub.artifacts.service import ArtifactService, InternalArtifactScope
from agenthub.core.config import Settings
from agenthub.db.database import Database

REPO = Path(__file__).resolve().parents[1]


async def create_database(settings: Settings, name: str) -> str:
    url = make_url(settings.database_url.get_secret_value())
    connection = await asyncpg.connect(
        url.set(drivername="postgresql").render_as_string(hide_password=False),
        timeout=5,
    )
    try:
        # 生成的名称仅由固定前缀与 UUID 的十六进制值组成。
        await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()
    return url.set(database=name).render_as_string(hide_password=False)


async def sentinel(settings: Settings, artifact_id: str | None = None) -> str:
    database = Database(settings)
    store = LocalFileArtifactStore(settings.artifact_root, settings.artifact_max_bytes)
    await store.open()
    scope = InternalArtifactScope(tenant_id="verification", project_id="restart")
    try:
        service = ArtifactService(database, store, settings.artifact_max_bytes)
        if artifact_id is None:
            # 旧 Schema 哨兵使用显式 SQL，避免当前 ORM 对新增列/表的依赖。
            artifact_id = str(uuid4())
            key = f"obj_{uuid4().hex}{uuid4().hex}"
            content = b"persist across application restart"
            stored = await store.save(key, content)
            async with database.session() as db, db.begin():
                await db.execute(
                    text(
                        "INSERT INTO artifacts (artifact_id, storage_key, tenant_id, "
                        "project_id, content_hash, size_bytes, media_type, status, "
                        "created_at, updated_at) VALUES (:id, :key, 'verification', "
                        "'restart', :hash, :size, 'text/plain', 'ready', now(), now())"
                    ),
                    {
                        "id": artifact_id,
                        "key": key,
                        "hash": stored.content_hash,
                        "size": stored.size_bytes,
                    },
                )
            return artifact_id
        assert (
            await service.read(scope, artifact_id)
            == b"persist across application restart"
        )
        return artifact_id
    finally:
        await store.close()
        await database.close()


def verify_server(environment: dict[str, str], logfile: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with logfile.open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agenthub.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=REPO,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("API exited; inspect retained verification log")
                try:
                    with urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=1
                    ) as response:
                        assert json.load(response) == {"status": "ok"}
                        assert response.headers["X-Request-ID"].startswith("req_")
                    break
                except URLError:
                    time.sleep(0.1)
            else:
                raise TimeoutError("API startup timed out")
            with urlopen(
                f"http://127.0.0.1:{port}/health/ready", timeout=5
            ) as response:
                assert json.load(response) == {
                    "status": "ready",
                    "dependencies": {"postgresql": "ok", "redis": "ok"},
                }
            print("Real Uvicorn liveness/readiness: passed")
        finally:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()  # 仅终止此函数创建的 Popen 进程。
                process.wait(timeout=5)
            if process.returncode != 0:
                raise RuntimeError("API did not shut down cleanly")


def main() -> None:
    os.chdir(REPO)
    settings = Settings()  # type: ignore[call-arg]
    if settings.env not in {"development", "test"}:
        raise RuntimeError("Verification is restricted to development/test")
    name = f"agenthub_unit12_test_{uuid4().hex}"
    directory = REPO / ".data" / "unit12-verification" / name
    directory.mkdir(parents=True, mode=0o700)
    url = asyncio.run(create_database(settings, name))
    print("Created isolated test database:", name, flush=True)
    environment = {
        **os.environ,
        "ENV": "test",
        "DATABASE_URL": url,
        "REDIS_URL": settings.redis_url.get_secret_value(),
        "TEST_DATABASE_URL": url,
        "TEST_REDIS_URL": settings.redis_url.get_secret_value(),
        "ARTIFACT_ROOT": str(directory / "artifacts"),
    }
    for _ in range(2):
        subprocess.run(
            [
                "uv",
                "run",
                "alembic",
                "-c",
                "backend/alembic.ini",
                "upgrade",
                "head",
            ],
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
    isolated = Settings(
        env="test",
        database_url=url,
        redis_url=settings.redis_url,
        artifact_root=directory / "artifacts",
        _env_file=None,
    )
    artifact_id = asyncio.run(sentinel(isolated))
    for index in range(2):
        verify_server(environment, directory / f"api-{index}.log")
        asyncio.run(sentinel(isolated, artifact_id))
    (directory / "result.json").write_text(
        json.dumps(
            {
                "database": name,
                "restart_artifact_id": artifact_id,
                "migration": "0004_tenant_authorization",
                "api_restart": "passed",
                "note": "Isolated deterministic integration evidence; "
                "no model or connector",
            },
            indent=2,
        )
        + "\n"
    )
    print("API restart preserved committed metadata and bytes: passed")
    print("Retained isolated test database and evidence:", directory.relative_to(REPO))


if __name__ == "__main__":
    main()
