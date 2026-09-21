# Intent Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the version-compatible contracts, intent registry, input-boundary classifier, structured multi-intent analyzer, and deterministic policy engine without changing the production graph entry point.

**Architecture:** New code lives beside the current Agent and is tested as pure components first. The LLM may produce semantic candidates, but Pydantic models, the intent registry, slot rules, and the policy engine own the executable decision.

**Tech Stack:** Python 3.12, Pydantic 2.10+, LangGraph 1.2.12 target, OpenAI-compatible chat API, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-22-enterprise-intent-routing-design.md`

## Global Constraints

- Keep the current graph runnable until the migration task explicitly switches entry points.
- Pin `langgraph==1.2.12` only after compatibility tests pass.
- Pin `langgraph-checkpoint-sqlite==3.1.1` for local durable checkpoints.
- `owner_id`, `self_person_id`, resource IDs, risk level, decision, and confirmation cannot be accepted from model output.
- Every persisted or traced structure carries a schema or component version.
- New modules use typed Pydantic models; do not pass anonymous nested dictionaries across module boundaries.
- Tests in this plan do not call a live model, Neo4j, ChromaDB, or network service.

## Review Focus

- A pasted customer sentence containing “删除” must remain `CONTENT` and must not become `PERSON_DELETE`; Task 3 pins this.
- Duplicate task IDs or unknown dependencies must fail Pydantic validation; Task 2 pins this.
- An unknown intent string must fail closed instead of becoming an executable tool name; Task 2 pins this.
- A high model confidence with a missing required slot must route to `CLARIFY`; Task 4 pins this.
- A model-provided `owner_id`, `person_id`, or `confirmed` field must be removed before policy evaluation; Task 4 pins this.

---

## File Structure

```text
backend/agent/intent/
├── __init__.py             # Public exports for the intent package
├── enums.py                # Stable enum values shared by all intent modules
├── models.py               # Pydantic contracts for model output and resolved tasks
├── registry.py             # IntentDefinition registry and lookup validation
├── boundary.py             # COMMAND/CONTENT/MIXED segmentation
├── analyzer.py             # Structured LLM adapter and repair-once behavior
└── policy.py               # Slot validation, sanitization, score, and route decision

tests/unit/agent/intent/
├── test_models.py
├── test_registry.py
├── test_boundary.py
├── test_analyzer.py
└── test_policy.py
```

## Task 1: LangGraph 1.x Compatibility Gate

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/compat/test_langgraph_runtime.py`

**Interfaces:**
- Consumes: Python 3.12 and the existing `AgentState` concept.
- Produces: a verified dependency lock that supports `Command`, checkpointers, `interrupt()`, and resume by `thread_id`.

- [ ] **Step 1: Write the failing version and runtime compatibility test**

```python
import importlib.metadata
import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict


class CompatState(TypedDict, total=False):
    value: str
    approved: bool


class LangGraphCompatibilityTests(unittest.TestCase):
    def test_project_uses_reviewed_langgraph_version(self):
        self.assertEqual(importlib.metadata.version("langgraph"), "1.2.12")

    def test_checkpoint_and_interrupt_resume(self):
        def approval(state: CompatState):
            approved = interrupt({"value": state["value"]})
            return {"approved": bool(approved)}

        graph = StateGraph(CompatState)
        graph.add_node("approval", approval)
        graph.add_edge(START, "approval")
        graph.add_edge("approval", END)
        app = graph.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "compat-thread"}}

        paused = app.invoke({"value": "delete"}, config=config)
        self.assertIn("__interrupt__", paused)

        resumed = app.invoke(Command(resume=True), config=config)
        self.assertTrue(resumed["approved"])
```

- [ ] **Step 2: Run the compatibility test and verify the version assertion fails**

Run:

```powershell
python -m unittest tests.compat.test_langgraph_runtime -v
```

Expected: FAIL because the project currently resolves LangGraph `0.2.61`.

- [ ] **Step 3: Upgrade and lock the reviewed packages**

Edit `pyproject.toml`:

```toml
"langgraph==1.2.12",
"langgraph-checkpoint-sqlite==3.1.1",
```

Then run:

```powershell
uv lock --upgrade-package langgraph --upgrade-package langgraph-checkpoint-sqlite
uv sync
```

Do not continue if dependency resolution upgrades `langchain`, `langchain-core`, or `openai` across a major version without first recording the resolved versions in the commit message and running the full existing test suite.

- [ ] **Step 4: Run compatibility and existing tests**

Run:

```powershell
python -m unittest tests.compat.test_langgraph_runtime -v
python -m unittest discover -s tests -v
```

Expected: both commands PASS.

- [ ] **Step 5: Commit the compatibility gate**

```powershell
git add pyproject.toml uv.lock tests/compat/test_langgraph_runtime.py
git commit -m "build: verify LangGraph 1.x runtime compatibility"
```

## Task 2: Intent Enums, Models, and Registry

**Files:**
- Create: `backend/agent/intent/__init__.py`
- Create: `backend/agent/intent/enums.py`
- Create: `backend/agent/intent/models.py`
- Create: `backend/agent/intent/registry.py`
- Create: `tests/unit/agent/intent/test_models.py`
- Create: `tests/unit/agent/intent/test_registry.py`

**Interfaces:**
- Consumes: Pydantic `BaseModel`, `ConfigDict`, and `model_validator`.
- Produces: `IntentAnalysis`, `IntentTask`, `ResolvedTask`, `IntentDefinition`, `get_intent_definition(intent)`.

- [ ] **Step 1: Write failing model and registry tests**

```python
import unittest
from pydantic import ValidationError

from backend.agent.intent.enums import IntentCode, InputType, RouteDecision
from backend.agent.intent.models import IntentAnalysis, IntentTask
from backend.agent.intent.registry import get_intent_definition


class IntentContractTests(unittest.TestCase):
    def test_multi_intent_analysis_accepts_valid_dependencies(self):
        analysis = IntentAnalysis(
            schema_version="1.0",
            input_type=InputType.COMMAND,
            domains=["RELATIONSHIP"],
            tasks=[
                IntentTask(task_id="t1", intent=IntentCode.PERSON_CREATE),
                IntentTask(
                    task_id="t2",
                    intent=IntentCode.RELATION_CREATE,
                    depends_on=["t1"],
                ),
            ],
        )
        self.assertEqual([task.task_id for task in analysis.tasks], ["t1", "t2"])

    def test_duplicate_task_id_and_unknown_dependency_are_rejected(self):
        for tasks in (
            [
                IntentTask(task_id="t1", intent=IntentCode.PERSON_QUERY),
                IntentTask(task_id="t1", intent=IntentCode.PERSON_SEARCH),
            ],
            [
                IntentTask(
                    task_id="t1",
                    intent=IntentCode.PERSON_QUERY,
                    depends_on=["missing"],
                )
            ],
        ):
            with self.subTest(tasks=tasks):
                with self.assertRaises(ValidationError):
                    IntentAnalysis(
                        schema_version="1.0",
                        input_type=InputType.COMMAND,
                        domains=["RELATIONSHIP"],
                        tasks=tasks,
                    )

    def test_unknown_intent_fails_closed(self):
        with self.assertRaises(ValidationError):
            IntentTask(task_id="t1", intent="DROP_DATABASE")

    def test_registry_owns_risk_and_required_slots(self):
        definition = get_intent_definition(IntentCode.PERSON_DELETE)
        self.assertEqual(definition.risk_level.value, "HIGH_WRITE")
        self.assertEqual(definition.required_slots, frozenset({"person_reference"}))
```

- [ ] **Step 2: Run tests and verify imports fail**

Run:

```powershell
python -m unittest discover -s tests/unit/agent/intent -p "test_models.py" -v
```

Expected: FAIL because `backend.agent.intent` does not exist.

- [ ] **Step 3: Implement enums, strict models, and registry**

Implement stable enums in `enums.py` and set every Pydantic model to `ConfigDict(extra="forbid")`. Add an `IntentAnalysis` model validator that enforces unique task IDs, existing dependencies, and no self-dependency.

Use these stable enum values; persisted values must never depend on enum member order:

```python
class Domain(str, Enum):
    RELATIONSHIP = "RELATIONSHIP"
    SALES = "SALES"
    GENERAL = "GENERAL"


class InputType(str, Enum):
    COMMAND = "COMMAND"
    CONTENT = "CONTENT"
    MIXED = "MIXED"


class SegmentType(str, Enum):
    COMMAND = "COMMAND"
    CONTENT = "CONTENT"


class RiskLevel(str, Enum):
    READ = "READ"
    LOW_WRITE = "LOW_WRITE"
    MEDIUM_WRITE = "MEDIUM_WRITE"
    HIGH_WRITE = "HIGH_WRITE"
    FORBIDDEN = "FORBIDDEN"


class RouteDecision(str, Enum):
    EXECUTE = "EXECUTE"
    CLARIFY = "CLARIFY"
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"
    RESPOND = "RESPOND"


class MemoryWritePolicy(str, Enum):
    NONE = "NONE"
    FOCUS_ONLY = "FOCUS_ONLY"
    VERIFIED_FACTS = "VERIFIED_FACTS"
```

`IntentCode` contains exactly: `PERSON_CREATE`, `PERSON_UPDATE`, `PERSON_DELETE`, `PERSON_QUERY`, `PERSON_SEARCH`, `RELATION_CREATE`, `RELATION_UPDATE`, `RELATION_DELETE`, `RELATION_LIST`, `KINSHIP_QUERY`, `CHAT_SUMMARIZE`, `CUSTOMER_PROFILE_EXTRACT`, `CUSTOMER_NEED_ANALYZE`, `SALES_FOLLOWUP_ADVICE`, `SALES_TALKING_POINTS`, `SALES_RISK_DETECT`, `CHITCHAT`, `HELP`, `OUT_OF_SCOPE`, and `UNKNOWN`.

Use this registry shape:

```python
@dataclass(frozen=True, slots=True)
class IntentDefinition:
    code: IntentCode
    domain: Domain
    description: str
    required_slots: frozenset[str]
    optional_slots: frozenset[str]
    risk_level: RiskLevel
    planner_key: str
    memory_policy: MemoryWritePolicy


def get_intent_definition(intent: IntentCode) -> IntentDefinition:
    try:
        return INTENT_REGISTRY[intent]
    except KeyError as exc:
        raise ValueError(f"Unregistered intent: {intent}") from exc
```

Register every intent listed in design section 6. Run a module-level validation that `set(INTENT_REGISTRY) == set(IntentCode)`.

Use this registry matrix as the first implementation baseline:

| Intent group | Risk | Required slots | Planner key | Memory policy |
|---|---|---|---|---|
| `PERSON_CREATE` | `LOW_WRITE` | `name` | `person_create` | `VERIFIED_FACTS` |
| `PERSON_UPDATE` | `MEDIUM_WRITE` | `person_reference`, `changes` | `person_update` | `VERIFIED_FACTS` |
| `PERSON_DELETE` | `HIGH_WRITE` | `person_reference` | `person_delete` | `NONE` |
| `PERSON_QUERY` | `READ` | `person_reference` | `person_query` | `FOCUS_ONLY` |
| `PERSON_SEARCH` | `READ` | `query` | `person_search` | `FOCUS_ONLY` |
| `RELATION_CREATE` | `LOW_WRITE` | `from_reference`, `to_reference`, `relation_type` | `relation_create` | `VERIFIED_FACTS` |
| `RELATION_UPDATE` | `MEDIUM_WRITE` | `relation_reference`, `changes` | `relation_update` | `VERIFIED_FACTS` |
| `RELATION_DELETE` | `HIGH_WRITE` | `relation_reference` | `relation_delete` | `NONE` |
| `RELATION_LIST` | `READ` | `person_reference` | `relation_list` | `FOCUS_ONLY` |
| `KINSHIP_QUERY` | `READ` | `target_reference` | `kinship_query` | `FOCUS_ONLY` |
| Six sales intents | `READ` | `content` | lower-case intent value | `NONE` |
| `CHITCHAT`, `HELP`, `OUT_OF_SCOPE`, `UNKNOWN` | `READ` | none | `respond_only` | `NONE` |

For the sales row, “lower-case intent value” is deterministic: `CHAT_SUMMARIZE → chat_summarize`, `CUSTOMER_PROFILE_EXTRACT → customer_profile_extract`, `CUSTOMER_NEED_ANALYZE → customer_need_analyze`, `SALES_FOLLOWUP_ADVICE → sales_followup_advice`, `SALES_TALKING_POINTS → sales_talking_points`, and `SALES_RISK_DETECT → sales_risk_detect`.

- [ ] **Step 4: Run contract tests**

Run:

```powershell
python -m unittest tests.unit.agent.intent.test_models tests.unit.agent.intent.test_registry -v
```

Expected: PASS.

- [ ] **Step 5: Commit the intent contract**

```powershell
git add backend/agent/intent tests/unit/agent/intent/test_models.py tests/unit/agent/intent/test_registry.py
git commit -m "feat: add versioned intent contracts and registry"
```

## Task 3: Input Boundary Classifier

**Files:**
- Create: `backend/agent/intent/boundary.py`
- Create: `tests/unit/agent/intent/test_boundary.py`

**Interfaces:**
- Consumes: raw user text.
- Produces: `BoundaryResult(input_type: InputType, segments: list[InputSegment], requires_model_fallback: bool)`.

- [ ] **Step 1: Write failing boundary tests**

```python
import unittest

from backend.agent.intent.boundary import classify_input_boundary
from backend.agent.intent.enums import InputType, SegmentType


class BoundaryTests(unittest.TestCase):
    def test_plain_instruction_is_command(self):
        result = classify_input_boundary("查询张三的资料")
        self.assertEqual(result.input_type, InputType.COMMAND)

    def test_prefixed_chat_is_content_and_delete_word_is_not_a_command(self):
        result = classify_input_boundary("聊天记录：\n客户：这个方案先删掉，下周再聊")
        self.assertEqual(result.input_type, InputType.CONTENT)
        self.assertEqual(result.segments[0].segment_type, SegmentType.CONTENT)

    def test_instruction_plus_chat_is_mixed_with_source_spans(self):
        text = "分析以下聊天并给出建议：\n客户：预算有点高"
        result = classify_input_boundary(text)
        self.assertEqual(result.input_type, InputType.MIXED)
        self.assertEqual([s.segment_type for s in result.segments], [
            SegmentType.COMMAND,
            SegmentType.CONTENT,
        ])
        self.assertEqual("".join(s.text for s in result.segments), text)

    def test_ambiguous_quote_requests_model_fallback_without_write_intent(self):
        result = classify_input_boundary("客户刚说：把旧联系人删除")
        self.assertTrue(result.requires_model_fallback)
        self.assertNotEqual(result.input_type, InputType.COMMAND)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest tests.unit.agent.intent.test_boundary -v
```

Expected: FAIL because `classify_input_boundary` is missing.

- [ ] **Step 3: Implement deterministic segmentation**

Implement explicit markers such as `聊天记录：`, `对话如下：`, fenced chat blocks, and repeated speaker prefixes. Preserve character spans. For unmarked quoted speech, return `requires_model_fallback=True` and use the safe default `CONTENT`; never default ambiguous text to `COMMAND`.

```python
def classify_input_boundary(text: str) -> BoundaryResult:
    normalized = normalize_newlines(text)
    marker = find_first_content_marker(normalized)
    if marker is not None:
        return split_at_marker(normalized, marker)
    if looks_like_pasted_dialogue(normalized) or contains_reported_speech(normalized):
        return BoundaryResult(
            input_type=InputType.CONTENT,
            segments=[InputSegment.content(normalized, 0, len(normalized))],
            requires_model_fallback=True,
        )
    return BoundaryResult.command(normalized)
```

- [ ] **Step 4: Run boundary tests**

Run:

```powershell
python -m unittest tests.unit.agent.intent.test_boundary -v
```

Expected: PASS.

- [ ] **Step 5: Commit boundary classification**

```powershell
git add backend/agent/intent/boundary.py tests/unit/agent/intent/test_boundary.py
git commit -m "feat: separate commands from pasted conversation content"
```

## Task 4: Structured Analyzer and Deterministic Policy

**Files:**
- Create: `backend/agent/intent/analyzer.py`
- Create: `backend/agent/intent/policy.py`
- Modify: `backend/services/llm.py`
- Create: `tests/unit/agent/intent/test_analyzer.py`
- Create: `tests/unit/agent/intent/test_policy.py`

**Interfaces:**
- Consumes: `BoundaryResult`, relevant context, and a `StructuredChatClient` protocol.
- Produces: validated `IntentAnalysis` and `list[ResolvedTask]`.

- [ ] **Step 1: Write failing analyzer and policy tests**

```python
import unittest

from backend.agent.intent.enums import IntentCode, RouteDecision
from backend.agent.intent.models import IntentTask
from backend.agent.intent.policy import evaluate_task


class PolicyTests(unittest.TestCase):
    def test_high_confidence_missing_slot_still_clarifies(self):
        task = IntentTask(
            task_id="t1",
            intent=IntentCode.PERSON_DELETE,
            raw_arguments={},
            evidence=["删掉他"],
            model_confidence=0.99,
        )
        resolved = evaluate_task(task, entity_bindings=[])
        self.assertEqual(resolved.decision, RouteDecision.CLARIFY)
        self.assertEqual(resolved.missing_slots, ["person_reference"])

    def test_untrusted_identity_and_confirmation_fields_are_removed(self):
        task = IntentTask(
            task_id="t1",
            intent=IntentCode.PERSON_CREATE,
            raw_arguments={
                "name": "张三",
                "owner_id": "forged",
                "person_id": "invented",
                "confirmed": True,
            },
            evidence=["记录张三"],
            model_confidence=0.95,
        )
        resolved = evaluate_task(task, entity_bindings=[])
        self.assertEqual(resolved.arguments, {"name": "张三"})

    def test_high_risk_task_requires_confirmation_after_resolution(self):
        task = IntentTask(
            task_id="t1",
            intent=IntentCode.PERSON_DELETE,
            raw_arguments={"person_reference": "张三"},
            evidence=["删除张三"],
            model_confidence=0.95,
        )
        resolved = evaluate_task(
            task,
            entity_bindings=[{"mention": "张三", "person_id": "p_1", "score": 1.0}],
        )
        self.assertEqual(resolved.decision, RouteDecision.CONFIRM)
```

In `test_analyzer.py`, use a fake `StructuredChatClient` that returns invalid JSON first and valid JSON second. Assert exactly one repair call; when both responses are invalid, assert `StructuredOutputError` is raised with code `MODEL_OUTPUT_INVALID`.

- [ ] **Step 2: Run analyzer and policy tests**

Run:

```powershell
python -m unittest tests.unit.agent.intent.test_analyzer tests.unit.agent.intent.test_policy -v
```

Expected: FAIL because analyzer and policy modules are missing.

- [ ] **Step 3: Implement structured client protocol and policy**

Add to `backend/services/llm.py`:

```python
class StructuredChatClient(Protocol):
    def complete(self, *, system_prompt: str, user_text: str, schema: type[T]) -> T: ...
```

Implement `IntentAnalyzer.analyze(...)` with one validation-repair attempt. The repair prompt contains the validation errors and schema, but never contains Tool credentials or trusted context fields.

Implement `evaluate_task(...)` in this exact order:

1. Remove `owner_id`, `self_person_id`, `person_id`, `relation_id`, `confirmed`, and `idempotency_key` from model arguments.
2. Load required slots and risk from the registry.
3. Attach resolved entity IDs only from `entity_bindings`.
4. Calculate evidence coverage, slot completeness, entity score, and context consistency.
5. Apply forced clarification rules.
6. Apply the risk matrix and thresholds.

- [ ] **Step 4: Run unit and full regression tests**

Run:

```powershell
python -m unittest discover -s tests/unit/agent/intent -v
python -m unittest discover -s tests -v
```

Expected: PASS.

- [ ] **Step 5: Commit structured analysis and policy**

```powershell
git add backend/agent/intent backend/services/llm.py tests/unit/agent/intent
git commit -m "feat: add structured multi-intent analysis policy"
```

## Plan 1 Completion Gate

Run:

```powershell
python -m unittest discover -s tests/unit/agent/intent -v
python -m unittest discover -s tests/compat -v
python -m unittest discover -s tests -v
```

The production graph still uses its original entry point. The new package is ready for the planning-runtime plan only when all commands pass and no live external service was required.
