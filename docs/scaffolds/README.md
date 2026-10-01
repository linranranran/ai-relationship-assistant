# 教学骨架使用说明

这里的文件是为你自己实现而准备的设计骨架，不属于运行中的 `backend`，也不会被应用自动导入。每个骨架先固定边界、数据类型和验收思路，业务函数保留 `NotImplementedError`。

建议按以下顺序学习和迁移：

1. [identity_access.py](identity_access.py)：账户、本人 Person、可信请求上下文与 owner 校验。
2. [tool_contracts.py](tool_contracts.py)：工具定义、参数校验、统一结果与注册一致性。
3. [memory_pipeline.py](memory_pipeline.py)：聊天导入、证据、事实版本、更正与检索。
4. [agent_loop.py](agent_loop.py)：一次一个动作的工具反馈循环、确认、上限与幂等。
5. [kinship_reasoning.py](kinship_reasoning.py)：有界亲属路径、方向归一化和不确定结果。

迁移方法：

- 先为一个骨架函数写失败测试，再把对应类型和函数迁到骨架头部建议的 `backend` 文件。
- 只实现当前测试需要的最小行为；不要一次复制整份骨架到生产目录。
- 业务代码不应引用 `docs.scaffolds`。骨架只是课堂讲义，正式类型由 `backend` 自己定义。
- 骨架中的 `driver: object`、Repository Protocol 等是边界占位。落地时换成项目实际依赖，但保留关键字参数和归属约束。
- 每完成一个任务，更新评测数据和实际结果。不能用“代码写完”代替验收。

对应的源码问题见 [代码审阅](../CODE_REVIEW_2026-09-21.md)，任务顺序见 [实施计划](../superpowers/plans/2026-09-21-project-remediation.md)。
整体目录与模块关系见 [目标代码框架](../REFACTOR_FRAMEWORK.md)。
