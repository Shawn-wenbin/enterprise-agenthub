# IMPLEMENTATION_STATUS

最后更新：2026-10-16
当前 Phase：1
当前 Unit：1.5 — OrderLab 素材与初始评测格式
Unit 状态：**verified**（固定故障、素材、输入合同及边界验证通过；不是 Agent/Grader 质量验收）
当前模式：synthetic 业务世界 + 真实本地 FastAPI/HTTPX 执行；平台回归使用真实 PostgreSQL/Redis/HTTP。

## Unit 1.5 范围与基线

开工 main 分支、工作区干净，基线 b68a8a7；核对 AGENTS、PROJECT_SPEC 第 3/4/16–20 节、
IMPLEMENTATION_PLAN 的 Unit 1.5、Unit 1.1–1.4 状态及 accepted ADR 0001/0002。
原仓库没有 fixtures/datasets、数据版本或 Manifest 公共工具；ArtifactStore 的字节 hash 为 SHA-256，
但它管理数据库业务产物，不适合作固定仓库素材清单。现有授权是服务端 Principal + Project ACL，
本次未把 Case 的 allowed_sources 变成授权，也没有硬编码 Demo Principal 或自动执行 Seed。
README 仍写 Unit 1.3，已按当前实现修正。无新增依赖，pyproject.toml、uv.lock、前端和迁移未改。

## OrderLab 最小闭环 — verified

- `fixtures/orderlab/source/orderlab.py`：synthetic 服务端 0.3.0 / orderlab_v1；独立 create_app，
  POST /orders、GET /orders?operation_id，严格请求 Schema、订单内存状态和结构化事件。
  与平台 User/Session/Tenant/Project、数据库及正常 Router 解耦；不是第二套产品后端。
- `source/scenario.py`：固定 checkout_001 / 商品 / 参数，从空状态执行真实 ASGI 请求；第一次写入后
  在 Transport 边界丢弃响应，触发客户端 ReadTimeout；第二次保留业务标识和参数，使用新请求 ID。
  无随机数、sleep、第三方或数据库竞态伪装；应用实例内状态不保证跨重启持久化。
- 正确预期：同一业务操作仅有一笔订单；实际 GET 返回 ord_0001、ord_0002 两笔独立订单。
  `tests/test_public_reproduction.py::test_timeout_retry_creates_one_order` 实际因
  `assert len(orders) == 1` / `ORDERLAB_SINGLE_OPERATION` / `assert 2 == 1` 失败，pytest 退出 1。
  三个独立进程观测完全一致，全部分类为 expected_reproduction_failure。
- `tests/test_controls.py`：2 个正向测试实际通过，覆盖合法创建、不同操作不合并、非法输入 422 且无写入。
  根 pytest testpaths 仍只包含 backend/tests，不用 skip/xfail 掩盖故障。

## 固定素材、版本与清单 — verified

Fixture Manifest 为 `fixtures/orderlab/manifest.json`，fixture_version=orderlab_v1，synthetic=true。
14 个固定文件覆盖 2 个源码/配方、3 个公开测试/配置、设计规范、实际生成的 OpenAPI、变更记录、
2 条历史工单、4 条主客户端事件、4 条服务端事件、2 条实际 Schema 探针、1 条仅客户端不完整观测和指南。
探针比较 201 / 422；不完整观测没有伪造关联服务端日志。所有时间固定为 UTC，版本发布/提交标签均为合成。

逐文件按原始字节计算 SHA-256；源码聚合只覆盖 source/，按相对路径稳定排序，拼接
`path + NUL + lowercase_hex_file_sha256 + LF` 后按 UTF-8 求 SHA-256。
源码 checksum：c9995b01e4399bb005bd2ea579ecda0862bbabfb19b86e1bb49a49e43a5488d5。
规范、OpenAPI、日志、公开测试等独立逐文件校验，Manifest 不参与自身哈希。
根 pyproject.toml / uv.lock / .python-version 独立校验；Python 3.12.13，
FastAPI 0.143.0、Pydantic 2.14.0、Starlette 1.7.0、httpx2 2.13.1、pytest 9.1.1、pytest-asyncio 1.4.0，
同时核对安装环境与根 lock。无绝对开发机路径写入素材清单。

`evaluation/validation.py` 默认只读，清单必须完整，不声明的新文件拒绝；只有已知缓存目录被忽略。
路径限制为受控相对 POSIX 路径，逐段拒绝上级跳转、URL、符号链接及父目录链接。
`scripts/orderlab_assets.py` 显式检查实际 OpenAPI/配方输出逐字节一致，执行前先验 Manifest；
--initialize 仅首次冻结，已有清单时拒绝，不提供自动旧版本 Hash 刷新。
实质变更需发布新 fixture_version / dataset_version，评审差异并登记新版本支持。
取舍及未来扩展见 accepted ADR 0003，PROJECT_SPEC 18.2 已同步当前合同。

## 初始 Eval 合同与边界 — verified

`evaluation/schemas.py` 采用严格 Pydantic、extra=forbid；`datasets/public/` 共 4 个 v1 输入：

- order_retry_001：retry_idempotency，具备完整公开故障复现。
- order_schema_001：schema_compatibility，有实际接口探针；仅初始任务合同，没有独立完整故障版本。
- order_timeout_001：timeout_outcome，复用主事件时间线；与 retry 同组 orderlab_checkout_v1，
  不能把两种提问当作独立故障家族或拆到 dev/holdout 制造独立样本。
- order_uncertain_001：insufficient_evidence，仅获客户端片段、规范与历史工单；没有本次服务端状态。

`datasets/manifests/orderlab_v1.json` 关联 Case ID、dataset_version、task_family、problem_group、
input_ref、input_sha256、fixture_version。它是评测侧元数据，禁止原样作为 Agent 输入/Tool Result。
grader_status=not_implemented、未填写 grader_ref、policy_assertions=[]；没有虚构答案、质量分数或成功率。
同一个固定业务世界可供不同调查角度复用，不要求每个 Case 都有独立故障代码。

公开输入不含参考原因、expected evidence、reference patch、隐藏测试和评分规则；未创建这些私有内容。
Schema、敏感标记、固定清单和输入 checksum 检查未知字段、越界引用与未发布变更；
它们不能完整判断自然语言答案泄漏，新增素材仍需人工审阅。
allowed_sources 仅是受控集合，未来读取还须由可信 Session/Principal 和资源 ACL 授权。
扫描只读字节，即使临时副本的已声明 Python 包含写文件代码且 hash 合法，也不会执行。
目录和路径检查不代表独立 grader 的身份/挂载/环境隔离，不承诺抵御同 UID 并发替换文件。

## 实际验证 — verified

- `uv run ruff check .`：All checks passed。
- `uv run ruff format --check .`：85 files already formatted。
- `uv run mypy backend/src`：strict 成功，46 源码文件。
- `uv run pytest backend/tests`：133 passed，56 deselected；没有收集公开故障测试。
- `uv run pytest backend/tests/test_orderlab.py -q`：早期 54 passed；最终统一验证的同文件为 56 passed，
  新增已声明 Python 不执行及 Fixture Manifest 敏感标记反例。
  覆盖临时副本源码/日志/规范/OpenAPI 篡改、依赖漂移、非法路径/链接、清单遗漏、公开测试引用、
  时间线错误、Case 重复/版本/Hash/答案字段/权限字段/引用、环境异常与非目标断言分类拒绝。
- `uv run python scripts/verify_unit15.py --static-only`：退出 0，Manifest/素材/四个 Case/边界一致，未执行源码。
- `uv run python scripts/orderlab_assets.py`：退出 0，实际生成的 OpenAPI 及 4 份 JSONL 与固定字节一致。
- `uv run pytest fixtures/orderlab/tests/test_controls.py -q`：退出 0，2 passed。
- `uv run python scripts/verify_unit15.py`：退出 0；56 个边界测试、2 个正向对照通过；
  三次实际 `python -m pytest fixtures/orderlab/tests/test_public_reproduction.py::test_timeout_retry_creates_one_order`
  均退出 1，均只有目标业务断言失败；保存真实命令、stdout/stderr、JUnit 和 GET 订单观测。
  **该结果表示故障基线确认，不表示公开业务要求已正常通过或故障已修复。**
- `uv run python scripts/verify_unit14.py`：最终 **189 passed**，其中 133 本地、56 真实集成；
  真实 PostgreSQL/Redis/HTTP、迁移/Seed/撤权回归通过。
  新旧隔离库从既有迁移链升级、重复升级与 Alembic check；历史三种产物/User/Session 保留。
  开发库只读前后审计一致，未改原有用户、项目或产物；Redis DB15 使用随机 test namespace，无 flush。
- `pnpm -C frontend typecheck`、`pnpm -C frontend build`：退出 0，Vite 8.3.4；未改前端代码。
- `git diff --check`：通过；.data、.env、缓存和构建产物仍被忽略。没有 commit/push/部署/外部写入。

Unit 1.5 最终证据：`.data/unit15-verification/72b4f6ae218b4fe48f2f601d6bce4e9f/result.json`，
同目录 boundary-tests/controls/reproduction-1/2/3 的 stdout、stderr、XML，全部 Git ignored。
Unit 1.1–1.4 最终回归证据：`.data/unit14-verification/7b85249385924bc1983eb1b7c9e747bd/result.json`、api.log；
fresh DB 为 agenthub_unit14_test_db059e01e2c44351aef7b8958a13ca35，
legacy DB 为 agenthub_unit14_test_94227bcfdd664a928adbcc1e3115270b，均保留不清理。
首轮分类器误以为 pytest JUnit 提供 type 属性，实际异常类型在 message；按真实 XML 修正，
保留 `.data/unit15-verification/9e455436404c465583d2c73609ca6f47/` 的失败记录，不计为复现成功。
历史验证入口首轮全部 187 个测试通过，但后续缺配置 CLI 检查受本地 .env 嵌套字典合并影响；
仅修正 verify_unit14 的 Seed 子进程 cwd，使用锁定环境 Python，不修改产品配置或降低断言。
修正后的完整验证已通过，原隔离库保留，无清库。

## 未实现、unverified 与下一 Unit

- **implemented / verified**：单一 orderlab_v1 主故障、固定多源合成素材、两个 Manifest 与四个 PublicCase 合同。
- **unverified / planned**：20 个手审 Case、其他独立故障版本、跨版本规范、同名受限素材、恶意指令材料、
  Retrieval Grader、Judge、Live Model/Agent 质量、Phase 7 独立隐藏 grader/Runner 和真实隔离。
  当前不执行任何模型调查、工单创建或补丁验证。
- **blocked**：无 Unit 1.5 必要退出条件阻塞；Phase 1 整体仍 unverified。
- Unit 1.6 状态为 **planned**，未开始。进入时可复用 PublicCase、固定 query、input hash 和 fixture_version，
  由服务端认证构造 Principal 并确定真实 Tenant/Project 资源范围，不能信任 Case 中的身份字段或 allowed_sources。
  Mock Run 应显著标记 Mock，仅读公开任务与授权素材，不读取 Dataset Manifest 私有元数据或未来隐藏答案，
  不把 MockModel 文本当作已完成真实调查。Run API/Celery/状态机的实现须下一次授权。

---

## Unit 1.4 验收历史（2026-10-14，原记录保留）

最后更新：2026-10-14
当前 Phase：1
当前 Unit：1.4 — 租户、项目、角色与资源授权
Unit 状态：**verified**（当前 Unit 退出条件已通过；完整历史归属收紧与后续资源权限见限制）
当前模式：本地确定性工程测试 + 真实 PostgreSQL/Redis/HTTP，无模型或外部连接器。

## Unit 1.4 范围与基线

开工 Git 工作区干净，基线 bfd391d；读取 AGENTS、PROJECT_SPEC 第 4/F01/12/13/15 节、
Unit 1.4 计划、既有状态和 accepted ADR 0001。复用 Unit 1.3 UserIdentity、AuthService、
Session Cookie、CSRF/Origin、错误结构和短事务，不重写认证。当前迁移 head 为 0004_tenant_authorization。
仅实现本 Unit，未开始 1.5，无 commit/push/部署，无清库或 Docker Volume 删除。
无新增依赖，uv.lock、前端依赖和已应用 0001–0003 迁移未修改。

## Unit 1.4 实现与数据模型

- tenants/models.py：Tenant 的全局唯一 slug、启用标志与 UTC 时间；Membership 关联现有 User，
  `(tenant_id, user_id)` 唯一，停用保留历史；membership_roles 以 `(membership_id, role)` 为主键。
- projects/models.py：Project 的 `(tenant_id, slug)` 唯一，允许跨租户同名同 slug；启用标志与 UTC 时间。
  resource_acl 显式保存 tenant/project/membership/scope，以 `(membership_id, project_id, scope)` 唯一。
- ID 沿用 Unit 1.3 的 UUID 字符串编码。slug 去首尾空白并转 ASCII 小写，1–63 字符，
  允许 `[a-z0-9-]` 且首字符为字母/数字；数据库 CHECK 拒绝未规范化值。
- Tenant/User 的 FK 与 projects/memberships 的复合唯一键保证引用完整性；ACL 的两组复合 FK
  `(tenant_id, project_id)`、`(tenant_id, membership_id)` 阻止跨租户关联。角色和 scope 有固定 CHECK。
- authorization/policy.py：固定 tenant_admin/developer/reviewer/viewer，能力集合不可变；
  scope 为 read/develop/review/manage。当前项目元数据读取接受明确 scope，管理只允许 Admin。
  Admin 无 ACL 例外明确限制项目元数据，不授予内容、外部写入或审批权限。

## Principal 与统一授权

authorization/service.py 的 frozen Principal 包含 user_id、tenant_id、membership_id、frozenset roles。
build_principal(UserIdentity, tenant_id) 从有效 User/Tenant/Membership 和成员角色构造，
客户端 tenant_id 只是目标选择；不合并不同租户的角色，也不把权限存入 Cookie。
require_tenant_access / require_project_access 每次重查有效性和当前角色，不采信旧 Principal 的角色快照。
无 ACL、跨租户、停用、不存在的项目统一 resource_not_found/404；可见对象上的能力不足返回
authorization_denied/403。身份无效为 401，必要依赖不可用沿用 dependency_unavailable/503。

list_projects 从可信 UserIdentity 对 SQL JOIN/EXISTS 授权集合查询，不加载全库后过滤。
单用户的多个有效 Membership 形成项目并集。无授权缓存，Cookie 不变也能观察已提交撤权。
当前授权是读取时快照，不保证取消正在执行的请求；后续敏感写入需在实际边界重新鉴权并解决并发竞争。
未来 Run/报告/证据/动作/审批 Action 只定义角色能力；当前项目入口拒绝缺少真实资源上下文的调用，
返回 authorization_context_required，不冒充已经实现 owner、自审、动作版本或原文权限检查。

## 真实 API 合同

- GET /api/v1/projects：可信 Session 认证；响应 `[{id, tenant_id, slug, name}]`。
  可选 tenant_id 仅收窄授权集合；无可见项目返回 `[]`。401/503 使用统一错误结构。
  未声明的 role/user/header 等客户端字段不会授予权限。Admin 仅获得本租户必要项目元数据。
- GET /api/v1/auth/me：保留 id/login_name 原语义，新增
  `memberships: [{tenant_id, tenant_slug, tenant_name, membership_id, roles}]`。
  只展示本用户有效 Tenant/Membership，角色来自当前数据库；无成员关系为 `[]`。
  登录响应仍为原最小身份；不暴露密码、token、CSRF secret 或其他用户权限。
- 两个响应均 no-store；Router 统一挂到既有 /api/v1 安全入口。没有 Project CRUD、成员管理或产物公开 API。
  测试中的项目 ID probe 仅存在于测试 fixture，也复用认证和 CSRF/Origin Guard，不注册到产品应用。

## 显式 Demo Seed

实际命令：`uv run python -m agenthub.cli seed-demo`。
配置 DEMO_SEED_PASSWORDS JSON 的四个 key：demo-a-developer、demo-a-reviewer、
demo-b-developer、demo-b-reviewer；值为本地生成的 12–256 字符密码。
.env.example 只有说明/空配置，无有效默认密码。CLI 不输出密码；使用已有 Argon2id 哈希实现。

seed 建立 demo-a/demo-b、两个同名同 slug demo-project、四个独立用户、4 Membership、4 Role、4 ACL。
开发者授 develop，审阅者授 review；Admin/Viewer/多角色由独立权限 fixture 验证，不增加多余演示账号。
只允许 development/test，production 拒绝、未知 ENV 配置失败、缺失/不完整密码失败。
不在启动或迁移时执行；本次默认开发库 demo tenant count 为 0，演示账号仅在隔离验证库创建。

固定唯一键和 UUID5 标识保证可重复；数据库 advisory transaction lock 序列化并发 seed。
密码计算期间释放连接，写入前重新核对状态，全部记录一个短事务提交。
第二次返回 unchanged，不重置密码；停用/删除 Membership、撤销 Role/ACL、已有自然键或数据冲突
返回 seed_conflict，不覆盖或静默重新授权。提交失败回滚全部创建，不报告成功。

## Artifact 归属审计与安全迁移

迁移前本地开发库 head=0003，Artifact count=0；原 tenant_id/project_id 为 String(128)、必填、无 FK。
不能把空开发库当成非空保留证据。隔离旧库在 0003 上建立 ready/pending/failed 三条 Artifact，
包括 verification/restart 和无法确认的历史标识，以及既有 User/Session；升级 0004、重复升级后
逐字段比较完全保留，原文件字节仍可通过原受控内部服务读取。

保留原字段类型/值，不删除、不重分配、不造默认租户。新增 nullable authorized_project_id，
复合 FK `(tenant_id, authorized_project_id)` 指向真实 Project，CHECK 要求非空值等于 project_id。
旧记录关联保持 NULL；当前内部保存只对数据库精确匹配的归属填入关联。NULL 代表未确认归属，
不是公开读取授权。Project ACL 不传入 ArtifactService，不放宽既有内部范围限制。
全部旧原字段的非空 FK 仍需独立授权的可信映射/数据确认，再回填与收紧；具体路径见
[ADR 0002](adr/0002-unit14-authorization-and-legacy-artifacts.md)。没有 Artifact 对外读取接口。
本地开发库升级后 count 仍 0，迁移没有 Seed。0001–0003 未重写，未 create_all/drop/recreate。

## 实际验证 — verified

- uv run ruff check .：All checks passed。
- uv run ruff format --check .：最终 68 files already formatted。
- uv run mypy backend/src：strict 成功，42 源码文件。
- uv run pytest backend/tests：77 passed、56 deselected；真实依赖用例不混入本地通过数。
- uv run python scripts/verify_unit14.py：**133 passed**，其中 77 本地、56 真实集成（含 20 新授权集成用例）。
  PostgreSQL 17.11、Redis 7.4.11；逻辑库 15 + 随机 test namespace，无 flush。
  隔离 fresh DB：agenthub_unit14_test_5b7dd45c2a7140c8b6666049512e8f2a；
  隔离 legacy DB：agenthub_unit14_test_9df7f63fc3e44f34b8e8ef636e5ee912。
  新库从零升级、旧库升级、重复 upgrade head、两库 alembic check 均通过；历史三种 Artifact/User/Session 保留。
- 同脚本 Seed CLI：首次 created、再次 unchanged；缺失配置、production、
  未知 ENV 拒绝且数据库快照不变。ASGI 集成验证配置变更不重置密码、并发 seed 只有一次创建、commit 失败全量回滚，
  Membership 停用/删除与 Role/ACL 删除后 seed 不恢复；旧 Artifact/Session 和用户密码保持不变。
- 同脚本真实 Uvicorn TCP HTTP：四个 Developer/Reviewer 通过原 Auth API 登录，Session Cookie 到
  /projects、/auth/me 生效；跨租户筛选/伪造 role 无扩大，原 Cookie 下 ACL/Role/Membership 撤权生效。
- 真实 ASGI HTTP + PostgreSQL/Redis：两租户同 slug 隔离、无 Cookie 401、跨租户 project_id/隐藏资源与
  不存在统一 404、伪造 tenant/role/user/header、无 Membership/无角色/无 ACL 默认拒绝，
  多 Tenant 项目并集和角色分离、多角色组合、Admin 元数据例外、Viewer 写能力拒绝，
  ACL/Membership/Tenant/Project/Role 撤权与旧 Principal 拒绝；数据库非法跨租户 ACL/重复/角色/scope 约束。
- 补充边界在同一 legacy 隔离库执行：uv run pytest backend/tests/test_authorization_integration.py
  --integration -k 'http_two_tenants or artifact_confirmed'，2 passed、18 deselected；
  实际 Cookie → Principal → ACL 项目准入成功；同时伪造 Artifact 原 project_id 和新关联以满足 CHECK，
  仍被跨租户复合 FK 拒绝。其余已通过用例未再次计为新增验证。
- uv run python scripts/verify_unit13.py：另一个隔离库 agenthub_unit13_test_d6b51d06b9944a169ec244d697da468e，
  完整 **133 passed**；Vite 真实代理登录/Cookie/身份、缺失 CSRF、API 重启后 Session 保留、logout 撤销，
  伪造 Forwarded/XFF 不绕过限流。原 Auth/CSRF/Origin/限流/健康/数据库/Artifact 回归均通过。
- LC_ALL=en_US.UTF-8 uv run alembic -c backend/alembic.ini upgrade head：默认开发库 0003 → 0004，
  重复执行成功；同 locale 的 alembic check：No new upgrade operations detected。前后 Artifact count=0。
- pnpm -C frontend typecheck、pnpm -C frontend build：成功，Vite 8.3.4；无业务前端修改。
- git diff --check：通过；Git 忽略验证文件/.env/构建产物，无 commit/push/部署。

Unit 1.4 最终证据：.data/unit14-verification/df4e7712f546466ab1cf24be2de05e3c/result.json、api.log。
Unit 1.3 回归证据：.data/unit13-verification/agenthub_unit13_test_d6b51d06b9944a169ec244d697da468e/result.json、
api-0.log/api-1.log/vite.log。全部 Git ignored，不写入原始密码或 Session/CSRF 凭据。
首轮隔离库 87009a8763734bb1aea1cc547969d917 / aca0b5ffc5bb4781b8ba39f67e054d1f 保留：
132 passed、1 failed（项目响应未 no-store），修正实现后重跑完整验收，不删除断言或清库。
Alembic 首次生成遇到已有中文 ini 的 ASCII locale 失败，使用显式 UTF-8 locale 重试；已应用旧迁移未修改。

## 限制、unverified 与 blocked

- **unverified / 后续范围**：真实 Run 资源/owner 授权、RAG/Evidence 原文 ACL、Agent/ToolGateway/MCP、
  审批动作版本/目标/发起人上下文、完整成员管理 UI、SSO/OIDC、生产 IAM 安全运维；当前 Action 常量不代表实现。
- **unverified**：生产 TLS/代理、多进程负载、真实浏览器、多宿主撤权竞争和性能。当前真实 HTTP/ASGI
  工程结果不推广为生产落地、模型任务质量或未来原文读取安全验收。
- 旧 Artifact 原字段完整 FK 收紧需要历史数据可信确认；当前 nullable 关联已有完整 FK/CHECK，
  无公开读取和自动回填。内部范围只用于可信服务端代码，不能从未来 HTTP/模型字段直接构造。
- **blocked**：无本 Unit 必要退出条件阻塞。没有 Mock 模型或连接器验证，不用 Mock 结果替代真实依赖。

## Unit 1.5 进入条件

Unit 1.1–1.4 已 verified，Phase 1 总体仍 unverified。本次止于 1.4。
1.5 需用户单独授权，建立 OrderLab 的 synthetic 故障快照、公开复现、manifest/version/hash 和
3–5 个初始 case 格式，并保持 Agent-visible 与 Grader-only 分离；不得将隐藏答案放进运行时可读目录。
后续业务复用当前 UserIdentity → 租户 Principal → AuthorizationService，具体资源与动作需补自己的上下文校验。

---

## Unit 1.3 验收历史（2026-10-13，原记录保留）

最后更新：2026-10-13
当前 Phase：1
当前 Unit：1.3 — Session 登录与安全请求
Unit 状态：**verified**（本 Unit 必要退出条件已通过；生产/浏览器未验证项见下）
当前模式：macOS 本地确定性测试 + 真实 PostgreSQL/Redis/HTTP；无 Mock/Live 模型或外部连接器。

## 范围与基线

本次仅实现 Unit 1.3，没有 tenant/membership/project/role/ACL/Principal、正式 demo seed、完整 UI、
SSO/OIDC、Run/Worker/Agent/RAG/MCP。未 commit/push/部署，未删除测试库、Volume 或用户数据。
开工 Git 工作区干净，基线 6df70fc；读取 AGENTS、规格第 4/13/15 节、Unit 1.3 计划、STATUS 和 ADR 0001。
实际复查 App Factory、AsyncSession、迁移、Settings、Compose、统一错误/request_id 和 Vite；
Unit 1.1–1.2 原本 43 个本地测试重新通过，已有 PostgreSQL/Redis 容器 healthy。
本次完整测试也包含原有 19 个真实数据库/产物回归，没有仅凭历史 STATUS 判定通过。
规格无整体架构冲突。仅同步 auth/csrf 合同、UserIdentity 阶段边界和新增持久化合同，AGENTS/旧迁移未改。

## 实际实现与关键文件

- auth/models.py、0003_users_sessions.py、db/metadata.py：最小 User/Session 模型及追加迁移。
  User：UUID id、唯一 login_name、password_hash、is_active、UTC created_at/updated_at。
  Session：UUID id、users 外键及 user_id 索引、唯一 token_hash、UTC created_at/expires_at/revoked_at。
  数据库 CHECK 限制规范化账号和 expires_at > created_at；不建立未来业务表。
- auth/schemas.py：账号 strip/lower、1–128 个 ASCII 字符（字母/数字开头，其余允许 _.@+-），
  输入禁止额外身份字段；密码 SecretStr 1–256 字符，身份响应只有 id/login_name。
- auth/security.py：pwdlib/Argon2id、线程并发限制、dummy 校验、256 bit 随机 Session Token/SHA-256，
  ItsDangerous SHA-256 签名与限时 CSRF；不自行实现密码或签名算法。
- auth/service.py：短查询、事务提交后返回 Session；密码计算期间无数据库连接；创建前重查并行锁用户，
  每次认证查 PostgreSQL 有效期/撤销/用户状态；注销只撤销当前 Session，连接/超时错误失败关闭。
- auth/dependencies.py、api/router.py：可覆盖 get_current_user/get_current_session 依赖；
  后续业务 Router 挂到受保护 /api/v1 入口，所有非安全方法要求精确 Origin + CSRF。
- api/auth.py：POST /api/v1/auth/login、POST /api/v1/auth/logout（成功 204）、
  GET /api/v1/auth/me、GET /api/v1/auth/csrf；成功与失败响应均 no-store。
- auth/body_limit.py：解析 JSON 前将登录请求体限制为 4096 bytes，包含 chunked 流。
- auth/rate_limit.py：Redis Lua 原子来源/账号固定窗口；main.py 复用已有 DB/Redis/store 生命周期。
- core/config.py、.env.example：环境 Cookie/CSRF/Origin/限流配置及生产启动硬校验；
  注入 Settings 也重新验证，不因 model_copy 绕过生产限制。
- frontend/vite.config.ts：/api 前缀原样代理，默认 loopback:8000，保留 Cookie/Origin；
  服务端 AGENTHUB_API_PROXY_TARGET 可支持临时端口，未创建正式登录 UI/重复 DTO。
- test_auth_security.py、test_auth_integration.py：新增 24 个本地安全测试和 17 个真实集成测试。
  既有迁移回归断言更新为明确四表和新 revision；没有删除断言或放宽安全检查。
- scripts/verify_unit13.py：新库迁移、旧 Artifact 保留、完整测试、真实代理/重启及伪造转发头验证。
  verify_unit12.py 仅同步实际迁移头报告；README 说明本地安全调用和隔离用户 fixture。

## 调用链与安全策略

登录：共享 Redis 限流 → 短查询用户并关闭 Session → 有限线程 Argon2id 验证（无用户也验证 dummy）→
短事务锁定/重查账号 → 生成并持久化 Session hash/TTL → 提交成功 → HttpOnly Cookie。
用户不存在、密码错和禁用均为通用 401；事务失败不返回有效登录/认证 Cookie。

身份：Cookie → 格式检查 → SHA-256 → Session 与 User 联合查询 → 检查固定 TTL、revoked_at、is_active →
不可变 UserIdentity。不存在、伪造、撤销、过期、禁用均 401；依赖故障 503，不信任 body/query/header user_id。

注销：Origin + 当前有效 Session 绑定 CSRF → 短事务条件撤销当前行 → 提交 → 删除同名同属性 Cookie。
重复/失效注销为 401；数据库撤销失败 503，不清 Cookie、不声称已失效。其他 Session 独立。

Cookie：dev 默认 agenthub_session、HttpOnly、SameSite=lax、Secure=false、Path=/、host-only；
无 Secure 时只允许 loopback/localhost Origin。prod 默认 __Host-agenthub_session、Secure=true、
HttpOnly=true、SameSite=lax、Path=/、无 Domain；None 必须 Secure。生产要求显式 HTTPS Origin、
32-byte 随机 CSRF 密钥和非默认数据库/Redis 凭据；本地无认证 Redis 配置不能用于生产。

CSRF：登录前发放 10 分钟签名随机 nonce，通过 HttpOnly Cookie + JSON body 给同源客户端，
修改请求必须将相同签名值放入 X-CSRF-Token 并通过签名/时效/Origin/JSON 格式校验。
登录成功清除预登录 Cookie，再获取绑定 Session.id 的签名凭证；服务端先验证当前 Session，
旧 Session 的 CSRF 不能在另一会话或撤销/过期后使用。不是任意 Cookie 的裸值比较。
获取凭证要求精确可信 Origin，或无 Origin 的浏览器 Sec-Fetch-Site=same-origin + X-CSRF-Fetch=1；
非法/第三方来源拒绝，无宽松 CORS。修改请求缺失 Origin 直接拒绝，不回退信任 Host/Referer/Forwarded。
所有 API 修改请求复用同一 guard；安全 GET 不改变业务记录，csrf GET 仅发放防伪 Cookie。

CSRF_SECRET 所有 API 进程共用；轮换使旧 CSRF 失效，但保留服务端 Session，可重新获取凭证。
dev/test 缺省随机临时密钥跨重启/进程不稳定，生产不允许缺省；需要多进程开发时应显式配置。
预登录凭证没有单独数据库账本，签名有效期间可重放，登录完成会清 Cookie，后续需 Session 绑定凭证。

限流：默认首次尝试起 300 秒固定窗口，连接来源 60 次、规范化账号 10 次；成功与失败都计数。
同一 Lua 脚本执行 INCR/首次 EXPIRE，拒绝不延长窗口，计数跨进程共享；Redis 故障登录 503。
来源与账号 Redis key 使用 hash；不记录原始密码/Session/CSRF。只使用 ASGI 连接来源，官方启动命令
使用 --no-proxy-headers；直接伪造 XFF/Forwarded 不改变来源。Vite 代理后的客户端共用 loopback
来源额度；当前未实现生产可信代理的细分客户端来源、Redis Cluster 或复杂风控。

Argon2id 参数：m=65536 KiB、t=3、p=4，库默认 16-byte 随机盐和 32-byte hash；
默认每 API 进程最多 2 个密码计算（约 128 MiB 同时工作内存），不阻塞 API 事件循环。
最终脚本单次真实 hash 为 36.05 ms（本机单样本，不是性能分位数或用户名时间侧信道证明）。
密码低熵，使用带盐且耗内存的 Argon2id；Session 随机 256 bit，可用快速 SHA-256 建索引。
Server-side Session 让有效期/撤销/禁用每次直接检查，不需要提前引入 JWT 撤销账本；
Cookie 属性不能替代请求防伪，当前 UserIdentity 只证明身份，后续 Principal 才表达授权。

## 实际验证 — verified

- uv sync：成功，uv.lock 新锁定 pwdlib 0.3.1、argon2-cffi 25.1.0、argon2-cffi-bindings 26.1.0、
  ItsDangerous 2.2.0、cffi 2.1.1、pycparser 3.11；既有框架/前端版本未升级。
- uv run ruff check .：All checks passed；uv run ruff format --check .：50 files already formatted。
- uv run mypy backend/src：Success，29 个源码文件，strict 未放宽。
- uv run pytest backend/tests：67 passed、36 deselected；真实依赖用例未混入本地通过数。
- uv run python scripts/verify_unit13.py：最终隔离库
  agenthub_unit13_test_5216ef9666b5419eba155eb813cf78e2，全部 **103 passed**（67 本地 + 36 集成）。
  真实 PostgreSQL 17.11 / Redis 7.4.11；Redis 使用逻辑库 15 与随机 test namespace，无 flush。
- 同脚本：新库从零到 0002 保存 Artifact 后升级 0003；重复 upgrade head 成功，
  alembic check 为 No new upgrade operations detected；原 Artifact 元数据及字节均保留。
  明确表集合 artifacts/users/sessions/alembic_version，User 唯一/规范化、Session hash 唯一和外键通过。
- 同脚本真实 Uvicorn/Vite HTTP：代理保留 /api，登录保存 Cookie，/me 成功；缺失 CSRF 拒绝；
  Uvicorn 进程重启后原 Session 仍有效，logout 后旧 Token 401；health/readiness 全部 200。
  --no-proxy-headers 下伪造 Forwarded/XFF 不绕过来源限流，超过 3 次测试额度为 429。
- 真实集成覆盖：错误密码/不存在/禁用通用 401、过期/撤销/禁用/伪造 Cookie、独立并发登录、
  跨 Session CSRF 拒绝、Origin/CSRF 任一失败、预登录防伪、账号规范化限流、窗口恢复、
  30 并发尝试经两个独立 Redis 客户端严格只放行 5 次、真实不可达 Redis 503。
  注入 DB 创建/撤销 commit 失败无成功 Cookie/撤销报告；真实不可达 DB 身份请求 503。
  密码计算期间禁用后不能建 Session；HTTPS ASGI 客户端验证 __Host-/Secure Cookie 及正确删除。
- 默认开发库实际执行 uv run alembic -c backend/alembic.ini upgrade head（UTF-8 locale），
  0002 → 0003，重复执行成功；alembic check 无差异。迁移前后 Artifact count 均 0、摘要相同；
  非空数据保留证据来自隔离库 sentinel，不将空库比较冒充非空验证。
- pnpm -C frontend typecheck、pnpm -C frontend build：成功，Vite 8.3.4。
- git diff --check：通过；暂存区为空，AGENTS 与 0001/0002 无改动；.env/infra/.env/.data/
  虚拟环境/node_modules/dist 的忽略规则核对通过。未改本地 .env 或 Compose 凭据。

最终证据：.data/unit13-verification/agenthub_unit13_test_5216ef9666b5419eba155eb813cf78e2/result.json，
api-0.log/api-1.log/vite.log；均 Git ignored，不包含原始凭证。
此前创建的隔离库 e02b29512813497194bfdf131ec01caa（locale 失败，未迁移）和
8f96673341614283a9a1aa771f8dcd9d（首轮 94 passed）、
 dd9c1cf3bf7b49ad8920ae2d4ba4457c（补充边界后 102 passed）也保留，不自动删除。

## 首轮失败与处理

- uv cache、Docker socket、仓库非 artifacts 路径和前端构建写入被沙箱拒绝，按权限流程获准后执行；
  没有绕过沙箱或改用个人全局环境。
- 配置示例复核发现 Literal[True] 拒绝环境变量字符串 true；改为标准 bool 解析并硬性拒绝 false，
  新增环境变量回归后最终完整 103 passed，未降低 HttpOnly 要求。
- 初轮格式/类型检查失败，修正类型收窄、格式和依赖注入测试；最终检查通过。
- 默认 8000/5173 端口已占用，验证脚本改用空闲端口和受控 Vite target，不终止用户已有进程。
- ASCII locale 导致 Alembic 读取已有中文 ini 注释失败；脚本显式 LC_ALL=en_US.UTF-8 后重跑成功，
  不修改已应用迁移、不删除失败库、不将失败记录为通过。

## 限制、unverified 与 blocked

- **unverified**：真实浏览器 Cookie/Fetch Metadata 行为、生产 HTTPS/TLS/反向代理、多宿主多 worker 部署、
  负载性能及密码校验时间侧信道评估；HTTP 代理和 HTTPS ASGI 属性测试不替代这些验收。
- **unverified / 后续范围**：Tenant/Project/Membership/Role/Resource ACL/Principal、正式 dev/test seed、
  完整登录 UI、SSO/OIDC/MFA、密码找回、用户管理、完整 IAM/生产安全运维。
- 每请求认证采用已提交状态快照；注销/禁用影响后续检查，不取消已经开始的业务请求或撤回外部效果。
  Session 创建提交响应丢失可能保留不可达 Session；无成功 Cookie，不盲重试，不承诺 exactly-once。
- 本地 Redis 无认证仅适合 loopback 开发；生产须另配置认证和部署边界，当前 Compose 不是生产方案。
- 没有滑动续期、会话管理/批量撤销 UI、过期行自动清理或分布式 Session cache。
- **blocked**：无 Unit 1.3 必要验收阻塞。以上未验证项保持明确，不推广为生产落地或模型质量。

## Unit 1.4 进入条件

Unit 1.1–1.3 verified，Phase 1 总体仍 unverified；本次止于 1.3，不自动进入 1.4。
下一 Unit 需单独授权，在当前可信 User/Session 上新增 Tenant、Membership、Project、Role、
Resource ACL 与 Principal；从服务端 membership/project scope 构建授权上下文，tenant_id/role
不能直接相信客户端字段。使用新增迁移补归属外键前审计已有 Artifact 内部标识，保留现有数据。
dev/test 幂等 seed 只在受控环境创建两个租户及独立演示账号，密码来自本地配置，无生产默认账户。
后续 Router 复用受保护 /api/v1、get_current_user、短事务和统一错误，写入前还需重新检查授权。

---

## Unit 1.2 验收历史（2026-10-10，原记录保留）


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
