# Memory and Human-in-the-Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add server-owned conversation identity, durable LangGraph checkpoints, cross-turn entity resolution, recoverable clarification and confirmation, and evidence-based long-term memory writes.

**Architecture:** The HTTP layer creates trusted request and conversation context. A context loader combines checkpoint state with a small long-term-memory projection. Entity resolution is deterministic and produces candidates with scores and evidence. LangGraph `interrupt()` pauses clarification or high-risk confirmation and resumes through the same server-derived `thread_id`. Memory is written only after successful execution.

**Tech Stack:** Python 3.12, Pydantic 2.10+, LangGraph 1.2.12, `langgraph-checkpoint-sqlite` 3.1.1, SQLite, existing Neo4j repositories, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-22-enterprise-intent-routing-design.md`

## Global Constraints

- Plans 1 and 2 must be complete before this plan starts.
- `owner_id`, `self_person_id`, `thread_id`, `request_id`, operation IDs, and confirmation state come only from server-controlled code.
- Version 1 uses one default Agent conversation per user. Derive its opaque ID as SHA-256 of `owner_id + ":default"`; never accept a raw `thread_id` from the browser.
- Set `LANGGRAPH_STRICT_MSGPACK=true` before constructing the SQLite checkpointer. Do not add arbitrary deserialization allowlists.
- A clarification or confirmation belongs to one owner, thread, operation, and plan version; it expires and can be consumed once.
- Ambiguous identity never defaults to the first database or vector-search result.
- Raw pasted customer chats stay in the active conversation/checkpoint. Long-term memory stores only validated facts, their source reference, and provenance.
- Failed, skipped, unconfirmed, or rolled-back operations cannot write long-term memory.
- Keep the old graph available behind `AGENT_RUNTIME_VERSION=v1` until the V2 graph passes the scenario tests in Task 4.

## Review Focus

- “张三是老师” followed by “他喜欢钓鱼” resolves “他” only when the recent focus is unique; Task 1 pins this.
- Two plausible people named 张三 trigger a candidate clarification and no Tool execution; Task 1 pins this.
- Restarting the service between interrupt and resume preserves the pending operation; Task 2 pins this.
- A confirmation from another owner, thread, expired operation, or second submission is rejected; Task 3 pins this.
- An execution failure leaves long-term memory unchanged; Task 5 pins this.

---

## File Structure

```text
backend/agent/
├── memory/
│   ├── __init__.py
│   ├── models.py              # Candidate, focus, fact, and provenance contracts
│   ├── context_loader.py      # Load checkpoint projection and relevant facts
│   ├── entity_resolver.py     # Pronoun/name candidate scoring and resolution
│   └── writer.py              # Post-success fact persistence
├── nodes/
│   ├── load_context.py
│   ├── resolve_entities.py
│   ├── request_clarification.py
│   ├── request_confirmation.py
│   └── write_memory.py
├── persistence.py             # SQLite checkpointer factory and thread derivation
├── runtime_graph.py           # V2 LangGraph wiring
└── state_v2.py                # Versioned state used by the new graph

backend/models/schemas.py      # Resume request/response contracts
backend/routers/chat.py        # Authenticated invoke and resume endpoints

tests/unit/agent/memory/
├── test_entity_resolver.py
├── test_context_loader.py
└── test_writer.py
tests/unit/agent/
└── test_persistence.py
tests/integration/agent/
├── test_checkpoint_resume.py
└── test_confirmation_lifecycle.py
tests/scenarios/agent/
└── test_cross_turn_resolution.py
```

## Task 1: Memory Contracts and Entity Resolution

**Files:**
- Create: `backend/agent/memory/__init__.py`
- Create: `backend/agent/memory/models.py`
- Create: `backend/agent/memory/context_loader.py`
- Create: `backend/agent/memory/entity_resolver.py`
- Create: `tests/unit/agent/memory/test_entity_resolver.py`
- Create: `tests/unit/agent/memory/test_context_loader.py`

**Interfaces:**
- Consumes: trusted `owner_id`, recent entity focus, relationship candidates, optional semantic candidates, and an extracted mention.
- Produces: `EntityResolution(status, mention, candidates, selected, reason_codes)` and `LoadedContext`.

- [ ] **Step 1: Write failing resolution tests**

```python
import unittest

from backend.agent.memory.entity_resolver import resolve_entity
from backend.agent.memory.models import CandidateSource, EntityCandidate, EntityFocus


def candidate(person_id: str, name: str, score: float, source: CandidateSource):
    return EntityCandidate(
        person_id=person_id,
        display_name=name,
        score=score,
        sources={source},
        evidence=[f"{source.value}:{name}"],
    )


class EntityResolverTests(unittest.TestCase):
    def test_pronoun_resolves_unique_recent_focus(self):
        result = resolve_entity(
            mention="他",
            recent_focus=[EntityFocus(person_id="p_1", display_name="张三", turn_distance=1)],
            graph_candidates=[],
            semantic_candidates=[],
        )
        self.assertEqual(result.status.value, "RESOLVED")
        self.assertEqual(result.selected.person_id, "p_1")

    def test_same_name_candidates_require_clarification(self):
        result = resolve_entity(
            mention="张三",
            recent_focus=[],
            graph_candidates=[
                candidate("p_1", "张三", 0.91, CandidateSource.GRAPH),
                candidate("p_2", "张三", 0.89, CandidateSource.GRAPH),
            ],
            semantic_candidates=[],
        )
        self.assertEqual(result.status.value, "AMBIGUOUS")
        self.assertIsNone(result.selected)
        self.assertEqual([c.person_id for c in result.candidates], ["p_1", "p_2"])

    def test_low_score_candidate_is_not_auto_selected(self):
        result = resolve_entity(
            mention="小张",
            recent_focus=[],
            graph_candidates=[candidate("p_1", "张三", 0.62, CandidateSource.GRAPH)],
            semantic_candidates=[],
        )
        self.assertEqual(result.status.value, "UNRESOLVED")
```

In `test_context_loader.py`, use fake repositories to assert every load receives `owner_id`, only the latest 12 turns are returned, superseded facts are excluded, and candidate evidence contains no raw customer-chat body.

- [ ] **Step 2: Run the memory tests and verify import failure**

```powershell
python -m unittest discover -s tests/unit/agent/memory -v
```

Expected: FAIL because the memory package does not exist.

- [ ] **Step 3: Implement typed candidates and deterministic score policy**

Set these initial thresholds in one `ResolutionPolicy`: auto-resolve at `0.85` only when the gap to the second candidate is at least `0.15`; clarify when at least two candidates score `0.65` or more; otherwise remain unresolved. Candidate ordering is `(-score, person_id)` for repeatability.

```python
class EntityResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: ResolutionStatus
    mention: str
    candidates: list[EntityCandidate]
    selected: EntityCandidate | None = None
    reason_codes: list[str]


def choose_candidate(candidates: list[EntityCandidate], policy: ResolutionPolicy):
    ranked = sorted(candidates, key=lambda item: (-item.score, item.person_id))
    if not ranked or ranked[0].score < policy.clarify_threshold:
        return ResolutionStatus.UNRESOLVED, None
    if len(ranked) > 1 and ranked[1].score >= policy.clarify_threshold:
        if ranked[0].score - ranked[1].score < policy.minimum_gap:
            return ResolutionStatus.AMBIGUOUS, None
    if ranked[0].score >= policy.auto_resolve_threshold:
        return ResolutionStatus.RESOLVED, ranked[0]
    return ResolutionStatus.UNRESOLVED, None
```

Merge the same `person_id` from multiple sources before choosing. Cap the final candidate list at five. Record source and reason codes so evaluation can explain the decision.

- [ ] **Step 4: Run the memory tests**

```powershell
python -m unittest discover -s tests/unit/agent/memory -v
```

Expected: PASS.

- [ ] **Step 5: Commit entity resolution**

```powershell
git add backend/agent/memory tests/unit/agent/memory/test_entity_resolver.py tests/unit/agent/memory/test_context_loader.py
git commit -m "feat: resolve cross-turn entity references with evidence"
```

## Task 2: Durable SQLite Checkpoints and Server-Owned Threads

**Files:**
- Create: `backend/agent/persistence.py`
- Create: `tests/unit/agent/test_persistence.py`
- Create: `tests/integration/agent/test_checkpoint_resume.py`
- Modify: `backend/config.py`

**Interfaces:**
- Consumes: trusted `owner_id` and configured SQLite path.
- Produces: `derive_thread_id(owner_id) -> str` and `open_checkpointer(path) -> ContextManager[SqliteSaver]`.

- [ ] **Step 1: Write failing derivation and restart tests**

```python
import tempfile
import unittest
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from backend.agent.persistence import derive_thread_id, open_checkpointer


class PauseState(TypedDict, total=False):
    operation_id: str
    approved: bool


def build_pause_graph(checkpointer):
    def pause(state: PauseState):
        answer = interrupt({"operation_id": state["operation_id"]})
        return {"approved": answer is True}

    builder = StateGraph(PauseState)
    builder.add_node("pause", pause)
    builder.add_edge(START, "pause")
    builder.add_edge("pause", END)
    return builder.compile(checkpointer=checkpointer)


class CheckpointRestartTests(unittest.TestCase):
    def test_thread_id_is_stable_and_owner_specific(self):
        self.assertEqual(derive_thread_id("u_1"), derive_thread_id("u_1"))
        self.assertNotEqual(derive_thread_id("u_1"), derive_thread_id("u_2"))
        self.assertNotIn("u_1", derive_thread_id("u_1"))

    def test_interrupt_resumes_after_checkpointer_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoints.sqlite"
            config = {"configurable": {"thread_id": derive_thread_id("u_1")}}
            with open_checkpointer(path) as saver:
                paused = build_pause_graph(saver).invoke({"operation_id": "op_1"}, config)
                self.assertIn("__interrupt__", paused)
            with open_checkpointer(path) as saver:
                resumed = build_pause_graph(saver).invoke(Command(resume=True), config)
                self.assertTrue(resumed["approved"])
```

- [ ] **Step 2: Run checkpoint tests and verify failure**

```powershell
python -m unittest tests.unit.agent.test_persistence tests.integration.agent.test_checkpoint_resume -v
```

Expected: FAIL because the persistence factory is missing.

- [ ] **Step 3: Implement the checkpointer factory and configuration**

Add `AGENT_CHECKPOINT_PATH`, `AGENT_RUNTIME_VERSION`, and a startup assertion for strict MessagePack mode. Create parent directories before connecting, use a context manager so the connection closes cleanly, and call the saver setup method required by the installed API.

```python
THREAD_NAMESPACE = "relationship-agent:v1"


def derive_thread_id(owner_id: str) -> str:
    material = f"{THREAD_NAMESPACE}:{owner_id}:default".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


@contextmanager
def open_checkpointer(path: Path):
    if os.getenv("LANGGRAPH_STRICT_MSGPACK") != "true":
        raise RuntimeError("LANGGRAPH_STRICT_MSGPACK must be true")
    path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(path)) as saver:
        saver.setup()
        yield saver
```

- [ ] **Step 4: Run checkpoint and compatibility tests**

```powershell
python -m unittest tests.unit.agent.test_persistence tests.integration.agent.test_checkpoint_resume -v
python -m unittest tests.compat.test_langgraph_runtime -v
```

Expected: PASS with a temporary SQLite file and no network service.

- [ ] **Step 5: Commit durable checkpoints**

```powershell
git add backend/agent/persistence.py backend/config.py tests/unit/agent/test_persistence.py tests/integration/agent/test_checkpoint_resume.py
git commit -m "feat: persist agent checkpoints with server-owned threads"
```

## Task 3: Clarification and Confirmation Lifecycle

**Files:**
- Create: `backend/agent/nodes/request_clarification.py`
- Create: `backend/agent/nodes/request_confirmation.py`
- Modify: `backend/models/schemas.py`
- Modify: `backend/routers/chat.py`
- Create: `tests/integration/agent/test_confirmation_lifecycle.py`

**Interfaces:**
- Consumes: pending `ClarificationRequest` or `ConfirmationRequest`, authenticated context, and a resume answer.
- Produces: an interrupt payload and a validated `ResumeAgentRequest` applied through `Command(resume=...)`.

- [ ] **Step 1: Write failing lifecycle tests**

```python
import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from backend.agent.intent.enums import RiskLevel
from backend.agent.memory.models import ConfirmationRequest, ResourceRef
from backend.agent.nodes.request_confirmation import validate_confirmation_resume
from backend.models.schemas import ResumeAgentRequest


class ConfirmationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.pending = ConfirmationRequest(
            operation_id="op_1",
            owner_id="u_1",
            thread_id="thread_1",
            plan_id="plan_1",
            plan_version=1,
            risk_level=RiskLevel.HIGH_WRITE,
            summary="删除人物张三以及关联关系",
            affected_resources=[
                ResourceRef(
                    resource_type="PERSON",
                    resource_id="p_1",
                    display_name="张三",
                )
            ],
            proposed_arguments={"person_id": "p_1"},
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            consumed_at=None,
        )

    def test_resume_schema_accepts_only_strict_boolean_confirmation(self):
        with self.assertRaises(ValidationError):
            ResumeAgentRequest(operation_id="op_1", confirmation="yes")

    def test_wrong_owner_or_thread_is_rejected(self):
        for owner_id, thread_id in (("u_2", "thread_1"), ("u_1", "thread_2")):
            with self.subTest(owner_id=owner_id, thread_id=thread_id):
                with self.assertRaises(PermissionError):
                    validate_confirmation_resume(
                        self.pending,
                        owner_id=owner_id,
                        thread_id=thread_id,
                        operation_id="op_1",
                        confirmation=True,
                        now=datetime.now(UTC),
                    )

    def test_expired_and_consumed_confirmations_are_rejected(self):
        expired = self.pending.model_copy(update={"expires_at": datetime.now(UTC)})
        consumed = self.pending.model_copy(update={"consumed_at": datetime.now(UTC)})
        for pending in (expired, consumed):
            with self.assertRaises(ValueError):
                validate_confirmation_resume(
                    pending,
                    owner_id="u_1",
                    thread_id="thread_1",
                    operation_id="op_1",
                    confirmation=True,
                    now=datetime.now(UTC) + timedelta(seconds=1),
                )
```

Add a matching clarification test: the reply must choose one of the offered candidate IDs or explicitly cancel; free text that maps to multiple candidates re-interrupts with the same `operation_id`.

- [ ] **Step 2: Run lifecycle tests and verify failure**

```powershell
python -m unittest tests.integration.agent.test_confirmation_lifecycle -v
```

Expected: FAIL because resume contracts and interrupt nodes are missing.

- [ ] **Step 3: Implement minimal interrupt payloads and authenticated resume**

Expose the pending operation ID, type, human-readable summary/question, candidate labels, expiration, and plan version. Do not expose owner IDs, internal scores, prompts, or raw Tool arguments. The resume route derives owner/thread again from authentication and then invokes:

```python
config = {"configurable": {"thread_id": derive_thread_id(context.owner_id)}}
result = runtime_graph.invoke(
    Command(resume={
        "operation_id": request.operation_id,
        "confirmation": request.confirmation,
        "candidate_id": request.candidate_id,
    }),
    config=config,
)
```

Use `StrictBool` for confirmation, require exactly one of `confirmation` or `candidate_id`, and mark the operation consumed in state before any write step runs.

- [ ] **Step 4: Run lifecycle, schema, and auth regression tests**

```powershell
python -m unittest tests.integration.agent.test_confirmation_lifecycle -v
python -m unittest discover -s tests -p "test_*auth*.py" -v
```

Expected: PASS.

- [ ] **Step 5: Commit the human-in-the-loop boundary**

```powershell
git add backend/agent/nodes/request_clarification.py backend/agent/nodes/request_confirmation.py backend/models/schemas.py backend/routers/chat.py tests/integration/agent/test_confirmation_lifecycle.py
git commit -m "feat: add recoverable clarification and confirmation"
```

## Task 4: Integrate the V2 Runtime Graph

**Files:**
- Create: `backend/agent/state_v2.py`
- Create: `backend/agent/nodes/load_context.py`
- Create: `backend/agent/nodes/resolve_entities.py`
- Create: `backend/agent/runtime_graph.py`
- Modify: `backend/routers/chat.py`
- Create: `tests/fakes/agent_runtime.py`
- Create: `tests/scenarios/agent/test_cross_turn_resolution.py`

**Interfaces:**
- Consumes: Plan 1 intent components, Plan 2 planning runtime, Task 1 memory components, and Task 2 checkpointer.
- Produces: `build_runtime_graph(checkpointer, dependencies) -> CompiledStateGraph` and feature-flagged HTTP routing.

- [ ] **Step 1: Write the failing two-turn and ambiguous-name scenarios**

```python
import unittest

from tests.fakes.agent_runtime import FakeAgentDependencies, invoke_turn


class CrossTurnScenarioTests(unittest.TestCase):
    def test_unique_recent_focus_resolves_pronoun_on_second_turn(self):
        deps = FakeAgentDependencies.with_people([{"person_id": "p_1", "name": "张三"}])
        invoke_turn(deps, owner_id="u_1", text="张三是老师")
        result = invoke_turn(deps, owner_id="u_1", text="他喜欢钓鱼")
        self.assertEqual(result.resolved_entities[0].person_id, "p_1")
        self.assertEqual(result.executed_tools, ["add_person_fact"])

    def test_ambiguous_name_interrupts_before_tool_execution(self):
        deps = FakeAgentDependencies.with_people([
            {"person_id": "p_1", "name": "张三"},
            {"person_id": "p_2", "name": "张三"},
        ])
        result = invoke_turn(deps, owner_id="u_1", text="查询张三的资料")
        self.assertEqual(result.interrupt.type, "CLARIFICATION")
        self.assertEqual(deps.tool_calls, [])
```

Implement `tests/fakes/agent_runtime.py` in this task as an in-memory dependency container that supplies structured analyzer outputs, repositories, a deterministic clock, and fake Tools. It must call the real graph and never duplicate production routing logic.

- [ ] **Step 2: Run scenario tests and verify failure**

```powershell
python -m unittest tests.scenarios.agent.test_cross_turn_resolution -v
```

Expected: FAIL because the V2 state and graph are missing.

- [ ] **Step 3: Wire nodes and keep orchestration thin**

Define a versioned `AgentStateV2` with typed fields from the design. Each node does one job and returns only changed fields. Wire this path:

```text
START → load_context → classify_boundary → analyze_intents → resolve_entities
      → apply_policy → compile_plan → execute_ready_steps → route_after_results
      → request_clarification | request_confirmation | generate_response
      → write_memory → END
```

The router chooses V2 only when `AGENT_RUNTIME_VERSION=v2`. V1 remains callable during rollout. For V2, invoke with the server-derived thread configuration and initialize only trusted identity plus raw user text.

- [ ] **Step 4: Run scenarios and all Agent tests**

```powershell
python -m unittest tests.scenarios.agent.test_cross_turn_resolution -v
python -m unittest discover -s tests/unit/agent -v
python -m unittest discover -s tests/integration/agent -v
```

Expected: PASS with fake dependencies and a temporary checkpoint database.

- [ ] **Step 5: Commit V2 graph integration**

```powershell
git add backend/agent/state_v2.py backend/agent/nodes backend/agent/runtime_graph.py backend/routers/chat.py tests/fakes/agent_runtime.py tests/scenarios/agent/test_cross_turn_resolution.py
git commit -m "feat: integrate memory-aware agent runtime graph"
```

## Task 5: Write Validated Memory After Successful Execution

**Files:**
- Create: `backend/agent/memory/writer.py`
- Create: `backend/agent/nodes/write_memory.py`
- Create: `tests/fakes/memory.py`
- Create: `tests/unit/agent/memory/test_writer.py`
- Modify: `tests/scenarios/agent/test_cross_turn_resolution.py`

**Interfaces:**
- Consumes: successful `StepExecution` results, resolved entities, source turn ID, and intent registry memory policy.
- Produces: versioned `MemoryFact` upserts and updated recent focus.

- [ ] **Step 1: Write failing memory-write tests**

```python
import unittest

from backend.agent.memory.writer import collect_memory_mutations
from backend.tools.contracts import ToolResult
from tests.fakes.memory import completed_state


class MemoryWriterTests(unittest.TestCase):
    def test_successful_fact_write_has_provenance(self):
        state = completed_state(
            result=ToolResult.ok(data={"fact_id": "f_1"}),
            execution_status="SUCCEEDED",
            confirmed=True,
        )
        mutations = collect_memory_mutations(state)
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0].subject_person_id, "p_1")
        self.assertEqual(mutations[0].provenance.source_turn_id, "turn_2")
        self.assertEqual(mutations[0].provenance.operation_id, "op_1")

    def test_failure_skip_or_missing_confirmation_writes_nothing(self):
        for status, confirmed in (("FAILED", True), ("SKIPPED", True), ("SUCCEEDED", False)):
            with self.subTest(status=status, confirmed=confirmed):
                state = completed_state(
                    result=ToolResult.failure("INTERNAL_ERROR", "failed"),
                    execution_status=status,
                    confirmed=confirmed,
                )
                self.assertEqual(collect_memory_mutations(state), [])
```

Add a supersession test: a later validated occupation fact marks the prior occupation fact superseded and preserves both provenance records; it never deletes history silently.

- [ ] **Step 2: Run writer tests and verify failure**

```powershell
python -m unittest tests.unit.agent.memory.test_writer -v
```

Expected: FAIL because the writer is missing.

- [ ] **Step 3: Implement policy-driven memory mutations**

`collect_memory_mutations` reads the registry's `MemoryWritePolicy`, accepts only successful typed results, and produces immutable mutations. The node applies all mutations in one repository transaction. Update recent focus only from resolved or newly created person IDs.

```python
def may_write_memory(
    execution: StepExecution,
    step: PlanStep,
    confirmed: bool,
) -> bool:
    if execution.status is not StepStatus.SUCCEEDED:
        return False
    if step.risk_level in {RiskLevel.MEDIUM_WRITE, RiskLevel.HIGH_WRITE} and not confirmed:
        return False
    return execution.result is not None and execution.result.success
```

- [ ] **Step 4: Run memory, scenario, and full regression tests**

```powershell
python -m unittest discover -s tests/unit/agent/memory -v
python -m unittest discover -s tests/scenarios/agent -v
python -m unittest discover -s tests -v
```

Expected: PASS.

- [ ] **Step 5: Commit controlled memory writes**

```powershell
git add backend/agent/memory/writer.py backend/agent/nodes/write_memory.py tests/unit/agent/memory/test_writer.py tests/scenarios/agent/test_cross_turn_resolution.py tests/fakes/memory.py
git commit -m "feat: persist validated agent memory with provenance"
```

## Plan 3 Completion Gate

Run:

```powershell
$env:LANGGRAPH_STRICT_MSGPACK = "true"
python -m unittest discover -s tests/unit/agent -v
python -m unittest discover -s tests/integration/agent -v
python -m unittest discover -s tests/scenarios/agent -v
python -m unittest discover -s tests -v
```

Demonstrate three flows with V2 enabled: cross-turn pronoun resolution, same-name clarification followed by resume, and high-risk deletion confirmation followed by a process restart and resume. Inspect the long-term store to prove that successful facts contain provenance and failed or rejected operations created no memory.
