# Direct 请求级 ReAct Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 用户已要求整理方案并开始执行，由当前会话连续实施。

**Goal:** Direct 跳过 goal，处理完整请求；Plan、Discovery 和旧请求继续原流程。

**Architecture:** 增加请求级 Direct 节点和回执账本，共用执行器、交互、预算、记忆、审计。版本冻结使已有 Direct 1 不被迁移；Direct 2 的所有写工具需确认。

**Tech Stack:** Python、LangGraph Command/interrupt/Checkpointer、现有 SDK/Pydantic/PostgreSQL/Neo4j。

**Spec:** docs/superpowers/specs/2026-10-11-direct-request-loop-design.md

## Global Constraints

- Plan/Discovery 版本 1，新 Direct 版本 2；旧 Direct 1 保留 goal 行为。
- 每轮一个工具；所有 Direct 2 写入确认；ID 和关系路径必须有可信来源。
- 不新增仓库测试，不调用付费模型或业务数据库，不启动后端，不改 .env、不 commit/push。
- 保留用户现有修改，在当前 develop 实施。

## Review Focus

- 确认恢复不能授权另一个动作或改变字段；拒绝不能被模型立刻重提相同操作绕过。
- WAIT 重试保留原 call_id，候选选择能替换同一动作的协议结果。
- 三层记忆和预算收尾必须看到全部轮次，不能只保留最后一次工具。
- 新请求清空账本，旧策略版本恢复不进入请求级循环。
- 无显式 goal 时不得把模型停止包装成确定性业务验收。

### Task 1: 策略版本、请求账本与模型适配

Files: strategies/contracts.py、factory.py、direct.py、discovery.py、catalog.py；新增 direct_runtime.py；state.py。

Interfaces: is_request_direct(state)->bool；current_strategy_version(mode)->str；require_supported_strategy(state)->None；direct_calls(state)/direct_records(state)->list[dict]；direct_bindings(state)->dict；validate_direct_call(state,call)->None。

- [x] 新增 Direct 2 标记、请求账本、可信绑定/新增预查/路径校验；拒绝写操作保存指纹。
- [x] 保留 GoalScopedDirectStrategy 给 Discovery/旧 Direct；新 DirectStrategy.propose 看完整请求与执行协议消息。
- [x] ToolCatalog.for_request 返回当前产品注册工具，工具 owner 隔离继续在公共执行器落实。
- [x] 隔离检查版本选择、同 call_id 替换、ID 来源、拒绝指纹及旧 Discovery 继承。

### Task 2: 图循环、确认和恢复

Files: 新增 nodes/direct_nodes.py；graph.py、nodes/observe_execution.py、execute_tools.py、ask_clarification.py、request_confirmation.py。

Interfaces: direct_decide/direct_validate/direct_observe/direct_finish(state)->Command；build_direct_confirmation(state,call)->dict。

- [x] 上下文成功后分流 Direct 2，沿用 Command 节点边界，不用函数内 while。
- [x] 保存动作→校验→公共执行器→观察→下一轮；停止不调用 goal 验收。
- [x] 绑定具体写操作确认；拒绝记入账本且可继续其他工作，取消保留部分结果。
- [x] 自由文本补充进入本请求决策，候选选择回填原工具协议；WAIT 恢复原动作。
- [x] 隔离真实图覆盖混合请求、中断恢复、拒绝/取消、无进展、协议错误和 WAIT。

### Task 3: API、回复、记忆、预算和可观察性

Files: routers/chat.py、models/schemas.py、nodes/generate_response.py、finalize_memory.py、goal_nodes.py、streaming.py、audit.py；.env.example 只补说明。

- [x] 新请求冻结模式版本并清空 Direct 账本；API 工具列表覆盖全部动作。
- [x] 最终回复使用请求回执；增加 completion_basis/requirements_verified 描述完成依据。
- [x] 记忆、澄清证据和焦点处理全部轮次；预算用已落库回执修复完整账本。
- [x] SSE 增加 Direct 节点标签，审计记录模型停止依据。
- [x] 运行现有相关检查、导入/语法检查和隔离集成验收；留存结果和限制。
- [x] 独立代码审查并修复重要问题，更新计划状态；不提交、不推送。
