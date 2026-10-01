# Agent 身份上下文契约

这份文件只描述你开发 Agent 链路时可以依赖的身份边界。

## HTTP 入口

`POST /api/chat` 的浏览器请求体只允许：

```json
{
  "message": "我应该怎么称呼张三？"
}
```

浏览器传入的 `user_id`、`user_name`、`history` 会被后端直接拒绝。账号身份来自 Bearer Token；历史记录应由 Agent checkpoint 根据服务端可信标识恢复。

## AgentState 中的可信字段

Chat 路由会在进入图之前写入：

```python
state["user_id"]        # 登录账号 ID，也是该账号私有关系图的 owner_id
state["self_person_id"] # 私有关系图中“我”的 Person ID
state["request_id"]     # 本次请求的链路追踪 ID
state["user_name"]      # 当前暂用手机号，不能作为数据库主键
```

你的 Tool 执行节点应覆盖大模型生成的身份参数：

```python
args["owner_id"] = state["user_id"]

if tool_name == "query_relation_path":
    args["from_person_id"] = state["self_person_id"]
```

不要让 LLM 或前端决定 `owner_id` 和 `from_person_id`。LLM 只负责生成业务参数，例如目标人物、关系类型和待写入的人物资料。

## User 与 Person 的关系

- `User` 是登录账号，负责认证和数据所有权。
- 每个 `User` 在自己的私有关系图中绑定一个 `is_self=true` 的 Person。
- 新账号注册时，User、本人 Person、`HAS_IDENTITY` 绑定在同一次 Neo4j 事务中创建。
- 旧账号第一次登录时会自动补齐本人 Person。
- 本人 Person 不能通过人物删除 Tool 删除。
- 同一个现实人物出现在不同账号的私有关系图中时，当前版本允许存在多个 Person 副本；不做跨租户自动合并。

## Tool 数据隔离

Person 和 Relation 的查询、更新、删除都必须同时命中资源 ID 与 `owner_id`。找不到和无权限对 Tool 来说使用同一类失败结果，避免泄露其他账号是否存在某个 ID。
