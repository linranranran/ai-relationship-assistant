# 架构与代码阅读

知客的业务主线是客户资料维护与基于证据的销售沟通准备。模型提出下一步建议；参数、数据归属、确认、幂等、预算和恢复由服务端控制。

## 两条业务链路

### 客户资料与关系

```text
HTTP请求 + request_id
  → 准备上下文 / 解析人物指代
  → 冻结工具策略与版本
  → 模型提出调用
  → 参数与权限校验 / 必要时interrupt
  → 幂等执行工具
  → 观察结果并继续 / 澄清 / 停止
  → 回复、消息与记忆收尾
```

Direct处理完整请求，每轮最多提出一个业务工具，不经过goal拆解。Plan先生成目标和依赖，再规划当前目标的工具步骤；真实ID与 `$ref` 由执行器解析。Discovery保留目标调度，通过搜索/描述工具减少一次暴露的Schema，目前为实验性对照。

三种方式共用执行器；不把Plan称为原创算法，也不把循环结束等同全部业务要求都已完成。

### 销售沟通准备

```text
模拟聊天导入 → customer_chat_message
用户请求 → Direct确认客户ID → get_sales_preparation_context
  → 读取Neo4j档案 + PG近期聊天
  → 受Token预算约束的来源快照与coverage
  → SalesPreparationBrief结构化生成
  → 人物、来源ID、逐字原文及约定状态引用校验
  → 固定文本渲染 + 结构化卡片 + 原文查询
```

Agent只读取证据，生成准备单是后续受控流程。专用准备入口限制为只读工具，并在执行前再次检查；普通聊天取得真实销售上下文回执也可触发卡片生成。

`customer_chat_message`是销售与客户的聊天，`conversation_message`是用户与Agent的对话，后者不能自动升级为客户事实证据。当前检索按客户和时间取最近消息，尚无按meeting_goal的语义排序。

## 存储职责

| 存储 | 内容 | 边界 |
|---|---|---|
| Neo4j | 登录身份、本人Person、客户资料、关系、业务操作回执 | 查询与写入按owner隔离 |
| PostgreSQL | 请求/工具/预算账本、客户聊天、对话、摘要、事实、后台任务、检查点 | 短事务与唯一约束；模型调用不占长事务 |
| Chroma | 人物语义搜索索引 | 可重建次级索引，不代替图中的业务事实 |
| Langfuse | 审计与实验观察 | 可关闭；凭据脱敏，不把未审核的真实客户材料上传到展示项目 |

## 执行与恢复

- `request_id`标识一次用户请求；重试与恢复保持原请求。
- `call_id`标识一个冻结的工具动作，用于执行账本和业务回执。
- `thread_id + interrupt_id`定位会话检查点与待处理交互，不能代替请求幂等。
- 执行租约和execution token防止过期执行者覆盖新执行者状态；未知结果不直接当成未执行。
- Neo4j业务写入与同库回执一起落地，再更新PG执行状态；跨库存储没有共同原子事务。
- 模型、工具、Token等预算写入PG，同一请求重试/恢复不充值。

## 上下文与证据

Agent会话使用最近消息、结构化摘要及长期事实，结合Token预算、人物焦点和歧义澄清选择上下文。后台记忆任务使用游标、版本和失败重试，避免摘要重复覆盖。

销售证据单独装配：保留speaker、时间、source_id、excerpt和截断标记；完整原文仍在PG。准备单保存取证快照，旧卡片不读取当前档案覆盖历史依据。逐字引用校验只能证明文本来自授权来源，不能证明转述、否定和推理语义正确。

## 建议阅读顺序

| 顺序 | 代码 | 看什么 |
|---|---|---|
| 1 | [graph.py](../backend/agent/graph.py) | 节点注册与Command路由 |
| 2 | [direct_nodes.py](../backend/agent/nodes/direct_nodes.py)、[direct.py](../backend/agent/strategies/direct.py) | 决策、校验、观察与停止 |
| 3 | [execute_tools.py](../backend/agent/nodes/execute_tools.py) | 参数引用、确认、幂等与真实执行 |
| 4 | [goal_policy.py](../backend/agent/goal_policy.py)、[plan_tasks.py](../backend/agent/nodes/plan_tasks.py) | Plan目标范围、依赖与验收 |
| 5 | [context.py](../backend/sales/context.py)、[contracts.py](../backend/sales/contracts.py) | 销售证据与输出合同 |
| 6 | [preparation.py](../backend/sales/preparation.py)、[evidence.py](../backend/sales/evidence.py) | 生成、引用校验与固定渲染 |
| 7 | [structured_llm.py](../backend/services/structured_llm.py) | LangChain结构输出、最多两次尝试与预算 |
| 8 | [Chat.jsx](../frontend/src/pages/Chat.jsx)、[sales/](../frontend/src/sales/) | 请求恢复、卡片与原文抽屉 |

已知质量缺陷与人工待审范围见[公开评测](evaluations/showcase/README.md)。
