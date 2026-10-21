# 知客文档导航

当前产品名称为**知客-销售沟通助手**。仓库与 Python 包沿用早期路径，避免影响已有环境和代码引用。以下当前指南是展示与运行入口；日期化设计、评测和个人笔记保留原名称与原始结论。

## 从这里开始

| 目的 | 文档 |
|---|---|
| 了解产品、范围与已知限制 | [项目首页](../README.md) |
| 配置数据库、模型并本地运行 | [本地运行指南](GETTING_STARTED.md) |
| 理解两条业务链路和公共执行边界 | [架构与代码阅读](ARCHITECTURE.md) |
| 查看真实实验规模与公开样例 | [评测证据包](evaluations/showcase/README.md) |
| 查看完整评分口径和未验收项 | [评测结论](评测文档/评测结果与验收结论.md) |
| 面试演示与项目介绍 | [演示路线](DEMO.md) |

## 专题阅读

- [身份与数据边界](AGENT_IDENTITY_CONTRACT.md)：User、本人Person与owner_id。
- [中断与恢复](AGENT_INTERRUPT_RESUME_GUIDE.md)：确认/澄清及resume语义。
- [依赖工具执行](DEPENDENT_TOOL_EXECUTOR_GUIDE.md)：引用解析、失败处理与重规划。
- [三层记忆代码梳理](总结文档/三层记忆代码梳理.md)：详细学习记录，结合当前源码阅读。
- [销售聊天证据设计](superpowers/specs/2026-10-13-sales-chat-evidence-mvp-design.md)。
- [评测脚本说明](../scripts/evaluation/README.md)：离线复核与付费模型运行分开。

## 历史与学习材料

`superpowers/specs/`、`superpowers/plans/`、`总结文档/`、`scaffolds/` 和旧版方向/整改文档保留学习过程；其中的 TODO、计划、旧技术选择不代表当前实现状态。

- [早期项目方向](PROJECT_DIRECTION.md)、[需求探索](REQUIREMENTS.md)、[开发计划](DEVELOPMENT_PLAN.md)。
- [旧版代码审阅](CODE_REVIEW_2026-09-21.md)、[学习骨架](scaffolds/README.md)。
- [历史 MySQL DDL](archive/legacy-mysql-schema.sql)：仅供理解迁移过程，当前项目使用PostgreSQL，不运行此文件。

完整模型响应、运行期身份、检查点备份等保留本地；GitHub提供经筛选的合成样例。不要用历史“通过”覆盖后续更严格的复评分结论。
