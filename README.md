# Enterprise AgentHub

面向研发故障分析与行动的 Agent 工作台。本仓库当前只实现 **Phase 1 / Unit 1.1：工程初始化与配置**：可安装的 Python 包、进程健康检查、统一配置与最小 React 页面。产品规格见 [PROJECT_SPEC](docs/PROJECT_SPEC.md)，实施证据见 [IMPLEMENTATION_STATUS](docs/IMPLEMENTATION_STATUS.md)。

## 环境与依赖

- Python **3.12**，由 `.python-version` 和 `requires-python` 约束；uv **0.12.18** 为本次验证版本。
- Node.js **24**，`.node-version` 记录本次验证版本 **24.21.0**；pnpm **12.8.1** 由前端 `packageManager` 固定。
- Python 依赖由根目录 `pyproject.toml` 管理，`uv.lock` 锁定解析结果；前端依赖使用确切版本和 `frontend/pnpm-lock.yaml`。
- 运行依赖与 Python `dev` 依赖组、前端 `devDependencies` 分开。pytest/httpx2 仅用于本地 HTTP 与配置测试，httpx2 符合当前 Starlette TestClient 的依赖合同。

在仓库根目录运行：

```bash
uv sync
uv run python -c "import agenthub; print(agenthub.__file__)"
pnpm -C frontend install
```

复现已有依赖时使用 `uv sync --locked` 和 `pnpm -C frontend install --frozen-lockfile`，防止安装时意外更新锁文件。Python 环境默认创建在 `.venv`，不使用全局环境安装项目依赖。

## 配置

可选地在根目录创建本地配置：

```bash
cp .env.example .env
```

当前唯一配置为 `ENV`，允许 `development`、`test`、`production`；缺省为 `development`，非法或空值在 API 启动时引发带字段名的 Pydantic 校验错误。当前没有必须提供的凭据或连接地址。

`get_settings()` 统一读取配置并缓存。系统环境变量优先于当前工作目录的 `.env`，因此启动命令应从仓库根目录运行。`.env.example` 只作说明，不会被自动读取。修改配置后重启 API；测试可调用 `get_settings.cache_clear()`，或通过 `create_app(settings=Settings(env="test", _env_file=None))` 注入配置。`.env` 中未知键被忽略，避免将共享配置文件强制扩大成当前 Unit 的配置合同。

模块导入和 App Factory 只装配应用，Settings 在 lifespan 启动阶段加载；不输出配置内容。

## 启动

终端一，在仓库根目录启动 API：

```bash
uv run uvicorn agenthub.main:app --reload
```

API 默认监听 `http://127.0.0.1:8000`。进程健康检查：

```bash
curl -i http://127.0.0.1:8000/health
```

应返回 HTTP 200 和 `{"status":"ok"}`。此检查只说明进程可用，不代表数据库、队列或模型服务可用。

终端二，在仓库根目录启动前端：

```bash
pnpm -C frontend dev
```

打开终端显示的 URL，默认 `http://localhost:5173`。页面仅显示项目名称和 Engineering Foundation，前端独立启动，不调用后端。两个开发进程均用 Ctrl+C 停止。

生成前端 production build：

```bash
pnpm -C frontend build
pnpm -C frontend preview
```

构建输出位于忽略的 `frontend/dist/`；preview 只用于本地检查，不是生产发布。

## 验证

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy backend/src
uv run pytest backend/tests
pnpm -C frontend typecheck
pnpm -C frontend build
```

测试覆盖 health 的 HTTP 合同、配置合法/非法值、`.env` 与环境变量优先级、缓存清理及显式注入。启动错误在进入 lifespan 时验证，测试通过 TestClient 上下文执行真实 ASGI 应用。

## 当前取舍与边界

- `backend/src/agenthub` 通过 `uv_build` 的 `module-root` / `module-name` 正式安装，distribution 为 `enterprise-agenthub`、import 为 `agenthub`。无需 `PYTHONPATH`，API 与未来 Worker 可以共用同一个包，测试也验证安装后的导入路径。
- `main.py` 只装配应用与 lifespan，`api/health.py` 定义路由和响应 Schema，`core/config.py` 统一配置；轻量 App Factory 支持测试注入，后续 Router 与生命周期扩展有明确位置。
- Unit 1.1 无持久业务、后台任务或 Agent 图需求，数据库、Redis、Celery、LangGraph 由对应 Unit 引入。
- Tailwind CSS 延后至 Unit 1.8 的实际工作台建设；当前只用少量原生 CSS。TanStack Query、API 契约生成和所有业务页面也留在后续 Unit。
- 本次验证为本地工程验证，不涉及 Mock/Live 模型或真实外部连接器，不代表生产部署或 Agent 质量验收。

依赖兼容核对参考：[uv build backend](https://docs.astral.sh/uv/concepts/build-backend/)、[Vite Node.js 要求](https://vite.dev/guide/)、[官方 React TypeScript 模板](https://github.com/vitejs/vite/blob/main/packages/create-vite/template-react-ts/package.json)、[pnpm Node.js 兼容表](https://pnpm.io/installation)、[Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)。最终安装结果以锁文件和实际验证记录为准。
