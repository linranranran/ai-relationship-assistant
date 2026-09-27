# Three-Layer Agent Memory Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a runnable, heavily commented three-layer memory scaffold that separates LangGraph execution checkpoints, bounded short-term conversation context, and evidence-backed long-term facts.

**Architecture:** PostgreSQL Checkpointer remains the execution-state store. Two new graph nodes call a memory runtime facade: `prepare_context` builds a bounded `ContextBundle` before intent classification, and `finalize_memory` records the completed turn and exposes summarization/fact-extraction extension points after response generation. Protocols and no-op adapters keep the existing Agent operational while the project author implements PostgreSQL repositories, summarization, tokenization, and retrieval.

**Tech Stack:** Python 3.12, LangGraph 1.2.12, FastAPI, Pydantic/dataclasses, psycopg 3, PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-09-27-three-layer-memory-design.md`

## Global Constraints

- Do not add new test files; the user will implement the learning exercises and tests later.
- Run the existing `unittest` suite, Python compile checks, and a minimal import/runtime smoke check after integration.
- Do not send the entire checkpoint or all historical messages to the LLM.
- Every repository interface receives trusted `owner_id` and `conversation_id`; browser-controlled identity is forbidden.
- Unimplemented persistence/retrieval defaults to explicit no-op adapters and must not fabricate memories.
- A memory-layer degradation must not re-execute already successful Tools.
- Keep `history` temporarily for compatibility, but new code uses `recent_messages`, `conversation_summary`, and `model_context`.

## Review Focus

- Empty/new conversation: `prepare_context` must still include the current user input and must not fail on absent summary/messages.
- Oversized single message: context assembly must report overflow/degradation without silently removing the current input.
- Foreign-owner data: repository contracts must require owner scoping for messages, summaries, and facts.
- Memory backend unavailable: current chat continues with current input while `context_stats.memory_degraded` records the fallback.
- Duplicate request retry: `finalize_memory` contracts use `(owner_id, request_id, role)` idempotency and do not duplicate a turn.

---

### Task 1: Memory Configuration and Domain Contracts

**Files:**
- Create: `backend/agent/memory/__init__.py`
- Create: `backend/agent/memory/models.py`
- Modify: `backend/config.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: existing environment loading helpers `_get` and `_get_int`.
- Produces: `MemoryMessage`, `ConversationSummary`, `MemoryFact`, `ContextStats`, `ContextBundle`, `MemoryWriteCandidate`, and `MemorySettings`.

- [ ] **Step 1: Add exact memory configuration values**

Add integer settings `AGENT_MAX_INPUT_TOKENS=12000`, `AGENT_RESERVED_OUTPUT_TOKENS=2000`, `AGENT_CONTEXT_SAFETY_TOKENS=1000`, `AGENT_RECENT_MESSAGE_LIMIT=12`, `AGENT_MEMORY_RETRIEVAL_LIMIT=5`; add float setting `AGENT_SUMMARY_TRIGGER_RATIO=0.70` via a new `_get_float(key: str, default: float) -> float` helper.

- [ ] **Step 2: Define immutable domain dataclasses and enums**

Define:

```python
class MemoryRole(StrEnum): USER = "user"; ASSISTANT = "assistant"; TOOL = "tool"
class MemoryFactStatus(StrEnum): ACTIVE = "active"; PENDING_CONFIRMATION = "pending_confirmation"; SUPERSEDED = "superseded"; REJECTED = "rejected"
@dataclass(frozen=True, slots=True) class MemoryMessage: ...
@dataclass(frozen=True, slots=True) class ConversationSummary: ...
@dataclass(frozen=True, slots=True) class MemoryFact: ...
@dataclass(frozen=True, slots=True) class MemoryWriteCandidate: ...
@dataclass(frozen=True, slots=True) class ContextStats: ...
@dataclass(frozen=True, slots=True) class ContextBundle: ...
@dataclass(frozen=True, slots=True) class MemorySettings: ...
```

Use JSON-compatible field types because values enter LangGraph checkpoints. Comments must distinguish confirmed facts from model candidates and stored messages from model context.

- [ ] **Step 3: Export only public contracts from `memory/__init__.py`**

Keep repository implementations out of the package export surface.

- [ ] **Step 4: Verify syntax and imports**

Run: `python -m compileall -q backend`

Expected: exit code 0.

### Task 2: Storage and Tokenization Ports with Safe Defaults

**Files:**
- Create: `backend/agent/memory/ports.py`
- Create: `backend/agent/memory/runtime.py`

**Interfaces:**
- Consumes: Task 1 domain contracts.
- Produces: `MessageStore`, `SummaryStore`, `LongTermMemoryStore`, `TokenCounter`, `MemoryRuntime`, `NoOpMessageStore`, `NoOpSummaryStore`, `NoOpLongTermMemoryStore`, `ConservativeTokenCounter`, `get_memory_runtime()`, and `configure_memory_runtime(runtime: MemoryRuntime) -> None`.

- [ ] **Step 1: Define Protocol interfaces**

Use keyword-only methods with mandatory `owner_id` and `conversation_id`. Required signatures:

```python
MessageStore.load_recent(...) -> tuple[MemoryMessage, ...]
MessageStore.save_turn(...) -> None
MessageStore.delete_conversation(...) -> None
SummaryStore.load(...) -> ConversationSummary | None
SummaryStore.save_if_cursor_matches(...) -> bool
SummaryStore.delete(...) -> None
LongTermMemoryStore.search(...) -> tuple[MemoryFact, ...]
LongTermMemoryStore.save_candidates(...) -> tuple[MemoryWriteCandidate, ...]
TokenCounter.count_messages(messages: list[dict]) -> int
```

- [ ] **Step 2: Add explicit no-op adapters**

No-op reads return empty values. No-op writes return without pretending data was persisted. Add comments explaining that production startup will later replace them with PostgreSQL adapters.

Deletion methods are also no-op by default. Production deletion must first verify conversation ownership, call the supported Checkpointer `delete_thread(thread_id)` API, and then apply the product's retention policy to messages, summaries, and facts.

- [ ] **Step 3: Add conservative token estimation**

`ConservativeTokenCounter` must never return zero for non-empty content. Mark model-specific tokenization as the project author's TODO; keep the estimate deterministic for debugging.

- [ ] **Step 4: Add a thread-safe runtime registry**

`get_memory_runtime()` returns one process-level runtime using safe defaults. `configure_memory_runtime()` replaces it during future application startup wiring. Do not create database connections at import time.

- [ ] **Step 5: Verify the safe default runtime**

Run a one-line Python import that calls all no-op read methods and token counting.

Expected: empty memory results and a positive count for a non-empty Chinese message.

### Task 3: Bounded Context Builder

**Files:**
- Create: `backend/agent/memory/token_budget.py`
- Create: `backend/agent/memory/context_builder.py`

**Interfaces:**
- Consumes: Task 1 models and Task 2 ports/runtime.
- Produces: `calculate_input_budget(settings: MemorySettings) -> int`, `compact_tool_results(results: list[dict]) -> list[dict]`, and `build_context_bundle(...) -> ContextBundle`.

- [ ] **Step 1: Implement configuration validation**

Reject non-positive maximum input, negative reserves, a reserve plus safety margin that consumes the full budget, negative retrieval limits, and summary ratios outside `(0, 1)`.

- [ ] **Step 2: Scaffold Tool-result compaction**

Return only Tool name, status, stable identifiers, error code, and a bounded data summary. Add TODO comments for per-Tool compactors. Never copy stack traces or unrestricted result arrays.

- [ ] **Step 3: Assemble context by fixed priority**

`build_context_bundle` receives trusted identity, current input, compact execution state, repository ports, token counter, and settings. Always retain system instructions/current input; then consider execution state, summary, long-term facts, and newest-to-oldest recent messages. Add detailed TODO markers where the project author will implement exact trimming and relevance scoring.

- [ ] **Step 4: Record observable decisions**

Populate `ContextStats` with budget, estimated input tokens, selected message/fact counts, dropped counts, `summary_required`, `memory_degraded`, and degradation reasons.

- [ ] **Step 5: Manually inspect empty, normal, and oversized inputs**

Use an inline Python command; do not create a test file. Confirm current input remains present and overflow is visible in stats.

### Task 4: PostgreSQL Memory Schema and Repository Skeleton

**Files:**
- Create: `backend/db/migrations/002_agent_memory.sql`
- Create: `backend/db/conversation_memory_store.py`
- Modify: `backend/db/init_postgres.py`

**Interfaces:**
- Consumes: Task 1 domain contracts, Task 2 ports, and existing `get_postgres_connection()`.
- Produces: table contracts for `conversation_message`, `conversation_summary`, `memory_fact`; `PostgresConversationMemoryStore` method skeleton matching all three storage protocols.

- [ ] **Step 1: Create idempotent PostgreSQL DDL**

Add owner-scoped unique constraints, foreign-key-free evidence IDs for the first scaffold, status checks, JSONB fields, cursor/version columns, timestamps, and lookup indexes specified by the design. Include SQL comments explaining the distinction from LangGraph checkpoint tables.

- [ ] **Step 2: Add one repository class with separated method groups**

Create exact method signatures required by the ports. Each method includes SQL-shape pseudocode and owner/idempotency comments. Methods not yet implemented must raise a domain-specific `MemoryPersistenceNotConfigured` exception and are not wired into the default runtime.

- [ ] **Step 3: Register migration execution order**

Make `init_postgres.py` discover or explicitly execute `001_postgres_idempotency.sql` followed by `002_agent_memory.sql`, each in its own transaction with clear logging.

- [ ] **Step 4: Verify migration syntax against configured PostgreSQL**

Run the migration initializer once and query only table names/constraints. Do not print credentials or message contents.

Expected: all three tables exist and rerunning the initializer succeeds.

### Task 5: Graph Nodes and State Integration

**Files:**
- Create: `backend/agent/nodes/prepare_context.py`
- Create: `backend/agent/nodes/finalize_memory.py`
- Modify: `backend/agent/state.py`
- Modify: `backend/agent/graph.py`
- Modify: `backend/routers/chat.py`

**Interfaces:**
- Consumes: `build_context_bundle(...)`, `get_memory_runtime()`, and Task 1 JSON-compatible models.
- Produces: `prepare_context(state: AgentState) -> dict`, `finalize_memory(state: AgentState) -> dict`, and the graph path `prepare_context → classify_intent → ... → generate_response → finalize_memory → END`.

- [ ] **Step 1: Extend `AgentState` without deleting compatibility fields**

Add the seven fields from the spec. Type them as JSON-compatible dictionaries/lists because PostgreSQL Checkpointer serializes them. Keep `history` with a deprecation comment.

- [ ] **Step 2: Add server-derived default conversation identity**

Initialize `conversation_id="default"` in the HTTP boundary. Add a TODO to replace it with an owner-validated persisted conversation ID; never accept it directly from an unauthenticated field.

- [ ] **Step 3: Implement `prepare_context` as a safe adapter node**

Build a bundle with the runtime facade, convert dataclasses to checkpoint-safe dictionaries, and catch optional memory-read failures as degradation. Programming/configuration errors must still propagate.

- [ ] **Step 4: Implement `finalize_memory` as an idempotent extension point**

Build user/assistant `MemoryMessage` values from the trusted state and call `save_turn`. If the default no-op adapter is active, update only the compact in-checkpoint recent-message window. Add TODO hooks for summary jobs and fact candidates; do not call the LLM or activate inferred facts.

- [ ] **Step 5: Rewire graph entry and terminal path**

Set `prepare_context` as entry, add its edge to `classify_intent`, register `finalize_memory`, and add the edge from `generate_response`. Update routing comments so no node is incorrectly described as terminal.

- [ ] **Step 6: Stop resetting short-term memory on every request**

Remove `history=[]` from new-turn input. Initialize only first-turn memory fields that cannot come from an existing checkpoint; use `state.get` in nodes so restored threads remain compatible.

### Task 6: Route LLM Calls Through the Prepared Context

**Files:**
- Modify: `backend/agent/nodes/classify_intent.py`
- Modify: `backend/agent/nodes/plan_tasks.py`
- Modify: `backend/agent/nodes/generate_response.py`
- Modify: `backend/services/llm.py`

**Interfaces:**
- Consumes: `state["model_context"]` and `state["context_stats"]` from Task 5.
- Produces: `select_context_for_node(state, node_name) -> list[dict]` or an equivalently named single helper used by all three LLM nodes.

- [ ] **Step 1: Add one task-specific context selector**

The selector preserves current input and summary but lets each node request only the fields it needs. Tool planning may receive compact execution state; intent classification must not receive unrestricted Tool output.

- [ ] **Step 2: Update intent classification**

Stop formatting the deprecated `history` directly. Pass the bounded message list supplied by the selector and keep the current structured JSON parsing behavior.

- [ ] **Step 3: Update planning and response generation**

Inject selected context without duplicating current user input. Keep existing planning validation, confirmation, and Tool sanitization unchanged.

- [ ] **Step 4: Expose token usage without coupling nodes to Langfuse**

Extend the LLM service return boundary only if necessary; otherwise keep `ContextStats` estimates and add a TODO for provider-reported usage reconciliation. Do not log full prompts.

### Task 7: Existing Verification and Learning Handoff

**Files:**
- Modify only if comments are inaccurate: `docs/AGENT_INTERRUPT_RESUME_GUIDE.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: a runnable scaffold and a concise list of implementation TODOs for the project author.

- [ ] **Step 1: Run Python compilation**

Run: `python -m compileall -q backend`

Expected: exit code 0.

- [ ] **Step 2: Run the existing backend tests**

Run: `python -m unittest discover -s tests -v`

Expected: all existing tests pass; no new test files are added.

- [ ] **Step 3: Run a memory scaffold smoke flow**

Invoke the graph in a new thread, inspect `context_stats`, invoke a second turn, and confirm the compact recent-message window is retained. Use a temporary inline script and remove it afterward.

- [ ] **Step 4: Verify PostgreSQL Checkpointer compatibility**

Pause at an interrupt, close the runtime, reopen it, and resume with the same server-derived thread ID. Confirm the new memory fields deserialize and old checkpoints without them use defaults.

- [ ] **Step 5: Produce the learning handoff**

Report the exact TODO order for the project author: token counting → repository SQL → summary generation → fact candidate validation → retrieval/indexing → multi-conversation API. Explain which file starts each exercise.
