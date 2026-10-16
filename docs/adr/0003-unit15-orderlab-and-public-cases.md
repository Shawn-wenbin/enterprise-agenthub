# ADR 0003 — OrderLab 固定素材与公开评测输入

日期：2026-10-16；状态：accepted；范围：Unit 1.5。

## 决策

OrderLab 保存在 fixtures/orderlab，不安装成平台业务包、不注册平台 Router，不需要新数据库或迁移。
source/orderlab.py 的 create_app 每次建立空内存状态。公开配方使用根 uv.lock 中的 httpx2
ASGITransport 调用真实 FastAPI 应用，在第一次写入完成后丢弃响应并抛 ReadTimeout，
然后用相同业务输入重试。这是响应丢失的确定性本地模型，不模拟数据库并发、TCP 延迟或生产故障概率。
只有固定受信任快照的显式测试与工具才能加载它；素材扫描从不执行 Python 或文本指令。

公开测试保持正确业务断言，故障快照独立运行时应失败。平台 testpaths 仍是 backend/tests。
verify_unit15.py 对每次 subprocess 保存 argv、退出码、stdout/stderr、JUnit 和实际 GET 状态；
只接受精确目标测试、AssertionError 消息、目标业务断言、两笔不同订单以及超时/重试事件。
非零退出、环境错误、跳过、其他断言或缺少业务观测均拒绝。三次实验比较全部结构化观测。
这不是隐藏 grader，也不证明修复正确；独立 Runner/隐藏测试属于 Phase 7。

Fixture Manifest 管理业务世界：逐文件字节 SHA-256，源文件聚合哈希，环境与公开复现入口。
聚合只覆盖 source/，按相对路径排序后连接 path + NUL + file_sha256 + LF；
规范/OpenAPI/请求/日志/历史工单/公开测试/指南另有逐文件哈希。Manifest 本身不参与自身哈希。
根 pyproject.toml、uv.lock、.python-version 独立锁定，核验实际环境和锁中六个直接实验依赖。
不调用 ArtifactStore：它管理授权业务产物和随机存储 key，不承担固定仓库快照清单职责；
沿用 SHA-256 字节语义，避免引入数据库、账号和额外适配器。

验证默认只读；未声明文件、非法路径和符号链接一律拒绝。只忽略已知 Python/pytest/Ruff 缓存目录。
逐段路径检查针对静止、可信本地工作副本，不承诺抵御同 UID 并发文件替换。
orderlab_assets.py 默认显式执行固定配方作字节比较；--initialize 仅首次冻结，已有清单即拒绝。
不提供旧版本自动刷新。内容实质改变须评审差异、增加版本并显式登记支持；锁漂移同样触发失败。

Dataset Manifest 管理任务：case_id、dataset_version、problem_group、fixture_version、input_ref、
input_sha256。它是评测侧元数据；运行时只能获得严格 PublicCase 投影和授权材料，不能获得整个清单。
公开任务不接受未知字段，包含四个调查角度；retry/timeout 属于同一实际事件分组，不能充当独立问题族。
uncertain 仅包含客户端片段，不授予主事件日志或源码。schema 探针确实执行接口，未创建独立故障版本。
grader_status=not_implemented、无 grader_ref、policy_assertions=[]，不伪造评分器或评分成功。
后续可用同一 Schema 扩展 20 个审阅 Case，当前 v1 验证限定 3–5 个初始输入。

PublicCase.allowed_sources 仅收窄素材集合，不能授予 ACL 或构造可信 Principal。
不硬编码 Demo Tenant：现有 seed 不在本机开发库自动执行，文件 fixture 也未入库成资源。
后续真实素材入库与读取需重新使用可信 Session/Principal、Tenant/Project/Resource ACL。
参考原因、expected evidence、reference patch、隐藏测试和私有政策断言未创建；
目录分离/敏感字段扫描只是合同回归，不能替代独立身份、挂载、环境和人工泄漏审阅。

## 依据

ASGI 测试方式核对本地锁定 API 及
[FastAPI 异步测试](https://fastapi.tiangolo.com/advanced/async-tests/)、
[HTTPX 自定义 Transport](https://www.python-httpx.org/advanced/transports/)。
本仓库实际使用 httpx2 2.13.1，构造与异常签名以安装版本和真实验证结果为准。
