# Direct 请求级 ReAct 改造

用户要求保留 Plan、Discovery，Direct 不再先识别意图、拆 goal 和逐 goal 调度，直接围绕完整原始请求循环调用工具。目标是减少编排负担，并清楚比较不同决策策略；不声称原创 Plan，也不以代码数量作为企业级标准。

## 架构与取舍

- 新 Direct：人物指代解析 → 三层记忆上下文 → direct_decide → direct_validate → 公共 execute_tools → direct_observe → 下一轮或 direct_finish。
- Plan、Discovery 保持当前 goal 路径。Discovery 的目录发现仍在单目标内运行。
- 使用已有 LangGraph StateGraph、Command、Checkpointer、interrupt；不引入第二个 Agent 框架、Redis/Celery 或新依赖。
- 不保留隐藏的需求清单充当另一套 goal。模型负责理解完整请求及提出停止；工具回执证明具体操作结果，不证明所有自然语言要求都已完成。

## 执行边界

- Direct 展示项目已注册工具；当前产品的普通登录用户可调用自己的业务工具，数据库继续按服务端 owner 隔离。这不是新角色权限系统。
- ID 只能来自当前请求可信工具结果、服务端本人及已验证会话指代；称谓路径必须来自真实查询；新增人物前需最新同名精确查询明确 NOT_FOUND。
- 无 goal 后不再声称能确定性校验自然语言写入范围。Direct 的六种写工具均展示服务端生成的目标、字段、新值并使用现有确认中断；拒绝本次写入后模型可继续独立查询。取消则结束请求，保留已经提交的操作。
- 确认绑定原 call_id 与完整业务参数，用户回答只能批准/拒绝，不能替换参数。不得把一次确认扩展到下一轮。
- 每轮最多一个工具；参数错误只给一次连续修复；请求总模型/工具/重规划/澄清/时间/Token 预算和无进展保护继续生效。
- IN_PROGRESS/TRANSIENT 继续 WAIT 原动作，恢复不重新调用模型、不生成新 call_id；权限/内部/未知失败停止，普通业务失败反馈模型。

## 状态和兼容

- Plan/Discovery 策略版本仍为 1；新 Direct 为 2。旧 Direct 1 的请求与检查点继续原 goal 流程，不能静默切换。
- 新 Direct 使用请求级 direct_tool_calls、direct_tool_results、direct_clarifications、direct_final_message，按 call_id 合并；候选选择替换同一动作的观察结果，不删除数据库审计。
- call_id 使用 request_id + 已保存的 decision_seq；供应商 tool_call_id 只配对协议消息。新请求必须清空 Direct 工作区，保留会话记忆。
- 模型停止后最终回复依据整次请求回执；API COMPLETED 表示生命周期结束，completion_basis=MODEL_STOP_WITH_TOOL_RECEIPTS，requirements_verified=false。强制停止给明确原因，不伪造逐 goal 成功。
- 记忆、焦点、工具列表、SSE、Langfuse 使用完整请求账本；预算收尾继续核对 PostgreSQL/Neo4j 回执。

## 验收与限制

按用户既有要求不新增仓库测试文件；在忽略目录进行隔离的图运行检查，运行相关现有检查。模拟模型和存储，关闭 Langfuse，不访问真实业务数据库，不启动用户后端，不新增付费模型复测。

覆盖：混合修改与查询、写入确认/拒绝/取消、候选澄清、自由文本补充、WAIT 后恢复同动作、重复写入复用、预算停止保留回执、协议修复、虚构 ID 拒绝、跨 owner 工具参数隔离、旧 Direct/Plan/Discovery 路由兼容、下一请求清空账本、最终记忆包含全部步骤。

此次修改不改 .env 当前模式、不提交或推送、不覆盖用户 tests/function call test.py。沿用当前 develop 上尚未提交的三策略实现，创建新 worktree 不会包含它，因此在现有工作区继续。

## 面试描述

称为三种可配置的工具决策/暴露策略：Direct 是请求级 ReAct 式循环，单工具是本项目的串行策略；Plan 借鉴规划与执行分离，为业务目标增加依赖与回执验收；Discovery 借鉴 OpenClaw 的按需发现工具目录，在较大目录中减少初始 Schema 暴露，目前为备选。小目录下发现轮次可能增加成本，需完整业务链路评测。

参考：[LangChain Agents](https://docs.langchain.com/oss/python/langchain/agents)、[LangChain Planning Agents](https://www.langchain.com/blog/planning-agents)、[LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[OpenClaw Tool Search](https://github.com/openclaw/openclaw/blob/main/docs/tools/tool-search.md)。
