# RAG Version Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [x]`) syntax for tracking. User explicitly requested direct implementation; no additional approval handoff.

**Goal:** 支持不可变树状版本、默认选中新版及手动激活历史版本，并保持可靠消费和查询快照一致性。

**Architecture:** schema2 用 document_revision 排序操作，用 version_id 标识内容；版本归档与活动版本发布分开。版本目录与活动选择追加到现有 SQLite，复用 generation、完整知识库 BM25 和事务快照指针。

**Tech Stack:** Python/sqlite3、现有 LlamaIndex/Chroma/Redis、RabbitMQ/pika。

**Spec:** `rag/docs/树状版本与活动版本设计.md`。

## Global Constraints

- 沿用当前独立副本和 develop-rag；保留用户未提交的 store.py 学习注释，不把注释改动混入功能提交。
- 保留 schema1；相同逻辑文档禁止猜测协议混合迁移，已知重复 receipt 仍可确认。
- 无新增外部依赖；单机单写入者；持久等待目标最多3600秒，不消耗普通失败次数。
- 索引失败不替换旧快照；默认只检索活动版本，历史对比查询不在本次范围。

## Review Focus

- 创建13后手动选择12，慢13及乱序旧消息不能覆盖12。
- 相同version_id不同对象/父节点、同操作修订不同目标必须隔离。
- 激活早于创建时不饿死创建任务；等待与普通重试、崩溃恢复的计数正确。
- 新增数据库表兼容已有 schema1 任务/快照，用户学习注释不丢失。
- 复用 generation 的完整性/模型匹配与 BM25 发布失败保持原活动版本。

### Task 1: Event contract and version catalog

**Files:** events.py, errors.py, new version_catalog.py, store.py minimal dispatch; tests/test_version_tree_catalog.py.

**Interfaces:** DocumentEvent.revision/is_versioned/version_id; Inbox.versions(scope,document_id), version_source(event), version_should_publish(job), version_ready(source,generation); publish dispatch handles schema2; fail/retry/dependency waiting dispatch.

- [x] Write and run RED tests: opaque IDs, tree10→11→12 and10→13, activation revision5 target12, conflicts/late messages/parent order/deletion/legacy compatibility.
- [x] Implement strict schema2 validation, additive schema, immutable catalog, persistent activation selection and wait scheduling.
- [x] GREEN state tests and existing schema1 suite; commit scoped implementation.

### Task 2: Version indexing and atomic activation

**Files:** worker.py, backend.py; tests/test_version_tree_indexing.py.

**Interfaces:** stage(source_event,inbox) uses immutable version identity; reuses verified complete manifest. Worker archives any created version, conditionally publishes current desired selection; activation resolves original version source.

- [x] RED tests: branch13 default→activate12→13→12; no parser/Embedding on ready activation; delayed13 and BM25 failure preserve active; missing creation waits without exhausting attempts.
- [x] Implement node metadata and verified generation reuse, selection-aware publish, completed archive semantics.
- [x] GREEN core and real Llama adapter tests; verify query filters still active-only.

### Task 3: API, producer simulation and acceptance

**Files:** rest_api.py, new scripts/publish_version_event.py, tests/test_version_tree_api.py, tests/test_version_tree_cli.py, test_rabbitmq_integration.py, docs.

**Interfaces:** GET /documents/{document_id}/versions?tenant_id&knowledge_base_id -> latest/desired/active/versions plus missing parent flag. CLI create/activate/delete/replay with --revision, --version-id, --parent-version-id, --version-label and --prepare-only.

- [x] RED behavior tests for readonly tree state, invalid/conflicting fields and immutable local producer paths.
- [x] Implement API/CLI, documented10/11/12/13 walkthrough, preserve legacy entry points.
- [ ] Run full suite including real RabbitMQ tree→manual activation, compile/diff checks, fresh independent whole-change review, regression fixes, scoped commit and push develop-rag.
