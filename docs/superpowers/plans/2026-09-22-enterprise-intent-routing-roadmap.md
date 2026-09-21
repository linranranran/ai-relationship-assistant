# Enterprise Intent Routing Delivery Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement the linked plans task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the approved enterprise intent-routing design in 20 focused development days at 4–6 hours per day, while keeping every day reviewable and every week independently demonstrable.

**Architecture:** Delivery follows the dependency chain `trusted intent → executable plan → recoverable state and memory → evidence and quality gate`. Each linked plan defines exact files, tests, interfaces, commits, and completion gates.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2.10+, LangGraph 1.2.12, SQLite checkpointer, Neo4j, optional Langfuse, React, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-22-enterprise-intent-routing-design.md`

## Linked Plans

1. [Plan 1 — Intent Foundation](2026-09-22-intent-foundation-implementation.md)
2. [Plan 2 — Planning Runtime](2026-09-22-planning-runtime-implementation.md)
3. [Plan 3 — Memory and Human-in-the-Loop](2026-09-22-memory-hitl-implementation.md)
4. [Plan 4 — Observability and Evaluation](2026-09-22-observability-evaluation-implementation.md)

## Global Constraints

- Complete plans in order. Within one plan, complete tasks in order unless the task explicitly states it can start earlier.
- Spend the first 45–60 minutes reading the named interfaces and writing the failing test; spend the final 30–45 minutes running regression, reviewing the diff, recording evidence, and committing.
- Implement the contracts and orchestration shown in the plans. Business algorithms remain the user's implementation exercise; comments must explain inputs, outputs, invariants, and failure branches without hiding executable logic inside comments.
- Stop the day's work when its acceptance command is red for an unexplained reason. Record the failing command, shortest error, expected behavior, and eliminated causes before continuing.
- Do not switch production traffic to V2 until Day 14 passes. Do not use résumé metrics until Day 20 passes with a model-backed report.
- Preserve the existing V1 entry point during the migration and keep commits scoped to one task.

## Review Focus

- A result-binding interface created in Week 2 must use the exact model types defined in Week 1.
- Server-owned identity and conversation fields must never re-enter browser or model-controlled schemas.
- Clarification, confirmation, retries, and memory writes must remain deterministic under request replay and process restart.
- Trace and evaluation output must prove system behavior without leaking raw customer content.

---

## Daily Cadence

| Block | Suggested time | Output |
|---|---:|---|
| Read and narrow | 30 min | Today's named interfaces and one acceptance target |
| Failing test | 60 min | Failure caused by missing behavior, not environment noise |
| Implement | 150–210 min | Smallest typed implementation that passes the target test |
| Regression | 45–60 min | Target tests plus the plan's related suite |
| Review and record | 30 min | Diff review, progress note, focused commit |

## Week 1 — Trusted Intent Foundation

### Day 1 — Dependency compatibility gate

- [ ] Execute Plan 1 Task 1.
- [ ] Prove LangGraph checkpoint plus `interrupt()`/resume on the reviewed pinned version.
- [ ] Run existing tests after the dependency lock changes.

**Daily evidence:** compatibility test output, resolved dependency versions, commit `build: verify LangGraph 1.x runtime compatibility`.

### Day 2 — Intent contracts and registry

- [ ] Execute Plan 1 Task 2.
- [ ] Define every enum, strict model, registry entry, required slot, risk, planner key, and memory policy.
- [ ] Reject duplicate tasks, bad dependencies, and unknown intents.

**Daily evidence:** intent contract tests and commit `feat: add versioned intent contracts and registry`.

### Day 3 — Command versus pasted-content boundary

- [ ] Execute Plan 1 Task 3.
- [ ] Cover plain commands, pasted chats, mixed input, reported speech, and a customer sentence containing “删除”.

**Daily evidence:** boundary fixtures and commit `feat: separate commands from pasted conversation content`.

### Day 4 — Structured multi-intent analyzer and policy

- [ ] Execute Plan 1 Task 4.
- [ ] Complete one repair attempt, slot validation, untrusted-field removal, confidence scoring, and risk routing.
- [ ] Run the Plan 1 completion gate.

**Daily evidence:** multi-intent structured output, policy reason codes, full Plan 1 tests, commit `feat: add structured multi-intent analysis policy`.

### Day 5 — Week 1 integration and interview explanation

- [ ] Run Plan 1 completion commands from a clean process.
- [ ] Add three concrete examples to the progress log: multi-intent, pasted chat safety, and missing-slot clarification.
- [ ] Explain aloud in five minutes why LLM confidence cannot authorize execution.

**Weekly gate:** intent output is typed, registry-controlled, testable without a live model, and the production V1 graph still works.

## Week 2 — Reliable Planning and Runtime Binding

### Day 6 — Planning contracts and DAG validation

- [ ] Execute Plan 2 Task 1.
- [ ] Validate unique steps, known dependencies, binding ancestry, and acyclicity.

**Daily evidence:** deterministic topological-order and cycle-rejection tests.

### Day 7 — Intent-specific compiler

- [ ] Execute Plan 2 Task 2.
- [ ] Compile person query, create, relationship, and kinship examples.
- [ ] Prove trusted `self_person_id` and stable idempotency keys enter the plan from code.

**Daily evidence:** readable task DAG and commit `feat: compile resolved intents into task DAGs`.

### Day 8 — Safe runtime result binding

- [ ] Execute Plan 2 Task 3.
- [ ] Bind `find_person.data.person_id` into a later Tool step.
- [ ] Reject undeclared or failed-source paths without `eval`, JSONPath, or attribute lookup.

**Daily evidence:** allowlist binding tests and commit `feat: bind trusted tool results into dependent steps`.

### Day 9 — Scheduler, branches, and bounded replanning

- [ ] Execute Plan 2 Task 4.
- [ ] Cover zero/one/multiple candidates, failed-parent descendant skipping, read batching, serialized writes, and repeated-plan rejection.

**Daily evidence:** scheduler branch table and commit `feat: schedule plans with bounded runtime branching`.

### Day 10 — Typed Tool contracts and Week 2 gate

- [ ] Execute Plan 2 Task 5.
- [ ] Adapt every exposed legacy Tool result to `ToolResult` and declare bindable outputs.
- [ ] Run the Plan 2 completion gate and demonstrate `find_person → get_person_detail`.

**Weekly gate:** a plan cannot execute arbitrary tools or paths, failed prerequisites cannot leak missing IDs downstream, and repeated writes keep one idempotency key.

## Week 3 — Memory, Clarification, and Recoverable Confirmation

### Day 11 — Context and entity resolution

- [ ] Execute Plan 3 Task 1.
- [ ] Implement unique recent-focus pronouns, same-name ambiguity, evidence, score gap, and candidate limits.

**Daily evidence:** “他” resolution and two-张三 clarification tests.

### Day 12 — Durable checkpoint and server-owned thread

- [ ] Execute Plan 3 Task 2.
- [ ] Reopen the SQLite checkpointer between interrupt and resume.
- [ ] Prove thread IDs are stable, opaque, and owner-specific.

**Daily evidence:** process-restart integration test and commit `feat: persist agent checkpoints with server-owned threads`.

### Day 13 — Clarification and confirmation lifecycle

- [ ] Execute Plan 3 Task 3.
- [ ] Reject wrong-owner, wrong-thread, wrong-operation, expired, reused, and non-boolean confirmations.
- [ ] Return minimal interrupt payloads.

**Daily evidence:** lifecycle matrix and commit `feat: add recoverable clarification and confirmation`.

### Day 14 — V2 graph integration

- [ ] Execute Plan 3 Task 4.
- [ ] Wire the full graph behind `AGENT_RUNTIME_VERSION=v2`.
- [ ] Keep V1 selectable and run the two-turn scenario suite.

**Daily evidence:** V1/V2 switch, graph scenario output, commit `feat: integrate memory-aware agent runtime graph`.

### Day 15 — Controlled memory writes and Week 3 gate

- [ ] Execute Plan 3 Task 5.
- [ ] Persist successful validated facts with provenance and supersession history.
- [ ] Prove failure, skip, rejection, or absent confirmation writes nothing.
- [ ] Run the Plan 3 completion gate.

**Weekly gate:** the Agent survives restart, can ask and resume, resolves cross-turn references conservatively, and only successful operations change long-term memory.

## Week 4 — Observability, Evaluation, and Interview Evidence

### Day 16 — Sanitized trace foundation

- [ ] Execute Plan 4 Task 1.
- [ ] Emit typed events for success and error paths while recursively redacting identity, secrets, phone numbers, and customer content.

**Daily evidence:** sanitizer tests and a collecting-sink trace.

### Day 17 — Versioned evaluation dataset

- [ ] Execute Plan 4 Task 2.
- [ ] Generate and validate all 200 synthetic cases, then manually inspect the 20 golden cases and a stratified sample from every generated category.

**Daily evidence:** dataset validation output and commit `test: add versioned agent evaluation dataset`.

### Day 18 — Stage graders and evaluation runner

- [ ] Execute Plan 4 Task 3.
- [ ] Generate fixture-mode JSON and Markdown reports with intent, slot, entity, policy, plan, runtime, latency, token, and cost fields.

**Daily evidence:** reproducible report artifacts and commit `feat: add reproducible stage-level agent evaluation`.

### Day 19 — Runtime instrumentation and release gate

- [ ] Execute Plan 4 Task 4.
- [ ] Add optional Langfuse export, V2 node timing, hard unsafe-execution failure, and initial quality thresholds.
- [ ] Prove tracing outages do not change Agent results.

**Daily evidence:** passing fixture gate and commit `feat: enforce observable agent quality gates`.

### Day 20 — Baseline, demo, and project evidence

- [ ] Execute Plan 4 Task 5.
- [ ] Generate one dated model-backed report without changing labels after seeing results.
- [ ] Rehearse the seven-step demo from a clean process.
- [ ] Run the Plan 4 completion gate and update progress with measured results.

**Final gate:** all tests pass, unsafe execution is zero, a model-backed report records exact versions, the demo is reproducible, and every résumé claim points to code, a test, a trace, or a metric.

## Progress Record Template

Append one entry per day to `docs/PROGRESS.md`:

```markdown
### YYYY-MM-DD — Day N: <goal>
- Completed:
- Test commands and results:
- Evidence or artifact:
- Design decision learned:
- Remaining failure or risk:
- Commit:
```

Do not mark a day complete from code volume. Mark it complete only when its named evidence exists and the daily regression command passes.
