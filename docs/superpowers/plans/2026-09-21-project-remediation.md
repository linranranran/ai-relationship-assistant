# AI 人际关系助手整改实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一个月内把当前初版改造成可稳定演示、可追溯、可评测的销售客户记忆 Agent，并保留经过测试的常见亲属称谓工具。

**Architecture:** 认证层创建可信 RequestContext；Neo4j 保存账户、人物、关系和事实版本，SQLite 保存原始消息、会话和检查点，ChromaDB 只作为可重建索引。LangGraph 每轮规划一个动作，读取工具结果后继续、澄清、等待确认或回答。

**Tech Stack:** Python 3.11、FastAPI、Pydantic 2、LangGraph 0.2.61、Neo4j 5、ChromaDB、SQLite、React、pytest、Langfuse。

**Spec:** `docs/PROJECT_DIRECTION.md`

## Global Constraints

- 总预算按 120 小时安排；先完成主演示流程和可复现验证。
- 用户数据默认完全独立；本月不实现团队共享和跨用户实体合并。
- User 与 Person 分开，`self_person_id` 显式关联；`user_id` 不能充当 Person ID。
- owner、本人起点和确认授权只来自服务端可信上下文，不作为模型参数。
- 先支持一种固定 JSON 聊天导入格式；数据标明模拟。
- 事实必须能回到原始证据；向量命中必须回查权威事实状态。
- 常见称谓只覆盖明确测试清单；未知、冲突和超范围时不猜测。
- 单实例演示使用 SQLite；本月不新增 PostgreSQL、微服务、Kubernetes 或多 Agent。
- `pyproject.toml` 和 `uv.lock` 是主要依赖来源；固定版本后同步 README。
- 用户自己实现业务函数；`docs/scaffolds` 仅作教学参考，不被生产代码导入。

## Review Focus

- 客户端伪造 user_id、thread_id、人物 ID 时，服务端必须以认证身份拒绝越权访问。
- 写操作响应丢失、Agent 恢复或用户重复点击时，幂等键必须阻止重复副作用。
- 同名人物、事实矛盾和资料不足必须进入澄清或不确定状态，不能默认选择第一项。
- 旧向量、索引失败和更正后的历史证据不能把已失效事实重新变成当前事实。
- 朋友直连、反向亲属边、未知年龄和多路径冲突不能产生虚假的唯一称谓。

---

## 每日工作循环

每个任务都使用相同节奏：先读对应骨架和现有调用链，写一个失败测试，确认失败原因与目标一致，实现最小行为，跑目标测试，再跑已经完成任务的回归测试。每个提交只覆盖一个可独立说明的结果。

建议分支名：`codex/customer-memory-agent`。由于当前 `docs/DEVELOPMENT_PLAN.md` 和 `docs/PROGRESS.md` 已有未提交修改，建立分支或提交前先确认这些修改归属，不能混入整改提交。

### Task 0：修复开发环境并建立无外部服务的测试入口

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_import_smoke.py`
- Modify: `README.md`

**Interfaces:**
- Produces: 可以导入 backend 的本地测试环境；Neo4j、Chroma、LLM 均可由测试替身隔离。

- [ ] 在 `pyproject.toml` 增加开发依赖组：pytest、pytest-asyncio；决定是否保留 requirements.txt。若保留，明确它由锁文件生成，避免 Chroma 0.5.23/0.6.3 分叉。
- [ ] 删除业务模块 `__main__` 中具有删除数据等副作用的手工测试入口，迁移为独立测试。
- [ ] 写导入冒烟测试：

```python
def test_importing_app_does_not_open_external_clients(monkeypatch):
    opened = []
    monkeypatch.setattr("backend.services.llm.get_llm_client", lambda: opened.append("llm"))
    import backend.main  # noqa: F401
    assert opened == []
```

- [ ] 使用当前项目虚拟环境执行 `uv sync` 和 `uv run pytest tests/test_import_smoke.py -q`。当前 `.venv` 启动器已损坏，应先重建环境；不要假装旧环境验证通过。
- [ ] 记录真实 Python、依赖和测试命令，提交 `chore: establish reproducible test environment`。

### Task 1：建立可信身份与本人 Person 映射

**Files:**
- Create: `backend/models/domain.py`
- Create: `backend/services/access.py`
- Modify: `backend/auth.py`
- Modify: `backend/routers/auth.py`
- Modify: `backend/models/schemas.py`
- Test: `tests/auth/test_request_context.py`
- Test: `tests/auth/test_registration.py`

**Interfaces:**
- Produces: `RequestContext(user_id, self_person_id, request_id)`；`resolve_request_context(...)`；注册后唯一的 `self_person_id`。
- Reference: `docs/scaffolds/identity_access.py`

- [ ] 先写失败测试，证明 User ID 与 Person ID 不相同，注册重试只生成一个本人档案：

```python
def test_registration_creates_one_self_person(user_repository):
    first = user_repository.register(phone="13800000000", password="secret1")
    second = user_repository.retry_same_registration(first.operation_id)
    assert first.user_id != first.self_person_id
    assert second.self_person_id == first.self_person_id
    assert user_repository.count_self_people(first.user_id) == 1
```

- [ ] 为 User.user_id、phone_number、Person.id 建唯一约束；注册事务内创建 User、本人 Person 和映射。旧账号缺映射时返回 `SELF_PERSON_BINDING_REQUIRED`，不按姓名自动绑定。
- [ ] ChatRequest 删除 `user_id` 与 `user_name`，只保留 `message`、`thread_id`、`request_id`。
- [ ] `get_current_user` 返回账户身份；业务路由调用 access service 解析本人。缺 token、过期 token、账户不存在和本人待绑定使用不同内部错误，其中资源越权不泄露存在性。
- [ ] 执行目标测试并提交 `feat: add trusted user and self-person context`。

### Task 2：把 owner 约束下沉到所有数据库操作

**Files:**
- Modify: `backend/db/neo4j_client.py`
- Modify: `backend/db/chroma_client.py`
- Modify: `backend/tools/person.py`
- Modify: `backend/tools/relation.py`
- Modify: `backend/tools/query.py`
- Test: `tests/db/test_owner_isolation.py`
- Test: `tests/tools/test_person_relation_crud.py`

**Interfaces:**
- Consumes: Task 1 RequestContext。
- Produces: 所有人物和关系仓储接口显式接收 `owner_id`；合法 CRUD 契约一致。

- [ ] 先建立双账号测试数据，逐项尝试 A 读取、更新、删除 B 的人物，创建 A→B 的关系，查询经过 B 的路径。每项均断言无数据返回、无计数变化。
- [ ] 将接口统一成关键字参数，例如：

```python
def update_person(driver, *, owner_id: str, person_id: str, changes: dict) -> dict: ...
def delete_person(driver, *, owner_id: str, person_id: str) -> bool: ...
def get_relation(driver, *, owner_id: str, relation_id: str) -> dict | None: ...
```

- [ ] 修改 Cypher，使资源、关系两端、中间节点和介绍人均在同一查询中匹配 owner；更新/删除检查 affected count。同步修复所有旧两参调用及新增人物补偿路径。
- [ ] 修复关系筛选分支漏传 owner、模糊查询只取 single、零结果返回 None、关系更新零匹配仍成功等已确认问题。姓名搜索统一返回候选列表。
- [ ] Chroma 文档 ID 使用 owner 与 person/fact 的稳定组合；删除和搜索包含 owner。向量命中后回查 Neo4j 权威归属。
- [ ] 执行两个测试文件及 Task 1 回归，提交 `fix: enforce ownership at data boundaries`。

### Task 3：统一工具契约、参数校验与错误结构

**Files:**
- Create: `backend/tools/contracts.py`
- Create: `backend/tools/args.py`
- Modify: `backend/tools/definitions.py`
- Modify: `backend/tools/registry.py`
- Modify: `backend/agent/nodes/execute_tools.py`
- Test: `tests/tools/test_contracts.py`

**Interfaces:**
- Produces: `ToolSpec` 唯一来源；`ToolResult(ok, code, retryable, data, call_id)`；模型参数不含可信上下文。
- Reference: `docs/scaffolds/tool_contracts.py`

- [ ] 写测试断言导出 schema 与运行 registry 的工具名集合完全相同，消除 `list_person_relations` / `list_relations` 漂移。
- [ ] 为每个工具建立 Pydantic 参数模型，配置 `extra="forbid"`；限制字符串长度、limit、max_depth 和枚举。明确拒绝 `owner_id`、`confirmed`、`self_person_id`。
- [ ] 以 ToolSpec 同时生成模型可见 schema 和运行 registry。亲属工具只接收 target_person_id；本人起点由 RequestContext 注入。
- [ ] 统一错误码，不再用中文文案判断重试。未知异常记录 request_id/call_id，对外返回稳定信息。
- [ ] 测试 `{}`、null、未知字段、字符串布尔、未知工具、越界深度，断言 handler 未执行。提交 `refactor: unify tool schemas and execution contracts`。

### Task 4：增加归属于用户的服务端会话

**Files:**
- Create: `backend/conversations/models.py`
- Create: `backend/conversations/repository.py`
- Modify: `backend/routers/chat.py`
- Modify: `backend/agent/state.py`
- Modify: `backend/agent/graph.py`
- Modify: `frontend/src/api/chat.js`
- Modify: `frontend/src/pages/Chat.jsx`
- Test: `tests/conversations/test_persistence.py`

**Interfaces:**
- Produces: `thread_id` 属于一个 owner；历史、实体选择和待确认状态由服务端保存。

- [ ] 在 SQLite 中定义 conversation、message、processed_request 表。保存 user/assistant 消息、创建时间、request_id；`UNIQUE(owner_id, request_id)` 支持请求重放。
- [ ] 写刷新与跨用户测试：

```python
def test_other_user_cannot_load_thread(client, alice, bob, alice_thread):
    response = client.get(f"/api/conversations/{alice_thread}", headers=bob.headers)
    assert response.status_code in {403, 404}
```

- [ ] 路由使用认证上下文，不再接收客户端 user_id/history。现阶段可先用 SQLite 保存消息，再验证当前 LangGraph 版本的 SqliteSaver；不要同时引入两套未验证的历史来源。
- [ ] 前端登录后调用 `/auth/me`；API baseURL 使用环境变量或同源 `/api`。同一会话串行发送，消息绑定 request_id/status，失败允许重试。
- [ ] 测试刷新、服务重启、请求重复与账号切换。提交 `feat: persist owner-scoped conversations`。

### Task 5：导入一种聊天格式并保存原始证据

**Files:**
- Create: `backend/memory/models.py`
- Create: `backend/memory/import_service.py`
- Create: `backend/routers/imports.py`
- Create: `frontend/src/pages/ImportChat.jsx`
- Test: `tests/memory/test_import.py`
- Test data: `tests/fixtures/demo_chat.json`

**Interfaces:**
- Consumes: RequestContext、明确的 customer person_id。
- Produces: 稳定的 SourceMessage；批次重试不重复插入。
- Reference: `docs/scaffolds/memory_pipeline.py`

- [ ] 固定 JSON 契约：source_id、external_message_id、speaker_external_id、sent_at、text。界面要求用户把说话人映射到“我/客户”，并标注为模拟数据。
- [ ] 编写重复导入与同文本不同 ID 测试。断言完整批次重放数量不增加，而两条相同文字、不同消息 ID 都被保存。
- [ ] 限制批次大小、单条与总文本长度；时间必须带时区。原文作为不可信数据保存，不能进入系统/工具控制区。
- [ ] 事务使用 owner+source_id+external_message_id 唯一键；同键不同内容返回冲突。提交 `feat: import traceable demo chat messages`。

### Task 6：实现有证据、可更正的事实记忆

**Files:**
- Create: `backend/memory/fact_service.py`
- Create: `backend/memory/retrieval.py`
- Modify: `backend/db/chroma_client.py`
- Create: `backend/jobs/reindex_facts.py`
- Test: `tests/memory/test_fact_lifecycle.py`
- Test: `tests/memory/test_reindex.py`

**Interfaces:**
- Produces: MemoryFact 的 active/superseded/pending_confirmation 状态；可重放的索引任务。

- [ ] 先测试“喜欢钓鱼”的证据建立、“其实是李总”的明确更正，以及“最近没空钓鱼”不构成否定偏好。
- [ ] 候选事实中的每个 evidence_message_id 都回查 owner 和 person；证据不存在时拒绝落库。
- [ ] 同一图事务写事实版本、失效旧版本并设置 index_status=pending。operation_id 唯一，重复请求返回原版本。
- [ ] Chroma 使用 upsert 和显式距离 metric；索引任务携带事实版本，旧任务不能覆盖新版本。查询命中后回查 active 状态。
- [ ] 模拟 embedding/Chroma 故障，断言事实仍可结构化查询、任务可重放、旧向量不会复活失效事实。提交 `feat: add evidence-backed versioned memory`。

### Task 7：生成带引用的客户概况与沟通建议

**Files:**
- Create: `backend/memory/brief_service.py`
- Create: `backend/tools/customer_memory.py`
- Modify: `backend/tools/contracts.py`
- Modify: `frontend/src/pages/Chat.jsx`
- Test: `tests/memory/test_communication_brief.py`

**Interfaces:**
- Produces: `CommunicationBrief(facts, advice, missing_information)`；每条事实和建议理由引用合法证据。

- [ ] 建立 10 个模拟场景的期望断言：目标人物、必须引用的事实、禁止出现的失效事实、资料不足项。
- [ ] 先检索 owner+person 范围内的 active facts，再把有限证据交给模型；输出使用结构化模型校验。
- [ ] 校验引用 ID 确实在输入证据中，引用文字为原文片段或明确标注摘要。事实和建议分栏。
- [ ] 前端引用可展开查看原文和时间；服务端再次验证引用属于当前 owner。提交 `feat: generate evidence-grounded communication briefs`。

### Task 8：改造为有界工具反馈循环与服务端确认

**Files:**
- Create: `backend/agent/contracts.py`
- Create: `backend/agent/nodes/decide_next.py`
- Create: `backend/agent/nodes/execute_one.py`
- Create: `backend/services/confirmation.py`
- Modify: `backend/agent/graph.py`
- Modify: `backend/routers/chat.py`
- Test: `tests/agent/test_tool_feedback_loop.py`
- Test: `tests/agent/test_confirmation.py`

**Interfaces:**
- Consumes: Tasks 3–7 的工具和会话。
- Produces: 每轮一个 ToolCall；Observation 回到规划；PendingAction 一次性消费。
- Reference: `docs/scaffolds/agent_loop.py`

- [ ] 先用假 planner/tool 测试随机人物 ID 被下一步使用、同名时零写入、最多六步终止。
- [ ] Graph 改为 `decide -> execute_one -> decide`，或转入 clarify/final。每个决定使用 Pydantic 校验；观察包含 tool、call_id、结果和错误码。
- [ ] 删除/覆盖等动作创建 PendingAction，保存 owner、thread、tool、规范化参数摘要、operation_id、过期时间。模型参数中的 confirmed 不具有授权效果。
- [ ] 确认接口只执行已保存参数；跨用户、过期、重复确认拒绝或返回同一幂等结果。写操作响应丢失后再次确认不重复执行。
- [ ] 失败时区分部分完成、可重试读取与不可重试写入。提交 `feat: add bounded result-driven agent loop`。

### Task 9：重做常见亲属称谓的确定性核心

**Files:**
- Modify: `backend/db/neo4j_client.py`
- Rewrite: `backend/tools/kinship.py`
- Modify: `backend/tools/contracts.py`
- Test: `tests/kinship/test_candidate_paths.py`
- Test: `tests/kinship/test_common_titles.py`

**Interfaces:**
- Produces: resolved/ambiguous/unsupported/no_path/conflict/incomplete 六种结果；证据路径使用稳定 ID。
- Reference: `docs/scaffolds/kinship_reasoning.py`

- [ ] 写 18 类测试：指定堂亲链四种性别年龄组合；缺年份；同年；未知性别；朋友直连并存；反向父母/子女/兄弟姐妹；母系表亲；异主节点/边；多路径一致/冲突；深度、环、截断和非法排行。
- [ ] 数据库先限定基础亲属边和所有 owner，再枚举深度≤3、候选≤64 的简单路径。不得对混合关系图先取 shortestPath。
- [ ] 保留存储端点和遍历端点；反向父亲得到“子女”，再用新的目标人物性别决定儿子/女儿。堂表亲长幼比较 A 与 D，不能使用父辈排行代替。
- [ ] 规则外返回 unsupported；数据不足返回候选和 missing_fields；关闭 LLM 确定性兜底及自动排行前缀。
- [ ] 执行全部亲属与 owner 回归测试，提交 `fix: make common kinship reasoning deterministic`。

### Task 10：资源生命周期、评测、部署和面试材料

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/services/llm.py`
- Modify: `backend/services/embedding.py`
- Modify: `docker-compose.yml`
- Create: `Dockerfile`
- Create: `evals/dataset.jsonl`
- Create: `evals/run_eval.py`
- Create: `docs/EVALUATION_REPORT.md`
- Create: `docs/DEMO_SCRIPT.md`
- Modify: `README.md`

**Interfaces:**
- Produces: 可部署单实例、可重放评测、五分钟演示和诚实的指标报告。

- [ ] 用 FastAPI lifespan 管理 Neo4j、LLM、embedding 与 SQLite 资源。同步链路先使用同步路由/线程池；假慢模型请求期间健康接口仍能响应。
- [ ] 构建 60～100 个测试/评测案例，其中一组从未用于提示词调整。数据集记录 query_time，只让系统看到该时间之前的消息。
- [ ] 记录任务完成率、事实/引用错误分类、P50/P95、失败率、模型调用和 Token。指标包含样本量、模型、提示词/代码版本和运行环境。
- [ ] Docker Compose 加应用、健康检查和数据卷；配置 CORS、密钥和 Neo4j 密码。编写 SQLite、Neo4j、Chroma 的备份与恢复说明。
- [ ] README 区分已实现/规划，注明模拟数据；演示脚本覆盖导入、建议、更正、跨账号隔离和堂亲案例。
- [ ] 运行完整测试、前端 lint/build、固定数据评测与干净环境启动。保存实际输出后提交 `docs: publish reproducible demo and evaluation`。

## 最终验收命令

环境修复后，在仓库根目录依次运行：

```powershell
uv sync
uv run pytest -q
pnpm --dir frontend lint
pnpm --dir frontend build
uv run python evals/run_eval.py --dataset evals/dataset.jsonl --output evals/results/latest.json
docker compose config
docker compose up --build
```

最后用账号 A、C 现场重放 `docs/DEMO_SCRIPT.md`。任何一步失败都记录真实结果和限制，不改写成已完成。

## 计划自查结果

- 项目方向中的身份、记忆、工具反馈、确认、常见称谓、评测和部署均有对应任务。
- 所有跨任务接口在前序任务或教学骨架中定义；owner 和确认权不交给模型。
- 五类高风险输入均落入具体测试任务。
- 本计划没有要求实现企业微信、共享客户库、全谱亲属、多 Agent 或新增 PostgreSQL。

## 执行方式

本轮已明确由项目作者自己实现。建议一次只做一个 Task：写测试、实现、验证、提交，然后把 diff 与测试输出交给 AI 审阅。第一项从 Task 0 开始；未修复环境前，不把静态审阅结论描述成运行验证结果。
