# OrderLab 订单接口规范 0.3（synthetic）

生效时间：2026-09-25T00:00:00Z；fixture_version：orderlab_v1。
所有账号、商品、订单、请求和时间均为合成数据。此文档描述预期合同，业务实现仍需验证。

## 创建与查询

`POST /orders`，JSON 请求：

```json
{"operation_id":"checkout_001","sku":"sku_demo_book","quantity":1,"currency":"CNY"}
```

必须提供 `X-Request-ID` 和 `X-Mobile-Version`。后者允许 `2.2`、`2.3`。
operation_id、sku、X-Request-ID 为 1–64 位小写字母、数字、下划线或短横线，首位为字母或数字。
quantity 为严格整数 1–20，不接受字符串；currency 仅接受 CNY；未声明字段返回 422。
该实验无支付、用户认证、网络服务依赖或跨租户数据；未来素材访问授权由平台控制。

首次创建返回 201：

```json
{"operation_id":"checkout_001","sku":"sku_demo_book","quantity":1,"currency":"CNY","order_id":"ord_0001","created_at":"2026-10-01T08:00:02Z"}
```

`GET /orders?operation_id=checkout_001` 返回 200 和该业务操作的持久状态列表；不存在时返回空列表。
此处“持久状态”指应用实例存活期间内存中的订单，不承诺跨重启持久化。
缺少或格式错误的查询参数返回 422。准确字段与实际接口结构见同目录 openapi.json。

## 业务身份和重试

operation_id 在一次结算操作开始时分配，传输超时与重试均保持不变；不同结算使用不同值。
X-Request-ID 标识一次 HTTP 尝试，允许每次尝试产生新值；它用于关联请求与日志。
移动端 2.3 使用每次尝试独立的请求 ID，保留原 operation_id 和业务参数。

正确性要求：同一 operation_id、相同业务参数的重复提交，只应对应一笔订单并返回该订单；
在此最小接口中重放也可返回 201。同一业务操作变更参数应返回 409，不能悄悄创建另一笔订单。
当前 OpenAPI 声明 409；需要通过源码及公开实验检查实现是否覆盖完整业务冲突语义。
不同 operation_id 即使参数相同，也不能错误合并。

客户端 ReadTimeout 只说明该尝试未获得响应，不证明服务端没有创建订单。
客户端应保留业务操作标识和原始参数，允许一次受控重试，之后查询业务状态，避免无限重试。
本快照不处理真实网络、支付、并发数据库事务与全局幂等键过期策略。

## 错误语义

- 422：请求字段、必填头或查询参数不满足 Schema；不得创建订单。
- 409：重复提交与已记录参数冲突；错误 detail 为 request_payload_conflict。
- ReadTimeout：客户端传输错误，没有可供客户端确认的 HTTP 成功/失败状态。

2.3 没有获得任意扩展请求字段的许可；候选字段 delivery_note 还需接口合同与探针佐证。
历史工单只能作为比较线索，不代表当前事件具有相同原因。
