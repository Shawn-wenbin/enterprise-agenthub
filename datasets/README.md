# 初始数据集合同

v1 共四个 synthetic 公开任务；这不是 20 个已审阅 Case 或任何模型质量得分。
public/ 只包含 PublicCase 严格 Schema 输入。allowed_sources 是相对 fixtures/orderlab 的受控文件集合，
不是 Tenant/Project/Resource ACL，不承载可信 tenant_id、role 或 Principal。
未来运行时须先用 Session → Principal → AuthorizationService 决定实际资源权限，授权后再收窄素材集合。

manifests/orderlab_v1.json 属于评测侧元数据，不得直接作为 Agent 输入或 Tool Result。
它将 Case ID、dataset_version、fixture_version、公开输入 SHA-256 和问题分组关联起来。
每条 grader_status=not_implemented，省略 grader_ref，policy_assertions 为空；没有伪造答案引用或评分成功。
同一实际事件的 retry 和 timeout 任务同属 orderlab_checkout_v1，不能随机拆到 dev/holdout
后声称是独立故障家族。schema 与 uncertain 是初始调查合同，未实现其独立故障版本。

Agent 可见：公开任务、授权的固定源码、接口合同、OpenAPI、请求/日志、历史工单、公开实验。
Grader 独占：参考原因、expected_evidence、reference_patch、隐藏测试、rubric、oracle 和私有策略断言。
本版没有存放这些私有内容，也没有实现 grader、Live Model、Agent、RAG、Runner 或真正运行隔离。
目录分开不等于隔离；后续必须使用独立身份、挂载和环境，不能让 Agent 读取整个 AgentHub 工程。

静态检查同时使用严格字段、受控文件清单、逐文件哈希、路径和符号链接拒绝、敏感标记扫描。
这些是公开数据合同的回归检查，不是自然语言标准答案泄漏的完整判定器；新增材料仍需人工审阅。
未来应把 PublicCase 的 JSON 数据显式投影为输入，不能传入 DatasetManifest 或通过根目录浏览放宽可见范围。
内容实质变化须增加 dataset_version；本版逐输入哈希会检测未发布的修改，不能静默刷新 v1。
