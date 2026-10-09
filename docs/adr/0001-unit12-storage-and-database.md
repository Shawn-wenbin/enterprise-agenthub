# ADR 0001 — Unit 1.2 数据库与本地产物一致性

日期：2026-10-10；状态：accepted；范围：Unit 1.2。

## 决策

继续使用单 Python 工程、模块化单体。SQLAlchemy 2.0.54 / asyncpg 0.32.0
提供 AsyncEngine 和 async_sessionmaker；Alembic 1.20.0 通过 run_sync 适配异步连接。
Redis 7.4.11 是开发缓存/队列依赖，不是业务数据的唯一事实来源。

每个 API 进程在 lifespan 中创建一个 Engine 和 SessionFactory；连接按需建立。
关闭时释放 Redis 客户端、存储目录描述符并 dispose Engine。
请求依赖 get_session、内部任务 Database.session() 都创建独立 AsyncSession。
Session 关闭与事务提交分离，service 使用 session.begin() 明确提交或回滚，
不会隐式提交 repository 操作。close_resets_only=False 防止已关闭 Session 被误复用。
expire_on_commit=False 避免提交后访问已加载字段触发隐式异步 IO；对象只是快照，
需要重新查询/refresh 才能获得其他 Session 的更新。

ArtifactStore Protocol 只处理字节；LocalFileArtifactStore 实现本地存储，
ArtifactService 管理元数据、状态与资源范围。数据库只有 artifacts 业务表，
文件字节不写入业务行。tenant_id/project_id 必填，无提前建立的身份或租户表。
InternalArtifactScope 仅由可信服务端代码构造，不是认证，也不绑定匿名 HTTP/模型参数。
Unit 1.4 引入 Principal/ACL 后，在此服务入口接入鉴权，并通过新增迁移补齐归属外键；
补约束前审计/映射已有开发标识，不自动把测试归属当成真实身份。

保存顺序固定为：pending 短事务提交 → 临时文件 → 正式文件 → ready 短事务提交。
文件 IO 与内容校验卸载至线程，期间没有数据库连接/事务占用。
只有 ready 提交成功才返回完成。事务失败不建立文件与数据库的跨系统原子性承诺。

文件采用 256 bit 随机不透明 key 和独立随机临时名。打开根目录时逐级 O_NOFOLLOW，
目录 fd 固定后所有操作使用 dir_fd。最终根目录必须属于当前进程用户且不能被组/其他用户写入；
新目录 0700，文件 0600。读取拒绝符号链接和非普通文件，设置最大字节数并核验 SHA-256/size。
存储卷仍需由可信本机用户/管理员控制；这不是对恶意同 UID 宿主进程的隔离边界。

同目录 os.link() 发布文件，原子建立最终名字并在目标占用时失败，避免 os.replace() 覆盖。
写入后 fsync 文件，发布与清理后 fsync 目录。原子可见性与断电持久性不同；
不宣称 fsync 在任意文件系统/硬件故障下绝不丢失数据。当前依赖 POSIX dirfd、hardlink、
O_NOFOLLOW 和目录 fsync，macOS 已实测；Windows 和网络文件系统未验证。
取消无法终止正在运行的线程，先等待该线程结束再关闭目录 fd；取消留下的 pending 可诊断。

## 失败与恢复边界

- pending 创建/提交失败：不进入文件保存，返回明确依赖错误。提交响应丢失可能留下 pending。
- 写入/发布失败且尚未发布：只清理本次创建并核验 dev/inode 的临时文件，尝试标记 failed。
- failed 更新也失败：保留 pending；日志记录 artifact_id 和错误分类，不记录秘密/原始异常文本。
- 已发布后刷新/临时清理失败：保留正式文件及 pending，返回失败和可追溯标识。
- ready 更新失败：保留正式文件，返回 completion unconfirmed。若提交结果不确定，
  实际记录可能仍 pending，也可能已 ready；本次调用仍不报告成功，不盲目删除或重试。
- ready 文件缺失/损坏：读取明确失败，诊断报告异常，不自动改变状态。

诊断入口为 ArtifactService.diagnose() 和 python -m agenthub.artifacts.diagnose。
默认只读，连不存在的存储根目录都不创建。可报告 stale_pending、unconfirmed_file、
temporary、orphan_object、缺失/损坏、unsafe/unknown entry；扫描不构成并发一致快照。
没有自动清理、恢复 Worker、分布式锁或 Outbox。任何人工修复需单独确定目标与授权。

## 可复现性与迁移

Compose 固定 PostgreSQL 17.11-bookworm、Redis 7.4.11-bookworm 及下载验证的 digest；
端口只绑定 127.0.0.1，PostgreSQL 使用命名 Volume，凭据只在未提交的 .env。
Compose 5 默认从 Compose 文件所在目录读 .env；infra/.env 是未跟踪的 ../.env 链接。
也可以显式使用 --env-file .env，无需复制凭据。

0001_artifacts 建立最小 Schema。实际检查发现 CHECK 约束被 naming_convention 重复加前缀，
0002_constraint_names 只重命名约束以匹配 metadata，不重写已经执行的 0001、不改表数据。
Alembic check 不保证识别所有 CHECK 名称差异，因此集成测试直接核对 PostgreSQL 约束名。
没有启动 create_all、drop/recreate、自动 downgrade 或数据库清空。

## 依据

- [SQLAlchemy AsyncSession 与并发、提交后属性](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Alembic asyncio 配方](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)
- [Redis asyncio 连接关闭](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)
- [Compose 环境变量与项目目录](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)
- [PostgreSQL 官方镜像版本](https://github.com/docker-library/official-images/blob/master/library/postgres)
- [Redis 官方镜像版本](https://github.com/docker-library/official-images/blob/master/library/redis)
