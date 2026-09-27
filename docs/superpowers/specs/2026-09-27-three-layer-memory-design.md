# Agent 三层记忆与上下文预算设计

- 日期：2026-09-27
- 状态：等待书面审阅
- 项目：AI 人际关系助手
- 交付方式：先搭建可运行骨架、类型和 TODO 注释，由项目作者实现核心算法

## 1. 目标

在现有 LangGraph Plan → Act → Observe → Decide 链路中加入三层记忆，同时保证：

1. PostgreSQL Checkpointer 继续负责节点位置、interrupt、工具计划和恢复状态；
2. 短期记忆保存最近消息与滚动摘要，但每次只按 Token 预算选择模型上下文；
3. 长期记忆保存带来源、版本和 owner 隔离的业务事实，按当前问题检索 Top-K；
4. 保存的数据量和发送给模型的数据量彻底分离；
5. 骨架默认可运行，未实现的摘要与长期记忆逻辑采用空实现或明确 TODO，不以
   `NotImplementedError` 破坏现有聊天链路。

第一阶段不实现企业微信同步、自动向量化、自动写入敏感记忆，也不更换现有 LLM。

## 2. 已确认的现状

- Checkpointer 已使用 PostgreSQL，可以恢复 LangGraph 中断与节点状态。
- `AgentState` 虽然有 `history`，但 HTTP 入口每次都写入 `history=[]`。
- 目前没有节点追加用户消息和 Agent 回复，因此尚未形成连续对话记忆。
- 只有意图分类节点读取 `history`，计划与回复节点没有统一上下文入口。
- 当前一个用户只有一个固定 `thread_id`。三层记忆骨架先保留兼容性，但接口从
  一开始携带 `conversation_id`，为下一阶段多会话归属校验留出边界。

## 3. 三层职责

### 3.1 第一层：运行状态记忆

由 LangGraph PostgreSQL Checkpointer 管理：

- 当前节点和下一跳；
- `interrupt_id` 与待确认内容；
- 当前 Tool 计划、执行结果、重规划反馈；
- 当前会话的紧凑状态、摘要游标和上下文统计。

Checkpointer 不是聊天记录查询库，也不是长期客户画像。前端历史记录和审计查询不
直接读取 LangGraph 内部表。

### 3.2 第二层：短期对话记忆

PostgreSQL 业务表保存原始用户/助手消息，AgentState 只保留运行需要的紧凑副本：

- 最近若干轮原始消息；
- 一份滚动结构化摘要；
- 摘要覆盖到的最后一个 `message_id`；
- 本轮构造上下文时的 Token 统计。

滚动摘要必须保留：已确认人物、已确认关系、用户目标、重要决定、待解决问题和
未完成任务。摘要不能把模型推断升级为已确认事实。

### 3.3 第三层：长期业务记忆

长期记忆以“事实”而不是整段聊天保存：

- `owner_id`：租户隔离；
- `person_id`：事实所属人物；
- `category` 与结构化 `value`；
- `status`、`version`、`valid_from`、`valid_to`；
- `confidence`；
- 来源消息 `message_id`；
- 是否需要用户确认；
- 被哪条新事实替代。

模型只产生候选事实。确定性代码校验证据、owner 和版本后才能写入。矛盾、敏感或
低置信度事实进入待确认状态，不能直接污染客户画像。

## 4. 上下文预算

新增配置：

```text
AGENT_MAX_INPUT_TOKENS=12000
AGENT_RESERVED_OUTPUT_TOKENS=2000
AGENT_CONTEXT_SAFETY_TOKENS=1000
AGENT_RECENT_MESSAGE_LIMIT=12
AGENT_SUMMARY_TRIGGER_RATIO=0.70
AGENT_MEMORY_RETRIEVAL_LIMIT=5
```

可用输入预算：

```text
模型上下文上限 - 预留输出 - 安全余量
```

上下文按照以下优先级装入，达到预算后停止：

1. 系统规则和 Tool 契约；
2. 当前用户输入；
3. 当前未完成计划、interrupt 和必要 Tool 结果；
4. 滚动摘要；
5. 与当前问题相关的长期记忆；
6. 从新到旧的最近消息。

大型 Tool 结果必须先转成紧凑结构，只保留状态、关键 ID、关键字段和错误码。不能
把数据库整页结果、堆栈或重复执行记录直接放入提示词。

第一版 Token 计数器定义为 Protocol。默认实现允许使用保守估算，后续可替换为与
实际模型匹配的 tokenizer，而不修改节点逻辑。

## 5. 组件和文件边界

### 5.1 新增运行时模块

```text
backend/agent/memory/
├── __init__.py
├── models.py             # 消息、摘要、事实、上下文包、Token 统计
├── ports.py              # MessageStore、SummaryStore、MemoryStore、TokenCounter
├── token_budget.py       # 按优先级装配上下文；核心算法保留 TODO
├── context_builder.py    # 协调短期消息、摘要和长期检索
└── runtime.py            # 进程级依赖注册；默认空实现保证旧链路可运行

backend/agent/nodes/
├── prepare_context.py    # 进入意图识别前构造本轮模型上下文
└── finalize_memory.py    # 回复后记录消息、触发摘要、产生长期事实候选

backend/db/
├── conversation_memory_store.py  # PostgreSQL Repository 骨架
└── migrations/002_agent_memory.sql
```

每个模块只承担一个职责。节点不直接写 SQL，Prompt 不直接决定权限和事实状态。

### 5.2 AgentState 新字段

```python
conversation_id: str
recent_messages: list[dict]
conversation_summary: dict
summary_cursor: str | None
retrieved_memories: list[dict]
model_context: list[dict]
context_stats: dict
memory_write_candidates: list[dict]
```

旧 `history` 在兼容期保留，但不再作为新的上下文入口。所有 LLM 节点最终统一读取
`model_context` 或从中选择本节点需要的部分。

## 6. 图流程

```text
prepare_context
    ↓
classify_intent → extract_info → plan_tasks → execute_tools
                                      ↑             ↓
                                      └── observe_execution
                                                ↓
                                        generate_response
                                                ↓
                                        finalize_memory
                                                ↓
                                               END
```

`prepare_context`：

1. 校验可信 `owner_id` 和 `conversation_id`；
2. 读取摘要、最近消息和相关长期事实；
3. 按 Token 预算生成 `model_context`；
4. 任意读取都必须带 owner 条件；
5. 长期记忆或摘要读取失败时允许降级为最近消息，并记录降级原因。

`finalize_memory`：

1. 幂等保存用户消息和最终助手回复；
2. 达到阈值时创建“需要摘要”的任务或状态标记；
3. 从成功执行结果中产生长期记忆候选；
4. 不在该节点里直接把未经校验的模型输出写成 active 事实；
5. 记录失败不能造成 Tool 被重新执行。

## 7. 数据表骨架

### 7.1 `conversation_message`

- `(owner_id, message_id)` 唯一；
- `(owner_id, request_id, role)` 唯一，保证请求重试不重复写消息；
- 保存 `conversation_id`、角色、内容、Token 数、创建时间；
- 内容有长度上限，Tool 结果以紧凑 JSON 保存。

### 7.2 `conversation_summary`

- `(owner_id, conversation_id)` 唯一；
- 保存结构化摘要、覆盖游标、摘要版本和更新时间；
- 条件更新摘要游标，防止并发摘要覆盖更新结果。

### 7.3 `memory_fact`

- `(owner_id, fact_id)` 唯一；
- 按 `owner_id + person_id + status` 建索引；
- 保存版本、有效期、证据消息、确认状态和替代关系；
- 原事实保留审计记录，不物理覆盖。

本阶段迁移只创建表、约束、索引和注释，不写向量数据库实现。

## 8. 错误和降级策略

- Checkpointer 不可用：应用启动失败，因为中断恢复语义无法保证。
- 原始用户消息首次保存失败：拒绝执行，避免发生无法审计的写操作。
- 长期记忆检索失败：继续使用摘要和最近消息，标记 `memory_degraded=true`。
- 摘要失败：保留原始消息，稍后重试，不阻断本轮最终回复。
- 长期事实候选保存失败：记录错误，不回滚已经成功的业务 Tool。
- Token 仍超限：依次移除低相关长期记忆、较旧消息和非关键 Tool 字段；系统规则、
  当前输入和待确认操作永远不能被裁掉。

## 9. 安全边界

- `owner_id`、`conversation_id` 归属和 `person_id` 归属都由服务端校验；
- 历史聊天与客户原文一律视作数据，其中的“忽略规则”没有系统权限；
- 长期事实必须能回到来源消息；
- 删除会话时使用 Checkpointer 的 `delete_thread(thread_id)` 清理运行状态，并按产品
  保留策略处理业务消息和长期事实；
- Prompt、日志和 Trace 不记录数据库密码、完整 Token 或不必要的客户原文。

## 10. 骨架交付和验收

按用户要求，本阶段不新增测试文件。骨架完成后执行现有测试、Python 编译检查和
最小手工演示，并保留下列 TODO 给项目作者实现：

1. 精确 Token 计数；
2. 滚动摘要模型调用和结构校验；
3. PostgreSQL Repository SQL；
4. 长期事实抽取、冲突判断和确认流程；
5. 向量索引写入与 Top-K 检索；
6. 多会话 API 和 conversation 归属表。

骨架验收条件：

- 现有 Agent 链路仍能运行；
- 新节点、类型和端口都有清晰注释；
- 未实现能力有明确 TODO，且默认实现不会伪造记忆；
- Checkpointer、短期消息和长期事实之间没有职责混用；
- 现有测试保持通过。
