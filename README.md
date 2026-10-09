# Enterprise AgentHub

面向研发故障分析与行动的企业 Agent 工作台。当前已实现 **Phase 1 / Unit 1.1–1.2**：
工程、PostgreSQL/Redis、异步数据库与迁移、受控本地产物、统一错误和 request_id。
规格见 [PROJECT_SPEC](docs/PROJECT_SPEC.md)，实际验收见 [IMPLEMENTATION_STATUS](docs/IMPLEMENTATION_STATUS.md)，
设计依据见 [ADR 0001](docs/adr/0001-unit12-storage-and-database.md)。没有开始 Unit 1.3。

## 环境与依赖

Python 3.12（本机实测 3.12.13）、uv 0.12.18、Node 24.21.0、pnpm 12.8.1。
本机 Docker Desktop 4.91.0 / Engine 29.8.0 / Compose 5.5.1 已验证。
后端是根目录单 uv 工程，backend/src/agenthub 正式安装，不需要 PYTHONPATH。
前端保持 Unit 1.1 的 React/TypeScript/Vite 工程。

在仓库根目录运行：

```bash
uv sync --locked
pnpm -C frontend install --frozen-lockfile
```

uv.lock 固定 SQLAlchemy 2.0.54、asyncpg 0.32.0、Alembic 1.20.0、redis-py 8.1.0。
Compose 固定 PostgreSQL 17.11-bookworm、Redis 7.4.11-bookworm 和镜像 digest。
不要用全局 Python 环境安装依赖。

## 本地配置与依赖启动

首次配置：已有 .env 时保留并补充字段，不覆盖原值。

```bash
cp .env.example .env
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

将生成的本地密码填入 POSTGRES_PASSWORD 和 DATABASE_URL 的密码部分；两者必须一致。
.env.example 只有占位符，不是可直接使用的数据库凭据。根目录 .env 不纳入 Git。
DATABASE_URL 必须使用 postgresql+asyncpg，并显式提供 host/database；REDIS_URL 使用 redis/rediss。
连接 URL 在 Settings 中使用 SecretStr。缺失必要 URL 或非法配置会在启动时明确失败。
系统环境变量覆盖 .env；修改配置后重启进程。ENV 允许 development/test/production。

Compose 5 默认从 infra/ 读取 .env。创建一次未跟踪的配置链接（若已存在则保留）：

```bash
ln -s ../.env infra/.env
chmod 600 .env
```

随后可直接运行本 Unit 的标准入口：

```bash
docker compose -f infra/compose.yaml up -d db redis
docker compose -f infra/compose.yaml ps
uv run alembic -c backend/alembic.ini upgrade head
```

也可使用 `docker compose --env-file .env -f infra/compose.yaml up -d db redis`，不需要配置链接。
默认映射 PostgreSQL 127.0.0.1:55432、Redis 127.0.0.1:56379，避免暴露到外部网络。
PostgreSQL 使用命名 Volume，升级/启动不会清空数据。已有 Volume 中的密码不会随 .env 修改自动变更。
Redis 当前是无认证、仅 loopback 的开发依赖，不作为业务数据唯一事实来源。
这些配置不是完整生产部署方案。

## API 与前端

```bash
uv run uvicorn agenthub.main:app --reload
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/health/ready
```

/health 返回 200 和 {"status":"ok"}，只检查进程；/health/ready 检查 PostgreSQL SELECT 1 与 Redis PING。
两项依赖分别有超时，失败返回 503、dependency_unavailable 和依赖状态，不泄漏连接信息。
请求忽略客户端 X-Request-ID，服务端生成 req_ UUID，在响应头、统一错误正文及 JSON 日志中关联。
统一错误包含 code/message/request_id/details；验证错误保留字段位置和类型，不回显原始输入。

```bash
pnpm -C frontend dev
pnpm -C frontend typecheck
pnpm -C frontend build
```

前端当前仍是独立的最小项目页面，没有产物或登录业务接口；build 输出被 Git 忽略。
业务 API 的 /api/v1 前缀由后续 Unit 接入，已有根路径 health 保持兼容。

## 数据库与内部产物合同

lifespan 创建并关闭每进程 Engine/SessionFactory、Redis 客户端和存储根目录 fd。
Database.session() 为每个内部任务创建 Session；get_session 可用于后续请求依赖。
service 通过 session.begin() 管理短事务，关闭 Session 会回滚未提交工作；没有全局 AsyncSession。
expire_on_commit=False 只保留已加载字段快照，跨 Session 更新仍须重新读取。

ArtifactStore 只存字节，ArtifactService 协调 artifacts 元数据和范围检查。
保存顺序为 pending 提交 → 临时文件 → 正式发布 → ready 提交，不跨文件 IO 持有数据库事务。
正式文件已保存但 ready 提交失败时返回 completion unconfirmed，并保留文件供诊断，不盲目删文件或重试。
未发布失败尽力清理自有临时文件并标记 failed；failed 更新失败则可能仍为 pending。

本地存储默认根目录 .data/artifacts、最大 16 MiB，使用服务端随机 key。
逐级 no-follow 目录检查、dirfd 相对操作和文件类型检查阻止路径穿越/符号链接逃逸；
os.link 原子发布并拒绝目标占用，文件与目录 fsync。根目录须归当前进程用户所有，禁止组/其他用户写入。
读取只接受 artifact_id + 可信内部资源上下文，检查 ready、归属、文件存在、hash/size。
文件 IO 和 hash 校验在工作线程运行；取消不能终止 OS 线程，会等待本次 IO 完成再允许资源关闭。

当前归属上下文不是认证：只能由可信服务端代码构造，不能从匿名 HTTP 或模型参数创建。
没有上传/下载 HTTP API，也不返回主机文件路径。Unit 1.4 补 Principal、ACL 与租户/项目外键。

只读诊断（先正常启动过应用，使存储目录存在）：

```bash
uv run python -m agenthub.artifacts.diagnose
```

报告过期 pending、未确认正式文件、临时残留、孤儿文件、缺失/损坏和不安全条目。
命令不创建存储根目录、不更新数据库、不删除文件。它使用服务端数据库凭据，属于内部管理入口。
没有自动恢复/清理 Worker、Outbox、分布式锁或跨文件与数据库的原子事务。

## 验证

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy backend/src
uv run pytest backend/tests
uv run alembic -c backend/alembic.ini check
```

默认 pytest 运行 43 个配置/API/本地文件测试，明确 deselect 19 个依赖真实服务的测试；
这不等于数据库集成验收。完整隔离验证入口：

```bash
uv run python scripts/verify_unit12.py
```

此入口要求 ENV=development/test 和具备 CREATE DATABASE 权限的本地开发账户：
创建新的 agenthub_unit12_test_<uuid> 数据库，从零迁移、重复升级，再运行全部 62 个测试；
启动/关闭真实 Uvicorn 两次，并验证已提交产物仍可读。不会清空现有数据库。
测试数据库及 .data/unit12-verification 下的结果/日志被保留，后续清理需单独授权。
故障测试有意留下 pending/failed 元数据和临时材料；它们与默认开发业务库分离。

只有 artifacts 和 alembic_version 两张表。已执行的首个迁移保持不变，第二个迁移只修正 CHECK 名称；
未执行 downgrade。当前实测平台为 macOS/Apple Silicon；POSIX 文件系统实现不承诺 Windows 兼容，
原子发布也不等于所有断电场景下的持久性保证。本地确定性验证没有 Mock/Live 模型或外部连接器，
不证明生产部署、模型质量、完整认证/ACL、长期任务恢复或外部写入可靠性。
