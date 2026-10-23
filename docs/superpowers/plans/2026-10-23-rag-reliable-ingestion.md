# RAG Reliable Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消费知识库文件变更事件，可靠落盘、可重试，并让完整的新文档版本原子进入混合检索。

**Architecture:** RabbitMQ → durable Inbox → 单写入 Worker → versioned Chroma/Redis + immutable BM25 snapshot → SQL snapshot pointer。ACK 在 Inbox 落盘后发送，任务状态单独表示完成。

**Tech Stack:** Python 3.11+、sqlite3、pika、LlamaIndex、Chroma Server、Redis、oss2。

**Spec:** `rag/docs/可靠消费设计.md`。用户要求直接实现并在本地 RabbitMQ 模拟，免去设计与计划的逐步审批。

## Global Constraints

- 只提交 rag/ 与本计划，不改 Agent 原有行为；真实 .env、运行数据、PDF 不提交。
- 至少一次 + 幂等效果，不宣称跨系统 exactly-once。
- SQLite 与快照目录必须持久化且位于同一机器，只有一个索引 Worker。
- 发布前验证所有节点、向量和 BM25；失败保留旧快照。

## Review Focus

- 相同事件 ID 被不同内容复用、同版本来自不同事件 ID 的冲突。
- 新版本到达时旧版本还在构建；删除后旧更新不能复活。
- 向量与 BM25 的可见版本一致，过滤发生在向量 TopK 之前。
- 永久错误、重试耗尽、进程崩溃、死信确认后掉电。
- local 路径逃逸、文件被覆盖、模型配置变化后的缓存失效。

### Task 1: Durable Inbox and event contract

**Files:** `rag/rag_api_service/reliable_ingestion/{events,store,errors}.py`, `rag/tests/test_reliable_ingestion.py`。

**Interfaces:** `DocumentEvent.parse(body)`, `Inbox.accept(body)`, `Inbox.claim()`, `Inbox.fail(job,error)`, `Inbox.publish(job,snapshot_id,generation)`。

- [ ] 写入并运行失败测试：重复、冲突、版本乱序、删除、重试、中断恢复。
- [ ] 实现严格事件校验、事务 Inbox、任务重试、版本 CAS、死信 Outbox。
- [ ] 运行 `python -m unittest discover -s rag/tests -v`，预期全部通过。
- [ ] 提交可独立测试的任务状态机。

### Task 2: Versioned indexing and query snapshots

**Files:** `reliable_ingestion/{worker,objects,backend,query}.py`；`rest_api.py`。

**Interfaces:** `Worker.run_once()`, `backend.stage(event,inbox) -> generation`, `backend.build_snapshot(event,generation,active) -> snapshot_id`。

- [ ] 故障测试先失败：第 21 个 chunk 失败、BM25 失败、发布竞争、Embedding 复用、对象校验。
- [ ] 实现不可变 generation、全量知识库 BM25 快照、单事务发布、查询预过滤。
- [ ] 事件模式路由接入，拒绝绕过版本协议的旧写接口。
- [ ] 运行全部 RAG 测试并提交。

### Task 3: RabbitMQ runtime and local simulation

**Files:** `reliable_ingestion/{broker,admin,__main__}.py`, `rag/scripts/publish_event.py`, `rag/docker-compose.yml`, `.env.example`, `README.md`。

**Interfaces:** `Consumer.handle(channel,method,properties,body)`, `relay_dead_letters(inbox,publisher)`。

- [ ] 先写故障测试：落盘失败不 ACK、持久落盘后 ACK、死信发布失败可恢复。
- [ ] 实现 heartbeat 安全的消费线程、断线重连、进程锁、任务状态 CLI、confirmed publisher。
- [ ] 提供新增/修改/删除/重放消息脚本与部署步骤；运行全部 RAG 测试与源文件检查。
- [ ] 完成独立代码审查、必要修复与提交；尝试推送用户指定仓库并核验结果。
