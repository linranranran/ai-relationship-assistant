# Agent 中断与恢复：项目落地指南

这份代码骨架已经把一条真实链路接通：

```text
POST /api/chat
  -> classify_intent
  -> extract_info
  -> plan_tasks
       -> 普通操作：execute_tools -> generate_response
       -> 高风险操作：request_confirmation -> interrupt

POST /api/chat/resume
  -> thread_id 定位会话 + interrupt_id 定位暂停点
  -> Command(resume={interrupt_id: {confirmed}})
  -> request_confirmation 从头重跑
       -> confirmed=true：服务端注入 confirmed=True -> execute_tools
       -> confirmed=false：结束流程，不执行 Tool
```

## 你先读这 5 个文件

1. `backend/agent/confirmation.py`
   - 定义哪些 Tool 属于高风险操作。
   - 清除模型伪造的 `owner_id`、`confirmed`。
   - 只有用户确认后，代码才注入 `confirmed=True`。
2. `backend/agent/nodes/plan_tasks.py`
   - 大模型生成 Tool 计划。
   - Python 代码判断是否进入人工确认节点。
3. `backend/agent/nodes/request_confirmation.py`
   - `interrupt()` 暂停工作流。
   - LangGraph 根据 `interrupt_id` 投递恢复值，节点决定执行或取消。
4. `backend/agent/graph.py`
   - 注册确认节点和检查点保存器。
5. `backend/routers/chat.py`
   - `/api/chat` 发起任务。
   - `/api/chat/resume` 恢复任务。

## 你应该从哪里开始实现

### 第一步：完善确认摘要

位置：`backend/agent/confirmation.py` 的 `build_confirmation_request()`。

当前摘要只显示人物 ID 或关系 ID。你需要调用只读查询，显示用户真正能理解的内容：

```python
# 伪代码
person = get_person_detail(person_id, owner_id)
relation_count = count_person_relations(person_id, owner_id)
summary = f"将删除 {person.name}，同时删除与他关联的 {relation_count} 条关系"
```

注意：构建摘要只能查询，不能执行删除等副作用。

### 第二步：给 Tool 计划做严格校验

位置：`backend/agent/confirmation.py` 的 `sanitize_model_tool_calls()`。

建议为每个 Tool 建立 Pydantic 参数模型：

```python
# 伪代码
TOOL_ARGUMENT_MODELS = {
    "delete_person": DeletePersonArgs,
    "find_person": FindPersonArgs,
}

model = TOOL_ARGUMENT_MODELS[tool_name]
validated_args = model.model_validate(raw_args).model_dump(exclude_none=True)
```

遇到未知 Tool、缺少必填参数或类型错误时，不要继续执行，转到
`ask_clarification` 告诉用户需要补充什么。

### 第三步：实现持久化检查点

位置：`backend/agent/graph.py`。

当前使用 `InMemorySaver`，服务一重启就无法恢复。先用它跑通流程，再替换为
SQLite 或 PostgreSQL 检查点。替换后节点代码不需要改变。

你要补充：

- 应用启动时创建一个共享 checkpointer；
- 应用关闭时释放数据库连接；
- 检查点过期策略；
- 同一个 `thread_id` 的并发控制。

### 第四步：增加幂等和审计

为每个 Tool 调用生成 `call_id`，执行写操作前查询该 ID 是否已经成功。确认、
取消、执行结果都写入审计日志。这样网络重试不会重复删除或重复创建数据。

建议审计字段：

```text
call_id, owner_id, thread_id, interrupt_id, tool_name,
sanitized_args, decision, status, created_at, finished_at
```

## 前端怎么接

第一次请求如果需要确认，会返回：

```json
{
  "status": "CONFIRMATION_REQUIRED",
  "response": "删除人物 p_xxx。是否继续？",
  "confirmation": {
    "interrupt_id": "LangGraph 生成的 ID",
    "summary": "删除人物 p_xxx",
    "tool_names": ["delete_person"]
  }
}
```

用户点击确认或取消后，前端调用：

```json
POST /api/chat/resume
{
  "interrupt_id": "LangGraph 生成的 ID",
  "confirmed": true
}
```

前端不要重新发送“确认删除”这句话到 `/api/chat`。恢复必须携带第一次响应中的
`interrupt_id`。后端用登录态生成 `thread_id` 定位会话，再由 LangGraph 将确认值
投递给这个会话中对应的暂停点。

## 当前版本的明确边界

- 每个用户暂时只有一个默认会话。
- 检查点暂存在内存，服务重启会丢失。
- 混合任务中只要含高风险 Tool，整组 Tool 会一起等待确认。
- 当前只有删除人物、删除关系属于高风险操作。
- 确认框架已接通，确认摘要、参数模型、持久化、幂等和审计留给你逐步实现。
