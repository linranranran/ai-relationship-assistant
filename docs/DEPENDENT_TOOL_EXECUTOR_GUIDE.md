# 依赖任务执行器

当前执行器已经支持一条完整链路：

```text
模型生成步骤计划
  -> sanitize_model_tool_calls 校验步骤、参数和依赖
  -> execute_tools 执行安全的前置查询
  -> $ref 读取前置结果
  -> Pydantic 再次校验解析后的具体参数
  -> 高风险步骤生成确认摘要并 interrupt
  -> 用户确认后从 next_tool_index 恢复
  -> 执行高风险 Tool
```

## Tool 计划格式

```json
{
  "tool_calls": [
    {
      "step_id": "find_target",
      "tool": "find_person",
      "args": {
        "query": "张三",
        "search_mode": "exact"
      }
    },
    {
      "step_id": "delete_target",
      "tool": "delete_person",
      "depends_on": ["find_target"],
      "args": {
        "person_id": {
          "$ref": {
            "step_id": "find_target",
            "path": "data.id"
          }
        }
      }
    }
  ]
}
```

字段职责：

- `step_id`：模型生成的计划内步骤名称，只用于表达依赖。
- `call_id`：服务端生成的完整 UUID，用于审计和幂等。
- `depends_on`：当前步骤依赖的前置 `step_id`。
- `$ref.step_id`：从哪个成功步骤读取结果。
- `$ref.path`：从该 Tool 原始返回对象中读取哪个字段。

## 执行记录

每一步都会形成结构化记录：

```json
{
  "step_id": "find_target",
  "call_id": "call_完整UUID",
  "tool": "find_person",
  "status": "SUCCESS",
  "result": {
    "success": true,
    "data": {"id": "p_123", "name": "张三"}
  },
  "error": null
}
```

状态包括：

- `SUCCESS`：执行成功，可以被后续 `$ref` 引用。
- `FAILED`：本步骤失败。
- `SKIPPED`：依赖步骤没有成功，因此没有执行。

## 你接下来实现什么

下一步实现“执行记录持久化与幂等”。当前 `call_id` 已经生成，但还只保存在
LangGraph state 中。你需要设计 `tool_execution` 表或存储接口：

1. Tool 执行前按 `call_id + owner_id` 查询记录。
2. 已经 `SUCCESS` 时直接返回历史结果。
3. 首次执行时写入 `RUNNING`。
4. 成功后写入 `SUCCESS + result`。
5. 失败后写入 `FAILED + error`。
6. 同一 `call_id` 并发执行时只能有一个请求获得执行权。

完成幂等后，再实现“失败后重新规划”：把 `FAILED/SKIPPED` 记录交回规划节点，
限制最多重新规划一次，避免 Agent 无限循环。





```sql
CREATE TABLE tool_execution (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    thread_id     VARCHAR(64)     NOT NULL COMMENT '会话线程ID',
    tool_name     VARCHAR(128)    NOT NULL COMMENT '工具名',
		call_id     VARCHAR(64)    NOT NULL COMMENT '工具调用id',
		owner_id     VARCHAR(64)    NOT NULL COMMENT '调用用户id',
    arguments     JSON            NOT NULL COMMENT 'LLM 返回的原始参数',
    result        JSON            NULL     COMMENT '执行结果',
    status        ENUM('success','failed','pending') NOT NULL DEFAULT 'pending',
    error_message TEXT            NULL     COMMENT '失败原因',
    latency_ms    INT UNSIGNED    NULL     COMMENT '执行耗时(毫秒)',
    create_time    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    update_time    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                  ON UPDATE CURRENT_TIMESTAMP(3),
    KEY idx_thread_created (thread_id, create_time DESC),
		KEY idx_call_id_owner_id (call_id, owner_id DESC),
    KEY idx_tool_status (tool_name, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```
