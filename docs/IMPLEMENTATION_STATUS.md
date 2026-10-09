# IMPLEMENTATION_STATUS

最后更新：2026-10-10  
当前 Phase：1  
当前 Unit：1.2 — 数据库、迁移与基础产物存储  
Unit 状态：**verified**  
当前模式：macOS 本地确定性工程测试 + 真实 PostgreSQL/Redis 集成；无 Mock/Live 模型或外部连接器。

## 范围与基线

本次仅实现 Unit 1.2，未进入 Unit 1.3，未 commit/push/部署或清理已有数据库/Volume。
开工 Git 工作区干净，基线提交 be175db；已有本地 .env 仅含 ENV，保留其内容并补充随机本地密码和连接配置，未输出或提交密码。
原有 AGENTS 保持不变。Unit 1.1 的 14 个测试和 Python 包导入重新通过；前端 typecheck/build 也重新通过。
规格与计划无实质冲突；PROJECT_SPEC 15.1 补当前产物/health 合同，未改变整体架构。
.gitignore 原先忽略整个 docs/，现取消该忽略使规格、计划、STATUS 和 ADR 可版本化；.data、所有 .env 仍忽略。
Unit 1.1 的完整验收历史保留在本文后半部分。

## 实际实现与关键文件

- infra/compose.yaml：仅 PostgreSQL/Redis；版本 + digest 固定、健康检查、loopback 映射、命名 PostgreSQL Volume。
- core/config.py、.env.example：必填 SecretStr 数据库/Redis URL、连接池、就绪超时、本地存储根目录/大小/pending 阈值。
- db/database.py：每进程 AsyncEngine/async_sessionmaker、独立 Session context 和后续请求依赖；业务自行 session.begin()。
- db/base.py、db/metadata.py：集中 metadata 与当前模型发现。
- backend/alembic.ini、backend/migrations/：asyncpg 连接/run_sync/NullPool 迁移环境；只建立 artifacts 元数据业务表。
- 0001_artifacts：最小元数据与状态/size/ready 约束、归属/状态索引；0002_constraint_names：不改数据地修正 CHECK 名称，保留已执行首个迁移。
- artifacts/store.py：可替换的字节接口和存储失败结果；artifacts/local.py：POSIX 文件卷、随机 key、no-follow/dirfd、no-clobber hardlink 发布、fsync、线程卸载。
- artifacts/models.py、service.py：资源归属/hash/size/UTC 元数据、pending/ready/failed、短事务一致性策略、受控读取及只读诊断。
- artifacts/diagnose.py：内部只读 CLI，禁止创建根目录、数据库变更与文件删除。
- core/errors.py、http_errors.py、request_context.py：统一错误、验证适配、可信服务端 request_id、响应头与实际 JSON 日志。
- main.py、api/health.py：lifespan 管理 Engine/Redis/store，兼容 /health，并新增有超时的 /health/ready。
- backend/tests/：43 个本地测试 + 19 个真实数据库/Redis 集成测试；scripts/verify_unit12.py：新隔离数据库、完整迁移、测试与真实 API 重启。
- README、docs/adr/0001-unit12-storage-and-database.md：已验证的运行入口、安全内部调用合同和设计取舍。

文件保存采用 pending 短事务提交 → 临时文件实际校验 → 不覆盖的原子发布 → ready 短事务提交。
文件 IO 时数据库 Session 已关闭；发布后 ready 提交失败保留文件，不报告成功、不盲删。
未发布失败尽力清理自有临时文件并标记 failed；failed 更新失败保留可诊断 pending。
只有 ready 且归属匹配才读取；缺失/损坏明确失败。没有产物 HTTP 上传/下载接口。
InternalArtifactScope 不是认证，只允许可信服务端调用；Unit 1.4 补 Principal/ACL 和归属外键。

## 实际环境与锁定版本

macOS / darwin arm64，项目 Python 3.12.13、uv 0.12.18；本机默认 Python 3.14.7 未用于项目安装。
Docker Desktop 4.91.0，Engine 29.8.0，Compose 5.5.1。
新增安装：SQLAlchemy 2.0.54、greenlet 3.5.6、asyncpg 0.32.0、Alembic 1.20.0、redis-py 8.1.0、pytest-asyncio 1.4.0；uv.lock 锁定全部传递依赖。
既有 FastAPI/Pydantic/前端版本未升级。实现核对基于 SQLAlchemy/Alembic/Redis/Compose 官方文档，链接见 ADR。

实际容器内版本：PostgreSQL 17.11 (Debian 17.11-1.pgdg12+2)、Redis 7.4.11。
固定镜像：

- postgres:17.11-bookworm@sha256:3645570cccdfa447589da9f57dd740faa29b30938e861289a5574b6ca6b03826
- redis:7.4.11-bookworm@sha256:4fa24486b8bcca8eec45ee0eb166edc674795e53a2b53d1a9ef263eecebaac85

PostgreSQL 127.0.0.1:55432，Redis 127.0.0.1:56379；两个容器 healthy。
本次新建 enterprise-agenthub_postgres_data；原有 minigameserver-db-1/MySQL 未操作。
Compose 5 从 infra/ 读取默认 .env；本地未跟踪 infra/.env -> ../.env 链接使标准命令可用。

## 验证结果 — verified

以下均实际执行；失败首轮不作为通过证据，列出最终结果。

- uv sync、uv sync --locked：成功，依赖已安装且锁文件一致。
- uv run ruff check .：All checks passed。
- uv run ruff format --check .：36 files already formatted（完成代码检查时，后续仅文档更新）。
- uv run mypy backend/src：Success，19 个源码文件，strict 未放宽。
- uv run pytest backend/tests：43 passed、19 deselected；默认明确排除需要真实服务的集成用例，没有静默算成通过。
- docker pull 固定 PostgreSQL/Redis 官方镜像：成功，记录并使用实际 digest。
- docker compose -f infra/compose.yaml up -d db redis：成功；ps 显示两项 healthy 且只映射 loopback。
- docker compose exec db postgres --version、exec redis redis-server --version：实际版本如上。
- uv run alembic -c backend/alembic.ini upgrade head：开发库从零升级成功，随后执行修正迁移并重复 upgrade head，未重建或清空数据。
- uv run alembic -c backend/alembic.ini check：No new upgrade operations detected；另有真实约束名称断言弥补 CHECK 名称自动比较的限制。
- uv run python scripts/verify_unit12.py：最终新数据库 agenthub_unit12_test_b2dc0a8408d04c68bb165125c9b71411 上，从零执行两项迁移且重复 upgrade head 成功，全部 62 passed。
- 该脚本实际启动/关闭 Uvicorn 两次：/health 与 /health/ready 均 200；重新创建 Engine/应用进程后，已提交元数据和同一文件内容仍可读取。
- 最终源码再次在上述隔离库执行 pytest backend/tests --integration：62 passed；没有清空隔离库或删除断言。
- 默认开发 .env 的实际 ASGI lifespan + HTTP：/health、/health/ready 均 200，响应有服务端 X-Request-ID 和对应 JSON 日志。
- uv run python -m agenthub.artifacts.diagnose：默认开发库 issues=[]，mode=read_only；不更新数据库或删除文件。
- pnpm -C frontend typecheck、pnpm -C frontend build：成功，Vite 8.3.4；前端源码未修改。
- git diff --check、AGENTS/前端无改动核对、暂存区无改动核对：通过；57 个可纳入版本管理的文件未含本地实际数据库密码。.env、infra/.env、.data、虚拟环境和构建产物的忽略规则已核对。

## 正常与故障路径证据

数据库验证了事务提交、业务异常回滚、唯一约束失败回滚、Session 永久关闭、两个独立 Session 的未提交隔离、连接归还、lifespan Engine dispose/Redis close、迁移重放保留数据及最小表集合。

产物验证了正常写读及 SHA-256/size、UTC、跨 tenant/project 拒绝、pending/failed 禁读、路径穿越拒绝、根目录/最终文件 symlink 拒绝、私有根权限、FIFO 拒绝、大小上限、并发 key 冲突不覆盖。
受控注入分别验证 pending 提交失败不写文件、临时写入失败、发布失败、清理失败可追溯、已发布但 ready 提交失败、failed 提交失败、正式文件缺失与 hash/size 损坏。
只读诊断识别 stale_pending、unconfirmed_file、temporary、orphan_object 和缺失/损坏；测试比较调用前后所有本次文件字节与数据库状态，保持不变。
取消测试确认线程结束后才释放控制，不让取消后仍在运行的线程使用已关闭目录 fd。

API 验证了 liveness 与依赖无关、readiness 成功、真实不可达 PostgreSQL/Redis 各返回 503、受控超时、验证错误、业务/依赖/未知异常的统一响应、每请求独立 request_id、日志关联以及秘密不回显。
真实 Uvicorn 日志也输出 request_completed JSON，不只依赖测试 caplog。

## 残留、未验证项与限制

默认开发库没有未解决 pending/orphan；只读诊断为空。
本次三个隔离测试数据库保留，前两轮名称为 agenthub_unit12_test_3212395c3be64cc691ee49c58e4599c7 和 agenthub_unit12_test_2c8e24a07e1e408385bb14dfd555cf24，最终目标见上。
故障用例有意保留 pending/failed 元数据，pytest 临时目录中也可能保留本次残留/损坏/孤儿文件；每个用例使用独立临时存储目录，这些元数据不能作为统一业务库进行恢复。
.data/unit12-verification/<database>/ 保存真实 API 日志、result.json 和 ready 的重启验证产物，全部被 Git 忽略。
没有自动清理测试数据库/Volume；后续清理需指定目标并单独授权。

- **unverified**：Linux/Windows、网络文件系统、真实断电/磁盘硬件故障下的持久性。当前本地 POSIX 实现经过 macOS 测试；原子发布不等于任意断电下零丢失。
- **unverified**：生产部署、负载性能、模型质量和外部连接器；本次没有 Mock/Live 模型，不能推广为真实生产落地。
- **unverified / 后续范围**：完整认证、RBAC/ACL、可信 Principal、tenant/project 外键、后台恢复、Worker/Outbox 与外部写入可靠性。
- 当前接口使用有大小上限的 bytes，不提供流式 HTTP 上传；诊断是内部并发快照，没有修复或垃圾回收。
- 存储根目录需由可信本机用户控制；不宣称隔离恶意同 UID 宿主进程或任意代码。
- Session 提交响应丢失仍可能产生不确定结果；本次保留记录/文件、返回未确认，不宣称数据库与文件跨系统 exactly-once。
- **blocked**：无当前 Unit 的必要验收阻塞。

## 首轮问题及处理

uv 默认缓存/Docker socket 沙箱拒绝，均按权限流程获准后验证；没有绕过沙箱或使用全局环境。
Compose 标准命令初次因配置目录 lookup 找不到密码失败；显式 --env-file 验证成功后，创建未跟踪的 infra/.env 链接，再重跑标准命令成功。
首轮格式/静态检查及 AsyncEngine 实例方法故障注入失败，分别修正格式、类型参数和类级受控注入；保留所有失败路径断言。
实际数据库 CHECK 名称重复加前缀，Alembic check 未报出该差异；保留已执行 0001，追加仅重命名约束的 0002，并通过直接 PostgreSQL 名称断言验证。
这些问题均已解决，最终通过结果见上。

## Unit 1.3 进入条件与 Phase Gate

Unit 1.1、1.2 verified；Phase 1 总体仍 **unverified**，Unit 1.3–1.8 未实现。
Unit 1.3 需要用户另行授权，并复用当前 Settings、Engine/独立 Session、Alembic 迁移链、Redis 生命周期、统一错误/request_id。
随后建立 users/sessions、密码 hash、服务端 session TTL/撤销、Cookie/Origin/CSRF 与开发同源代理。
完整 tenant/project 归属约束和授权入口仍在 Unit 1.4 补齐。本次止于 Unit 1.2。

---

## Unit 1.1 验收历史（2026-10-09，原记录保留）


最后更新：2026-10-09  
当前 Phase：1  
当前 Unit：1.1 — 工程初始化与配置  
Unit 状态：**verified**（当前 Unit 的必要验收均有实际通过证据）  
当前模式：本地工程验证；没有 Mock/Live 模型或外部连接器。

## 仓库基线与范围

开工时 Git 为 `main...origin/main`，工作区干净，已有 `AGENTS.md`、`docs/PROJECT_SPEC.md`、`docs/IMPLEMENTATION_PLAN.md` 和已跟踪的 `.DS_Store`。没有工程代码、锁文件、STATUS 或 `docs/adr/`。既有文件保持不变，未 commit、push、部署或开始 Unit 1.2。

PROJECT_SPEC 第 17.1 节列出的是整体项目配置目标；遵照 Unit 1.1 及本次授权，只定义当前使用的 `ENV`，不提前要求数据库、认证、模型等配置。总体架构与产品规格未改动。

## 实现内容与关键文件

- `pyproject.toml`、`.python-version`、`uv.lock`：根目录 uv 工程，Python `>=3.12,<3.13`；distribution 为 `enterprise-agenthub`，版本 `0.1.0`。运行依赖与 `dev` 依赖组分开，配置 Ruff 和 strict mypy。
- `backend/src/agenthub/`：通过 `uv_build==0.12.18` 的 `module-root=backend/src`、`module-name=agenthub` 实现 src layout 安装，不设置 `PYTHONPATH`。
- `backend/src/agenthub/main.py`：轻量 `create_app(settings=None)` 与导出的 `app`；应用装配不读取配置，lifespan 启动时加载和校验 Settings，支持显式注入。
- `backend/src/agenthub/api/health.py`：Pydantic 响应 Schema，`GET /health` 返回 HTTP 200 和 `{"status":"ok"}`；不访问任何外部依赖或输出配置。
- `backend/src/agenthub/core/config.py`：Pydantic Settings；只接受 `ENV=development/test/production`，缺省为 development，非法/空值明确失败；环境变量覆盖工作目录 `.env`，统一 `get_settings()` 缓存入口可清理。
- `backend/tests/`：14 个配置和 HTTP 行为测试，覆盖默认值、有效/无效环境、dotenv 来源优先级、缓存刷新、App Factory 注入和启动校验。测试隔离本地 `.env`、环境变量与缓存。
- `frontend/package.json`、`frontend/pnpm-lock.yaml`、`.node-version`：React/TypeScript/Vite/pnpm 工程和锁定版本，提供 dev/typecheck/build/preview 脚本。
- `frontend/tsconfig*.json`：浏览器与 Vite 配置分别类型检查，启用 strict、未使用变量检查等约束；生成的类型检查缓存位于忽略的 node_modules。
- `frontend/index.html`、`frontend/src/`、`frontend/vite.config.ts`：可启动的 React 入口、官方 React 插件与最小项目页面，没有后端请求或业务功能。
- `.env.example`、`.gitignore`、`README.md`：当前配置示例、凭据/缓存/环境/构建输出忽略规则，以及可执行的安装、启动、验证与配置生命周期说明。
- `frontend/pnpm-workspace.yaml`：pnpm 12 安装时生成的项目配置，仅记录 `vite@8.3.4` 的精确发布等待期例外，没有多包目录或未来模块。pnpm 12 的锁文件包含包管理器环境及应用依赖两个 YAML 文档，保持工具原始格式。

Tailwind CSS 延后到 Unit 1.8，当前仅使用少量原生 CSS；TanStack Query、OpenAPI 契约生成与业务 UI 尚未实施。没有数据库、Compose、Redis、Celery、LangGraph、模型、检索、认证或工具调用基础设施。

## 实际环境与依赖选择

- 验证平台：macOS / Apple Silicon（darwin arm64）。
- 本机默认 `python3` 为 3.14.7；项目实际使用现有 Python **3.12.13**，由 uv 创建仓库 `.venv`。
- uv **0.12.18**；Node.js **24.21.0**；pnpm **12.8.1**。
- 实际安装：FastAPI **0.143.0**、Uvicorn **0.54.0**、Pydantic **2.14.0**、pydantic-settings **2.15.0**。
- 静态/测试工具：Ruff **0.16.10**、mypy **2.4.0**、pytest **9.1.1**、httpx2 **2.13.1**。
- 前端：React/react-dom **19.3.0**、Vite **8.3.4**、@vitejs/plugin-react **6.1.2**、TypeScript **6.0.2**；@types/node **24.19.1** 与本机 Node 24 对齐。
- 已读取 PyPI/npm 官方注册表元数据并核对 [Vite Node.js 要求](https://vite.dev/guide/)、[React TypeScript 官方模板](https://github.com/vitejs/vite/blob/main/packages/create-vite/template-react-ts/package.json)、[pnpm 兼容表](https://pnpm.io/installation)、[uv 构建配置](https://docs.astral.sh/uv/concepts/build-backend/)、[Starlette TestClient](https://starlette.dev/testclient/)。TypeScript 采用官方模板的 6.0.2，没有随注册表最新主版本自动升级。最终依赖以实际安装、锁文件及检查结果为依据。

## 验证记录

以下结论均为实际执行结果；除特别注明外，命令在仓库根目录运行。

### 后端 — verified

- `uv sync`：成功创建 `.venv`，构建并安装 `enterprise-agenthub`，生成完整 `uv.lock`；切换 TestClient 开发依赖后再次同步成功。
- `uv run python -c "import agenthub; print(agenthub.__file__)"`：成功，路径指向安装后的 `backend/src/agenthub/__init__.py`。
- 在 `/private/tmp` 执行项目 `.venv/bin/python -I -c "import agenthub; import sys; from importlib.metadata import version; print(sys.version); print(agenthub.__file__); print(version('enterprise-agenthub'))"`：成功，Python 3.12.13、distribution 0.1.0；隔离模式和仓库外工作目录证明导入不依赖当前目录或 `PYTHONPATH`。
- `uv run ruff check .`：最终 `All checks passed!`。
- `uv run ruff format --check .`：最终 `13 files already formatted`。
- `uv run mypy backend/src`：`Success: no issues found in 6 source files`。
- `uv run pytest backend/tests`：最终 **14 passed**，无警告。
- 通过 `uv run python -` 创建 `ENV=invalid` 的独立 Python 子进程，执行 `from agenthub.main import app; print(app.title)`：导入成功，输出项目标题且 stderr 为空，验证导入不提前读取或校验环境配置。
- `uv build`：从 sdist 成功构建 wheel；额外检查 wheel 包含 `agenthub` 的 main/health/config 模块，没有测试、前端或 `.env`。构建文件只在已忽略的 `dist/`。
- `uv sync --locked`：成功，冻结安装前后 `uv.lock` SHA-256 不变：`5f5ab0f51cc06c13a2763c22dfec8e2db3d3c8f93bfd4696ea2d1f55b6e35481`。

### 前端 — verified

- `pnpm -C frontend install`：真实安装成功，生成锁文件，未全局安装项目依赖。
- `pnpm -C frontend typecheck`：最终 `tsc -b` 退出码 0。
- `pnpm -C frontend build`：包含 TypeScript 检查，Vite 8.3.4 production build 成功，输出 index.html、CSS、JS 至已忽略的 `frontend/dist/`。
- `pnpm -C frontend install --frozen-lockfile`：成功，锁文件与供给链策略检查通过，安装前后 SHA-256 不变：`0fe465287442967f3ab49cc491622a2d8484fcb3fe573507dcfeaa715ea0f279`。

### 实际开发服务器与 HTTP — verified

在 `uv run python -` 的临时验证脚本中启动下列真实子进程，使用本机空闲端口避免影响已有服务：

```bash
uv run uvicorn agenthub.main:app --reload --host 127.0.0.1 --port 64025
pnpm -C frontend dev --host 127.0.0.1 --port 64026 --strictPort
```

- Uvicorn reload 进程和 API 进程均启动成功，lifespan 完成。
- 实际请求 `http://127.0.0.1:64025/health`：HTTP 200，精确响应 `{"status":"ok"}`。
- Vite dev server 启动成功；请求 `/`：HTTP 200，包含项目标题与 React root 入口。
- 请求 `/src/main.tsx`、`/src/App.tsx`：均 HTTP 200，Vite 实际转换并提供 React 入口与两段项目文案。
- 验证脚本退出前向本次创建的进程组发送 SIGINT，并等待子进程结束；Vite 的 SIGINT 提示属于预期关闭，没有遗留本次开发服务。

### 工程与范围 — verified

- `git check-ignore -v` 验证 `.env`、`.env.production`、`.venv`、Python 三类检查缓存、pyc、node_modules、dist、TypeScript 缓存和 `.secrets` 被忽略。
- `.env.example`、`pyproject.toml`、两个锁文件没有被忽略；示例文件只含 ENV，不含真实凭据。
- 已跟踪的 `.DS_Store` 不会因新增忽略规则自动取消跟踪；本次保留该原有文件。
- 当前只增加 Unit 1.1 所需源码和测试，无后续业务模块占位。原有 AGENTS、规格与实施计划未修改，无 commit/push。
- `git diff --exit-code -- AGENTS.md docs/PROJECT_SPEC.md docs/IMPLEMENTATION_PLAN.md .DS_Store`、`git diff --cached --exit-code`、`git diff --check` 均通过；通过 `git ls-files --others --exclude-standard` 和本地脚本检查 28 个新增文件，未暴露虚拟环境、Node 依赖、缓存或构建输出。检查 Python 工程元数据与新增文件换行格式也通过。

## 首轮失败、警告及处理

- uv 默认缓存位于工作区外，首次 `uv python find 3.12` 被沙箱拒绝。请求并获准使用正常缓存后成功；没有绕过权限或改用全局安装。
- 注册表版本查询在沙箱内被网络权限拒绝；一个 pnpm view 查询被中止，尝试的旧式 `--fetch-retries` 参数也被 pnpm 12 拒绝。随后通过获准的官方注册表读取完成版本核对，不将这些初始查询算作成功验证。
- 首轮 Ruff lint 失败：main.py 一处 89 字符行触发 E501；format check 也指出同一处换行。按 formatter 建议修改后，两项真实复验通过。
- 首轮 pytest 已有 14 passed，但当前 Starlette 对 httpx 发出弃用警告。核对实际源码和官方 TestClient 文档后，开发依赖改为 httpx2，重新生成锁文件并复验，14 passed 且无警告；未过滤警告。
- 首轮沙箱内 `pnpm -C frontend typecheck` 无法访问默认 store，触发重装并遇到网络拒绝，已停止该失败尝试。使用获准缓存环境重跑 typecheck、build 及冻结安装，全部通过；未放宽 TypeScript 检查。
- 所有上述问题均已解决，当前必要验收没有 blocked 项。

## 未验证项与限制

- **unverified**：浏览器内人工视觉检查与交互测试；已验证开发服务器、React 模块转换和 production build。本 Unit 没有业务交互，因此这些不作为当前退出阻塞。
- **unverified**：Linux/Windows 环境复现、生产部署；本次通过证据仅来自本机 macOS。
- 数据库、Redis、认证、Worker、SSE、模型、Mock 业务流程和 Live 集成均未实施/未验证，不能从 health 或本次确定性测试推导其可用性。
- `.env` 按启动工作目录读取；缓存配置改变后需重启或显式清理。README 已说明根目录启动要求。
- 网络和默认工具缓存需要本环境沙箱授权；首次复现需具备包注册表可达性或完整本地缓存。

## Phase Gate 与 Unit 1.2 进入条件

Phase 1 Gate：**unverified**。仅 Unit 1.1 verified，Unit 1.2–1.8 尚未实施，身份、后台任务和最小工作台的阶段退出条件未通过。

进入 Unit 1.2「数据库、迁移与基础产物存储」需要：

1. 用户另行授权 Unit 1.2；Unit 1.1 的代码与验收证据已齐备。
2. 准备并实际验证 Docker/Compose 与 PostgreSQL、Redis 测试环境可用性，核对和固定兼容版本；本次没有检查这些外部运行条件。
3. 明确开发/测试连接配置和文件卷位置，按当前功能建立 AsyncSession 生命周期与 Alembic 迁移，不提前铺设未来全量表。
4. 为文件卷版 ArtifactStore 准备可写的受控目录，并验证路径穿越拒绝；在该 Unit 实现基础错误结构/request_id 和依赖健康检查。

本次工作止于 Unit 1.1。
