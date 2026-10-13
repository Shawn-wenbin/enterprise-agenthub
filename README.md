# Enterprise AgentHub

面向研发故障分析与行动的企业 Agent 工作台。当前已实现 **Phase 1 / Unit 1.1–1.3**：
工程、PostgreSQL/Redis、异步数据库与迁移、受控本地产物、统一错误和 request_id，
以及 User/Session 登录、撤销、CSRF/Origin 与共享登录限流。
规格见 [PROJECT_SPEC](docs/PROJECT_SPEC.md)，实际验收见 [IMPLEMENTATION_STATUS](docs/IMPLEMENTATION_STATUS.md)，
设计依据见 [ADR 0001](docs/adr/0001-unit12-storage-and-database.md)。本次止于 Unit 1.3，未实现租户/角色授权。

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
uv run uvicorn agenthub.main:app --reload --no-proxy-headers
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

前端当前仍是最小项目页面，没有正式登录 UI；build 输出被 Git 忽略。
Vite 在 127.0.0.1:5173 将 `/api` 原样代理到 127.0.0.1:8000，保留 Cookie 和浏览器 Origin。
非默认 API 端口可在启动 Vite 时配置服务端环境变量 AGENTHUB_API_PROXY_TARGET；该值不进入客户端包。
使用相对路径 `/api/v1/...`，不启用通配符凭据 CORS。生产需同源 HTTPS 反向代理，不能使用 Vite 代替。
来源限流使用 ASGI 连接地址；必须使用上述 `--no-proxy-headers`，当前不会按任意 XFF 拆分客户端。
开发代理后的客户端共用后端看到的 loopback 来源额度；生产代理的可信来源传递需单独设计和验证。

## 登录与安全请求

当前 API：POST `/api/v1/auth/login`、GET `/api/v1/auth/me`、POST `/api/v1/auth/logout`、
GET `/api/v1/auth/csrf`。身份只有 id/login_name，密码及 Session Token 不进入 JSON 响应。
账号标识 strip/lower 后限 ASCII 1–128 字符（字母/数字开头，其余允许 `_.@+-`）；数据库唯一和
CHECK 约束保证一致。密码输入 1–256 字符，登录 JSON 总大小最多 4096 bytes（含 chunked）。

没有公共注册或正式 demo seed。`backend/tests/test_auth_integration.py` 的 user fixture 在指定
隔离库里创建唯一账号和随机密码，通过 PasswordSecurity.hash 存 Argon2id；验证脚本同样只在新库
创建测试用户，不输出密码。运行下方完整脚本即可自动验证登录，无需默认账号或生产密码。

同源浏览器的最小调用方式（loginName/password 使用受控测试 fixture 准备的值）：

```javascript
async function csrf() {
  const response = await fetch('/api/v1/auth/csrf', {
    credentials: 'same-origin',
    headers: { 'X-CSRF-Fetch': '1' },
    cache: 'no-store',
  })
  if (!response.ok) throw new Error('CSRF credential unavailable')
  return (await response.json()).csrf_token
}
const loginResponse = await fetch('/api/v1/auth/login', {
  method: 'POST', credentials: 'same-origin',
  headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': await csrf() },
  body: JSON.stringify({ login_name: loginName, password }),
})
if (!loginResponse.ok) throw new Error('Login failed')
const identity = await fetch('/api/v1/auth/me', { credentials: 'same-origin' })
const logoutResponse = await fetch('/api/v1/auth/logout', {
  method: 'POST', credentials: 'same-origin',
  headers: { 'X-CSRF-Token': await csrf() },
})
```

浏览器自动设置修改请求的 Origin；JavaScript 不应自行设置这个受限 Header。
命令行 HTTP 客户端必须显式设置与 TRUSTED_ORIGINS 一致的 Origin，先用 Cookie jar 调用 csrf，
再同时发送取得的 Cookie 与 X-CSRF-Token。登录成功清除预登录 Cookie；下一次 csrf 返回 Session
绑定签名。缺失/错误 CSRF 或非法/缺失 Origin 均拒绝；SameSite 不能代替这两项服务端检查。
所有后续 Cookie 业务 Router 必须挂到 `api/router.py` 的受保护入口。

Session 固定 8 小时，过期、撤销或用户禁用立即影响后续认证；logout 只撤销当前 Session，
重复/失效退出返回 401。数据库创建/撤销提交失败为 503，无成功 Cookie 或删除 Cookie。
当前使用每请求短查询的提交态快照，撤销不回滚已开始请求或已产生效果。

Argon2id 使用 pwdlib 0.3.1 / argon2-cffi 25.1.0：64 MiB、t=3、p=4，库生成 16-byte 盐、32-byte hash。
默认每 API 进程最多 2 个密码计算在线程中运行；不存在用户也验证 dummy hash。Redis 原子固定窗口
默认 300 秒：来源 60 次、规范化账号 10 次；成功/失败尝试都计数，返回 429 后等待窗口，不做账号封禁。
Redis 不可用返回 503，不降级成进程内计数。来源与账号写入 Redis 前 hash，不保存原始密码/Token。

生产必须 ENV=production + Secure/HttpOnly/__Host- Cookie + HTTPS 精确 Origin + 独立 CSRF_SECRET +
非默认数据库/Redis 认证凭据；路径固定 /，Domain 不设置，SameSite 默认 lax，none 必须 Secure。
配置示例的无认证本地 Redis 不能直接用于生产。安全组合在启动时校验，包含注入 Settings 的情况。
CSRF_SECRET 为 secrets.token_hex(32) 生成的随机值，所有 API 进程共享；轮换使旧 CSRF 凭证失效，
但不撤销 PostgreSQL Session，客户端重新获取 csrf 即可。dev/test 未配置时每进程生成临时密钥，
重启或跨进程会让旧 CSRF 失效；Session 仍可查验。预登录签名凭证有效 10 分钟，不单独持久化/撤销。

认证方案遵循 [OWASP CSRF 指南](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)，
签名/时效复用 [ItsDangerous](https://itsdangerous.palletsprojects.com/en/stable/timed/)，
代理配置核对 [Vite server.proxy](https://vite.dev/config/server-options#server-proxy)，
原子限流核对 [Redis INCR](https://redis.io/docs/latest/commands/incr/)。

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

默认 pytest 当前运行 67 个测试，明确 deselect 36 个依赖真实服务的测试，不将未运行项算作通过。
完整 Unit 1.3 验证入口：

```bash
uv run python scripts/verify_unit13.py
```

脚本要求 ENV=development/test 和本地 CREATE DATABASE 权限，自动使用 UTF-8 locale：
创建新的 agenthub_unit13_test_<uuid>，先升级到 Unit 1.2 并保存真实 Artifact，再升级 head、
重复升级、Alembic check，运行全部 103 个测试。Redis 使用本地服务的逻辑库 15 和随机 test namespace，
不 flush 或改动业务键。启动本次 Uvicorn/Vite 空闲端口进程，验证代理 Cookie/login/me、重启后身份、
logout 失效、非法 CSRF 和伪造 Forwarded/XFF 无法绕过来源限流；只停止脚本自己的进程组。
测试库及 .data/unit13-verification/<database>/ 的无敏感 result.json/日志保留，清理需另行授权。
也可配置 TEST_DATABASE_URL（只允许 agenthub_unit12_test_<uuid> 或 agenthub_unit13_test_<uuid>）
与 TEST_REDIS_URL 后运行 `uv run pytest backend/tests --integration`；先确保隔离库已 upgrade head。
旧 verify_unit12.py 仍可验证产物/health 回归，但不会验证 Vite 认证链路。

迁移链为 0001_artifacts → 0002_constraint_names → 0003_users_sessions；只有 artifacts/users/sessions
和 alembic_version 四张表。已有两条迁移未修改，不启动 create_all，不自动 downgrade 或删除数据。
若当前终端为 ASCII locale，迁移命令前设置 `LC_ALL=en_US.UTF-8`，避免 Alembic 读取中文 ini 注释失败。
本地测试覆盖真实 PostgreSQL/Redis/HTTP，HTTPS Cookie 属性由 ASGI HTTPS 客户端验证；尚未验证真实
浏览器 Cookie 行为、生产 TLS/反向代理、多宿主部署和负载性能。没有 Mock/Live 模型，不证明生产落地。
Tenant/Project/Membership/Role/Resource ACL/Principal 和 dev/test 幂等 seed 留待 Unit 1.4；
SSO/OIDC、完整登录 UI、会话管理和完整 IAM 不在本 Unit 范围。
