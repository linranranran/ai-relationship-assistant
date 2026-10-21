# 销售沟通准备：实现、验收与阅读入口

更新：2026-10-13。核心代码、页面和开发库新表已接入；完整主线的真实模型结构输出尚不稳定，**不能把本记录描述为全部场景通过或生产上线**。

## 已实现的链路

1. 页面搜索当前用户已有客户，选择人物；导入项目定义的模拟 JSON，或点击“载入虚构示例聊天”。
2. 后端检查人物归属；PostgreSQL 在短事务中保存不可变原文，用源消息唯一键裁决重复，内容/人物绑定冲突整批回滚。
3. 明确准备入口发送 `response_kind=sales_preparation`，复用正常 request_id、任务、检查点和 SSE。
4. 最新 Direct 查找/澄清人物，再调用只读 `get_sales_preparation_context`。模型目录和真实执行授权都限制写工具。
5. 上下文按 owner + person + as_of 取最近候选，限制消息/Token；档案快照和聊天原文使用不同来源类型，记录 coverage。
6. 回复节点调用现有 LangChain `with_structured_output` 适配，经严格模型与引用校验后确定性渲染卡片。没有证据或校验失败返回明确失败，释放当前会话。
7. 卡片通过原 request_id 保存响应，历史查询关联结果；点击聊天证据读取当前 owner 的原文，档案引用显示生成时快照。

没有自动长期事实回写、待办、客户消息发送或真实企业微信接入。第一次准备只支持最新 Direct，Plan / Discovery 保留已有功能。

## 实际验证

| 验证 | 实际结果 | 边界 |
|---|---|---|
| 既有 Python 检查 | 30/30 通过 | 外部服务临时指向本机关闭端口；异步检查需允许 Windows 本地 socket |
| 既有前端检查 | 3/3 通过 | 不是完整浏览器自动化回归 |
| 后端编译、前端构建、`src` 静态检查 | 通过 | 既有裸 lint 命令会扫描 node_modules，本次使用源码范围 |
| 临时离线业务检查 | 22 组通过，15 个案例输入与证据窗口检查通过 | 模型语义不是由这些检查证明的 |
| 真实 LangGraph 链路 | 9 组通过 | 外部模型/仓储替换；保留图、协议、执行器、上下文 Tool、结构/引用校验；包含同名 interrupt/resume、终态重放、无证据失败、阻止写入 |
| 真实 FastAPI 路由 | 10 项通过 | 仓储替换；验证输入、归属、404/409/422/503 与异常脱敏 |
| 真实 PostgreSQL 仓储 | 7 组通过 | 纯虚构数据，事务回滚；验证首次、重复、正文冲突、换人物、混合冲突整批回滚、原文、owner 隔离 |
| 真实 PostgreSQL 并发 | 2 个相同批次并发：分别新增 8/0，重复 0/8；物理共 8 条 | 随机隔离 owner，结束后精确清理，剩余 0 条 |
| 浏览器界面 | 客户选择、载入示例、导入计数、准备提交、卡片、原文抽屉、刷新展示通过 | 临时 API 提供固定虚构结果；不能冒充真实 Agent、真实 PG 历史或 SSE 联调 |
| 独立审阅发现的生命周期问题 | 3 项均先复现失败再修复通过 | 取消丢回复类型、终态业务失败占用会话、跨回复类型缓存摘要碰撞 |

完整图检查还发现新 Tool 遗漏 Direct 只读授权分类，已修复并通过同一链路复验。

临时检查及原始调试记录保存在忽略目录 `.superpowers/sdd/2026-10-13-sales-chat-evidence-mvp-implementation/`，没有新增仓库测试文件。新表迁移为 `012_customer_chat_message.sql`，可重复启动，不删除已有数据。

## 真实模型验证：如实记录失败

用户共授权最多 **8 次调用**，已用完，不再追加。仅发送虚构资料/聊天、准备单 Schema 与提示词到配置的 qwen3.5-plus；Langfuse 关闭，不读取业务客户数据。

| 场景/阶段 | 调用数 | 结果 |
|---|---:|---|
| normal：完整 8 条主线 | 2 | 两次均 OUTPUT_SCHEMA_INVALID，未取得成功准备单 |
| commitment_unknown：只有承诺，无明确后续 | 2 | 第二次通过；状态 UNKNOWN，无完成证据，引用检查通过 |
| conflict：预算先确定再撤回 | 2 | 两次均 OUTPUT_SCHEMA_INVALID |
| 追加定位 | 1 | 调试捕获器错误处理 SDK 的 LegacyAPIResponse，本次未取得可用诊断；消耗仍计入授权 |
| 追加定位复验：conflict | 1 | 通过；finish_reason=stop，原生结构 Tool 调用可解析，逐字引用通过；预算未确定并建议澄清 |

最后一次实际 usage：prompt 2752、completion 1081、total 3833 Token。此前六次仅保存总消耗，**没有保存失败时的原始终止原因和解析错误**，因此不能事后确诊是截断、字段越界或供应商偶发响应；成功复验也没有证明原因已消除。

为后续 Debug 补充了结构化服务的安全诊断：解析器类型、finish_reason、Schema 字段路径和错误类型；只反馈给模型修复一次，不输出客户原文或字段值到普通事件。离线重放校验错误已验证能定位字段并修复；**这项诊断增强后没有再做真实模型调用**。实际费用以服务商账单为准，Token 数不是货币金额。

准备好 15 个固定案例：normal、same_name、no_chat、reported_person、negation、commitment_unknown、conflict、fake_quote、injection、long_window、owner_isolation、retry_refresh、response_kind、stale_context、future_message。当前没有完整运行 15 个真实模型场景，不提供模型准确率或销售收益百分比。

## 你现在怎么演示和 Debug

先在 PyCharm 重启后端，并确认 `AGENT_TOOL_STRATEGY=direct`；前端按项目原方式启动。后端启动会校验销售窗口配置并确保新表存在。升级前尚未结束的旧请求可能因工具目录版本变化被拒绝，应先停止旧任务再发新请求。

在普通对话中建立一个虚构客户杨鑫，或选一个用于演示的已有客户。在左侧“客户沟通准备”查询并选择客户，点击“载入虚构示例聊天”→“导入聊天”→“生成沟通准备”。同一源文件对同一 owner 换绑另一人物会 409，这是保留证据归属的设计；另一个模拟会话应使用新的来源标识。

不要一次读全部改动，按这个顺序：

| 阅读位置 | 你要弄清的问题 |
|---|---|
| `backend/sales/contracts.py` | 导入数据、事实、建议、引用、承诺状态分别是什么 |
| `backend/db/customer_chat_store.py::save_messages` | 为什么唯一约束加短事务能去重，冲突为什么回滚整批 |
| `backend/sales/context.py::build_sales_preparation_context` | 原文如何被选择，哪些进入模型，为什么裁剪 |
| `backend/tools/sales.py` | 为什么 Tool 只提供数据，不能在内部再运行一个 Agent |
| `backend/sales/preparation.py::prepare_sales_brief` | 如何复用框架生成结构化草稿，预算和一次修复在哪里生效 |
| `backend/sales/evidence.py::validate_brief` | 引用范围、逐字匹配、客户归属能检查什么，不能证明什么 |
| `backend/agent/nodes/generate_response.py::_sales_response` | 如何根据真实工具回执选择销售分支，失败怎样收尾 |
| `backend/services/structured_llm.py::_schema_rejection` | 结构失败如何安全定位字段与终止原因，不泄露客户正文 |
| `backend/db/chat_history_store.py` 与 `frontend/src/sales/` | 已验证结果如何进入历史，引用如何打开原文 |

建议先在 context、validate_brief、_sales_response、_schema_rejection 打断点。一次新准备仍沿 Direct 逐轮流程走：查找人物 → 读取上下文 → 模型停止调用 → 结构化生成/校验 → finalize_memory；没有另建 goal 层。同名用原 interrupt_id 恢复，response_kind 和 request_id 保持不变。

五分钟演示：30 秒用途；1 分钟导入/重复导入；1 分钟准备卡片；1 分钟引用与 UNKNOWN；1 分钟同名/隔离/失败边界；30 秒说明真实企微是待接入数据源。完整 normal 模型场景尚未稳定通过，正式面试演示前优先定位这个失败，不把固定页面预览当真实生成。

## 仍需验证

- 完整 normal 主线的真实模型解析失败原因、修复后的重复稳定性，以及转述/否定/冲突等 15 例的人工语义评分。
- 用户实际 PyCharm 服务与 Neo4j + PG + 模型 + SSE 全链路；真实服务重启/掉线恢复；两个真实登录账号的浏览器切换；移动端。
- 销售人员对建议的可用性评价、实际准备耗时和信息遗漏；当前没有客户业绩效果数据。
- 高并发负载、真实客户资料治理、真实企业微信权限/SDK/增量游标/存档同意；这些属于后续范围。

可描述已实现的模拟聊天导入、去重、证据窗口、结构化准备与引用展示；不能描述已自动读取企微聊天、生产上线或提高成交率。证据存在不等于推理正确，这是本功能最重要的工程边界。
