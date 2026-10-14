# ADR 0002 — Unit 1.4 授权与历史产物归属

日期：2026-10-14；状态：accepted；范围：Unit 1.4。

## 决策

沿用共享 PostgreSQL Schema 和 Unit 1.3 的 UUID 字符串编码，不修改 User/Session。
Membership 按 `(tenant_id, user_id)` 唯一；membership_roles 的复合主键支持固定多角色。
Project 按 `(tenant_id, slug)` 唯一。resource_acl 当前只表示真实 Project，采用显式
membership_id/project_id/scope，不创建多态的未来资源或通用策略引擎。
ACL 通过 `(tenant_id, project_id)`、`(tenant_id, membership_id)` 复合外键约束同租户引用。

Principal 从 UserIdentity 和有效数据库 Membership 构造，按目标租户保持角色隔离。
AuthorizationService 每次判断重查有效用户、租户、Membership、角色、项目和 ACL，
不把 Principal 的旧角色集视为长期权限证明，不缓存判定。列表在 SQL JOIN/EXISTS 中限制范围。
Tenant Admin 的无 ACL 例外明确限定项目元数据读取/管理；不是通用内容读取或外部写入授权。
当前角色定义后续 Action 能力，但项目 service 对尚无真实资源上下文的 Action 返回
authorization_context_required，不把项目准入当作 owner、自审、原文或批准版本校验证明。
后续业务需在实际读写边界组合具体资源权限与上下文，不能仅使用角色能力函数。

## 历史 Artifact

ADR 0001 原计划在 Unit 1.4 为原归属字段直接补外键。实际旧字段为 String(128)，
历史测试/验证数据使用 `verification/restart`、随机 UUID 及其他尚无对应实体的内部标识。
本地开发库审计为 0 条，隔离库保留真实非空的 ready/pending/failed 三种历史记录。
不能据此将所有旧标识强制转换为 UUID，不能为通过外键而虚构 Tenant/Project。

因此保留 tenant_id/project_id 的类型和值，追加可空 authorized_project_id。
复合 FK `(tenant_id, authorized_project_id)` 指向 projects 的 `(tenant_id, id)`；
CHECK 要求非 NULL 的关联等于原 project_id。新迁移建立的项目表为空，所有旧关联保持 NULL，
迁移不执行 Seed、不自动回填、不重分配归属。当前内部保存只对数据库里精确匹配原归属的项目
记录关联，仍使用原有受控内部读取合同；项目 ACL 不传入 ArtifactService，也不放宽其范围。

NULL 关联代表未确认归属，不是默认租户或公开读取授权。未来公开产物 API 必须拒绝这种记录，
并另外校验具体产物内容权限，不能仅凭 Project ACL 或关联外键放行。
直接给全部旧 tenant_id/project_id 加非空 FK 仍需逐条可信映射：先只读审计、由操作人员确认目标、
在独立授权的迁移中回填关联并核验字节/元数据，最后才考虑原字段类型和完整 FK 收紧。
当前新关联 FK 已生效；旧标识的完整 FK 不声称完成，也不以清库绕过。

## Seed 与撤权边界

固定两个租户、同 slug 项目和四个独立账号。全部数据在一个短事务中创建；事务 advisory lock
序列化并发 Seed，Argon2id 计算期间释放数据库连接。重复执行比较固定记录，不重置密码。
停用、删除、角色/ACL 改变或自然键占用导致 seed_conflict，整个调用不修改已有状态。
只允许 development/test，密码配置必须完整；不在应用启动时执行。

授权观察已提交的状态，后续请求/调用立即生效；不声称撤销正在执行的请求或外部副作用。
当前没有业务修改 API。后续敏感写入必须在实际写入边界再次鉴权，并按业务需要解决检查与写入竞争。
