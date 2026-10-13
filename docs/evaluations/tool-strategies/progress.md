# Implementation ledger — 2026-10-11-tool-strategies-implementation.md

- User authorized implementation on 2026-10-11; all six tasks are in scope.
- Baseline: 20 existing offline checks passed before changes.
- Ruling: implement in the user's current develop checkout for local PyCharm learning/debugging; preserve modified tests/function call test.py. No branch, commits or push.
- Ruling: do not add repository tests as requested; use existing checks and temporary offline probes. No production DB writes during verification.
- Pre-flight: Task 1 completion contracts feed Task 3 loop policy; Task 2 canonical actions feed Tasks 4/5; Task 3 guards cover both modes; shared existing executor/confirmation is preserved.
- Tasks 1–5: implemented and verified offline. Task 6: local comparison and authorized final provider comparison recorded. User explicitly authorized the configured Alibaba Cloud destination, synthetic inputs and API cost on 2026-10-11. Remote migrations were not applied during this work.

## 已实现与阅读顺序

1. `backend/agent/goal_completion.py`：纯代码完成合同。找到人物 ID 不等于查到了爱好；部分字段更新也形成进展。
2. `backend/agent/goal_scope.py`：公共业务范围。参数引用解析后、claim 前验证目标人物、属性和关系；三模式都不能修改用户未请求的数据。
3. `backend/agent/nodes/strategy_nodes.py`：LangGraph 决策、验证、执行发现操作、观察与路由。每轮返回 Command；没有节点内部无限 while。
4. `backend/agent/strategies/direct.py`、`discovery.py`：SDK tools 与完整 assistant/tool 消息协议；控制函数和业务工具分别进入对应节点。
5. `backend/services/tool_calling_llm.py`：完整协议组裁剪、工具 Schema Token 记账、模型结果缓存与审计。
6. `backend/agent/nodes/execute_tools.py`：三模式复用确认、幂等和真实业务执行；提交字段与运行默认参数分离，WAIT 恢复原 call_id。

## 验证证据

- 最终现有检查：26 项通过；包含人物身份、引用解析、查询范围和 LangGraph 兼容检查。没有新增仓库测试文件。
- `offline-results.json`：48 个完整图场景通过，外加纯边界断言。模型响应脚本化、业务仓储为内存替身、Checkpoint 为 InMemorySaver；分类/抽取/最终回复/记忆写入按场景替换，不能视为全系统集成验收。
- 脚本化 Token 数为替身 Usage，只验证额度记账与停止行为，不能用于比较真实成本。已测普通完整运行最大 super-step 为 29；默认 recursion_limit 为 64，并保留已有 RemainingSteps 提前收尾。
- 覆盖查询爱好、含属性新增、多目标依赖、同名澄清、删除确认、WAIT、业务提交后崩溃、PG success 后目录变化、错误人物/额外清空拒绝、合法清空、重复读取/描述停止、提前结束、参数修复和发现额度耗尽。
- `provider-protocol.json`：真实 qwen-max 接受 Direct/Discovery 的 tools 和 parallel_tool_calls=false；strict=false。仅协议探测，未执行业务工具。
- `provider-preliminary.json`：中间版本各模式一次真实模型“查询爱好”诊断，三者完成；使用模拟业务库。样本小且早于最终修复，不能推导完成率、速度或成本优势。诊断暴露的 query/name 参数误用和 data.0.id 引用已修正。
- `provider-final.json`：最终授权复测，qwen-max、temperature=0、11 工具目录，三模式各 3 次；共 9 次场景运行、8 次完成。没有在失败后重跑挑选样本，也没有扩大 API 调用范围。
- 独立复审：发现并修正 Plan 未校验具体人物/属性、默认参数影响 WAIT 恢复等问题；最终复审未发现新的可复现阻塞问题，仅使用纯代码/替身。
- 最终源码编译与 diff 格式检查通过。没有提交或推送。

## 本地启用

`.env` 选择 `AGENT_TOOL_STRATEGY=plan|direct|discovery`（填写其中一个值），默认 plan。配置只改变新 request_id；已有请求沿用首次保存的策略。修改后由用户在 PyCharm 重启后端。

启动流程会执行兼容迁移 `010_agent_request_strategy.sql` 与 `011_tool_strategy_loop_budget.sql`；本次尚未对远程 PostgreSQL 执行，启动账号需具有对应 DDL 权限。已有初始化入口也包含这两份迁移。

Discovery 使用有限目录的中英文词法检索，保留实验性。正常继续不消耗重规划额度；协议/工具错误修复、无进展、提前结束、发现操作和请求总预算各有停止边界。默认 strict=false，未经验证的兼容服务不能假设支持 strict=true。

## 尚未验证

- 真实 PostgreSQL/Neo4j 迁移、并发、进程崩溃及持久化 Checkpointer 集成；真实 Langfuse 平台回放。
- strict=true 的供应商兼容性；100 工具规模的真实模型比较（仅做了模拟目录词法候选上限检查）。
- 不能把当前结果称为生产验收或证明某模式优于另一个模式。

## 最终真实模型样本

固定问题为“张建国的爱好是什么？”，内存人物 p1 的爱好为钓鱼。分类、抽取、最终回复和记忆写入被替换；测量的是工具决策与模拟执行链路，不是完整产品响应时间。完成依据为公共目标合同通过，并实际调用 get_person_detail。

| 模式 | 完成样本 | 模型调用数范围 | 结算 Token 中位数 | 延迟中位数 |
| --- | --- | --- | --- | --- |
| ToolPlan | 2/3 | 1–2 | 1259 | 4.54 秒 |
| Direct | 3/3 | 2 | 2347 | 4.29 秒 |
| Discovery | 3/3 | 4–5 | 5753 | 7.27 秒 |

Token 为请求预算的 charged_tokens：有供应商 Usage 时按 Usage 结算，否则保守保留预占值。中位数包含失败样本，不是成功样本筛选后的结果，也不能直接换算为价格或断言统计优势。

ToolPlan 第 3 次在两次结构化调用后仍报 OUTPUT_SCHEMA_INVALID，业务工具未执行，任务明确失败。临时 SDK 记录器没有解析 Plan 的 raw-response 包装，未保存这次公开响应，因此不能确定是缺少工具调用、参数 JSON 错误还是 Schema 不匹配；不应据此猜测并修改产品代码。

Discovery 三次首轮都误用 find_person 的 name 参数，校验拒绝后模型调用 tool_describe，修复为 query/search_mode，再完成查询。其中一次还返回了函数名和参数为空的 tool_calls，协议修复后才继续。记录说明后端边界和有限修复路径生效，同时反映此模型/小目录下的额外开销。

这组样本不足以改变默认策略：默认仍为 plan，Discovery 保持实验性。要选择生产策略，还需更多业务场景、足够样本、真实存储集成及明确的费用/延迟目标。
