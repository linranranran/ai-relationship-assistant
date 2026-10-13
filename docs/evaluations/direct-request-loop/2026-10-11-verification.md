# Direct v2 请求级循环：隔离验证记录

日期：2026-10-11。对应 [改造方案](../../superpowers/specs/2026-10-11-direct-request-loop-design.md) 与 [实施计划](../../superpowers/plans/2026-10-11-direct-request-loop-implementation.md)。

## 验证范围

Direct v2 围绕完整请求进行原生工具调用，跳过意图识别、信息提取、goal 调度与逐目标验收。Plan、Discovery、历史 Direct v1 保留原目标流程。此次没有修改实际 .env，没有提交或推送，没有启动或停止后端，也没有新增 tests 下的测试。

使用真实 LangGraph 状态图、Command、interrupt、InMemorySaver 和项目节点。模型返回、预算存储、工具执行租约、人物存储和最终回复流使用内存替身；三层记忆仍运行现有事实提取流程，其存储为空实现。Langfuse/LangSmith 上报关闭。未连接真实 PostgreSQL/Neo4j，未调用付费模型。

## 已执行结果

- 26 组隔离检查通过，脚本位于 git 忽略目录 `.superpowers/sdd/2026-10-11-direct-request-loop-implementation/verify_direct.py`。
- 30 项已有检查通过：上下文相关性、人物记忆查询、指代解析、owner 隔离、关系计划执行、身份参数校验与 LangGraph 兼容。
- `python -m compileall -q backend` 与 `git diff --check` 通过。
- 一次独立只读代码审查，无 Critical/Minor，发现 3 项 Important，均已复现、修复，并增加针对性隔离检查。修复后未请求第二轮审查。

26 组覆盖以下行为：

1. 混合修改/查询按四次实际工具回执完成，写入先确认，记忆/API 保留完整账本。
2. 已结束请求用原 request_id 重试不重复执行。
3. 新请求清空动作账本，闲聊不强制调用工具。
4. 拒绝写入后继续独立查询。
5. 取消确认后保存已执行步骤并记忆收尾。
6. 普通业务失败后继续独立要求。
7. 同名候选选择替换同一步结果与协议组。
8. 自由文本澄清保留原请求，并进入消息证据。
9. WAIT 恢复原 call_id，不重新决策。
10. Schema 错误只有有限修复机会。
11. 无来源人物 ID 在执行前拒绝。
12. 用户拒绝后不能换 call_id 重提相同写入。
13. 后续重复写建议复用已完成结果，避免第二次物理写入。
14. 重复读取受无进展上限限制。
15. 精确 NOT_FOUND 后才进入新增确认。
16. 确认绑定完整业务参数，篡改会被拒绝。
17. Plan、Discovery、旧 Direct 保持版本路由与策略继承。
18. 预算收尾可补回已提交业务回执。
19. 全部策略收到取消信号均能正常结束。
20. 预算核对后的成功回执不会被当前旧失败记录覆盖。
21. 多个不同 NOT_FOUND 视为有效新证据。
22. 重复同一 NOT_FOUND 仍受无进展限制。
23. Plan v1 完整查询图仍按目标验收。
24. Direct v1 完整查询图仍按目标验收。
25. Discovery v1 完整查询图可搜索/描述/调用工具，并按目标验收。
26. 请求图模型预算耗尽时，保留已提交写入并进入最终记忆，无额外解释模型调用。

## 审查问题与修复

| 问题 | 修复 | 验证 |
| --- | --- | --- |
| 执行函数内再次 import 导致取消分支使用未赋值的局部变量 | 使用顶层导入，移除内层重复 import | 四种模式取消信号 |
| 核对成功账本被旧批次 TRANSIENT 覆盖 | 合并核对完毕后清空当前批次数组 | 最终 direct_records 保留 SUCCESS |
| 三个不同人物 NOT_FOUND 被当成无进展 | 负向读取证据纳入稳定指纹；失败写入不计新证据 | 不同查询继续，重复查询达到上限 |

## 限制与后续真实验证

这些结果证明模拟输入下的控制流和边界行为，不证明模型能可靠完成所有真实多意图请求，也不证明 PG/Neo4j 并发、崩溃恢复或生产性能。先前三策略的 9 次真实模型小样本来自目标级旧流程，不能当作本次 Direct v2 的效果数据。

Direct 不创建隐藏需求清单，因此 `completion_basis=MODEL_STOP_WITH_TOOL_RECEIPTS` 与 `requirements_verified=false`：工具回执证明具体动作，不证明用户每个要求都已覆盖。所有六种写入逐次确认是授权边界，增加交互次数；Direct 暴露完整工具 Schema，目录增大可能增加上下文与模型成本。后续应使用完整请求评测漏项率、参数错误率、确认次数、模型调用数、Token 与延迟。

隔离检查还输出了项目既有 `MemoryRole` Checkpoint 反序列化兼容警告：当前可运行，未来严格 msgpack 需明确允许类型或统一序列化格式。本次未改动这部分配置。

Windows 沙箱内已有 asyncio 检查在 socketpair 初始化等待；授权的本地检查运行通过。该限制与模型、业务数据库无关。
