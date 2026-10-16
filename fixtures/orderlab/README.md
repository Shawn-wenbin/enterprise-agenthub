# OrderLab v1（synthetic）

这是 AgentHub 被调查的固定业务世界。所有源码、请求、日志、商品、工单和发布说明均为合成材料。
运行环境：仓库根 Python 3.12 / uv.lock；无需数据库、Redis、真实移动端、模型、支付或账号。
只提供服务端 0.3.0 的小型 FastAPI 内存快照，不是完整订单产品或安全沙箱。

## 显式公开实验

从 AgentHub 仓库根目录运行：

```bash
uv sync --locked
uv run pytest fixtures/orderlab/tests/test_controls.py
uv run pytest fixtures/orderlab/tests/test_public_reproduction.py -q
uv run python scripts/verify_unit15.py
```

公开复现从空状态开始，以固定商品和 operation_id 创建订单；第一次应用响应在本地传输边界丢弃，
客户端收到 ReadTimeout 后保留业务输入并重试。测试从实际 GET 接口读取订单状态，
断言同一结算只能有一笔订单。故障版本预计由此业务断言失败，pytest 退出码为 1。
ImportError、环境错误或其他异常不构成复现成功。测试不包含无条件失败、skip 或 xfail。
verify_unit15.py 分别执行正向对照和三次公开失败实验，保存命令、退出码、JUnit、实际状态和分类。
默认 `uv run pytest backend/tests` 不会收集这里的故意失败实验。

日志生成调用同一实际应用和固定配方；逻辑时钟可重复，非真实移动网络延迟。
schema_probes.jsonl 是实际接口探针；uncertain_requests.jsonl 则是明确只有客户端观测的合成片段，
没有对应服务端状态，不得据此认定创建成功或失败。

## 素材和版本

source/：固定业务实现和显式实验配方。specs/：正确业务合同与实际应用生成的 OpenAPI。
requests/、logs/：一行一条、UTC、具有同名关联字段的合成记录。
changes/：合成版本背景；tickets/：提供比较价值的不同历史事件；tests/：公开要求与正向对照。

manifest.json 记录每个受控文件的 SHA-256；source_checksum 覆盖 source/ 下全部文件。
聚合规则：按相对 POSIX 路径排序，连接 `path + NUL + lowercase_hex_file_sha256 + LF` 后按 UTF-8 哈希。
manifest 自身不参与自身哈希。pyproject.toml、uv.lock、.python-version 单独核验，
使用该锁中的实际依赖版本。除了明确忽略的 Python/pytest/Ruff 缓存，新增未声明文件会拒绝。

只读素材校验：

```bash
uv run python scripts/verify_unit15.py --static-only
uv run python scripts/orderlab_assets.py
```

前者只读取和验证，不导入源码。后者显式执行固定受信任配方，比较 OpenAPI、日志与请求字节，
不写入文件。`orderlab_assets.py --initialize` 仅用于首次冻结，已有 Manifest 时拒绝，不能刷新旧哈希。
实质变更需评审版本差异、发布新的 fixture_version / dataset_version 和相应清单；不能更改内容后
沿用旧版本覆盖哈希。当前初始合同只登记 orderlab_v1，后续增加版本时须显式登记验证支持。

## 当前边界

源码读取应限定在本快照内；素材路径须相对、无上级跳转且无符号链接，验证不执行文本中的命令。
目录结构和静态标记检查是数据合同，不是面对恶意同 UID 进程的运行隔离或所有秘密的检测器。
平台 API 不注册这里的 Router，也不会在启动时导入或执行本目录。
后续仍需多个故障版本、跨版本规范、同名受限素材与受控恶意文档；本版不批量生成占位材料。
