# 项目评测

GitHub读者请先看[公开证据包](../../docs/evaluations/showcase/README.md)。下文复评分命令需要本地完整运行目录，该目录与数据库备份未提交Git；克隆仓库后不能直接复评分全部180条原始记录。

绑定方案是 `docs/评测文档/AI 人际关系助手评测方案.md`。最终阅读入口是该目录的
`评测结果与验收结论.md`。本轮结论为 NOT_ACCEPTED，而非生产可用认证。

## 不付费的复核

从项目根目录使用项目虚拟环境：

```powershell
./.venv/Scripts/python.exe -X utf8 -m scripts.evaluation.rescore docs/评测文档/runs/20261014-141025-retest
./.venv/Scripts/python.exe -X utf8 -m scripts.evaluation.report docs/评测文档/runs/20261014-141025-retest
node --experimental-vm-modules scripts/evaluation/frontend_probe.mjs
```

复评分不覆盖原始 trials、summary 或云端分数。原评分是 `v2.rule-proxy`：
检查状态与关键词，不能证明完整任务/回复语义正确。`v3.audit` 额外检查
关系重复次数、未授权额外属性、Plan整体完成误报与故障是否命中。
人工审核未完成时完整业务完成率为 null，不能当0或100%。

`response-review.csv` 审核人物与记忆回复的一致性、所有要求和误报完成；
`semantic-review.csv` 审核销售事实。每行可能含多条原子事实，审核前先拆分。
完整来源见同 trial_id 的 JSON 的 sales_preparation.sources 与 manifest.cases 初始聊天，
不能只看被截取的 quote。

## 新的真实模型运行会产生费用

```powershell
./.venv/Scripts/python.exe -X utf8 -u -m scripts.evaluation.run --phase pilot --ids P01,S01,M01 --ceiling 80 --concurrency 3
./.venv/Scripts/python.exe -X utf8 -u -m scripts.evaluation.run --phase retest --ceiling 2400 --concurrency 3
```

每个 run_id 冻结配置/数据/源码并重置虚构业务仓储。修复评测替身的缺省字段、
故障注入路径后，应生成新数据版本和 run_id。不能复用旧保留集调提示词后仍称独立验收。
P14本轮没有命中预设故障，不能据此宣称验证了独立要求故障恢复。

模型额度/权限失败会停止接收新试验，已启动失败保留，未启动单列。更换模型须新建run。
业务图库、预算与检查点在质量矩阵中为替身；真实集成的17表PG schema及4个虚构Neo4j owner
另在 integration-20261014 报告，不能将两层混为一谈。

## 云归档

`upload <run_folder>` 仅导入现有结果，不调用模型。本次用户已经明确授权
把虚构案例和项目内部提示词/Schema/节点/模型结果上传当前cloud.langfuse.com项目。
脚本不读取.env正文，不上传凭据。
当前读回检查范围是task_roots_only，验证任务根存在不证明全部generation/轨迹/评分完整性。
云端归档时间不代表原模型运行时长，性能以原始JSON和真实HTTP样本为准。

## 原始记录定位

- `manifest.json`：模型、配置、数据与源码摘要；source-snapshot.zip包含当时未提交源码。
- `trials/<trial_id>.json`：提示词、Schema、模型原始返回、节点状态、工具回执与业务结果。
- `summary.json`：原规则分数，保留作为历史记录。
- `scoring-v3-audit.json`：独立复评分与未命中故障；原评分缺陷明确记录。
- `integration-20261014/*.json`：真实恢复、取消、预算、API和性能；每项都有范围限制。

本轮未自动commit/push。真实企微、生产容量、完整跨库回滚、人工销售试用另外验收。
