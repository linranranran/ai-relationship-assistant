"""LangGraph runtime compatibility gate.

This test module is deliberately small and independent from our business Agent.
Its job is to prove that the locked LangGraph version supports the two runtime
capabilities used by later plans:

1. persist graph state under a server-controlled ``thread_id``;
2. pause a graph with ``interrupt()`` and resume it with ``Command``.

When a future dependency upgrade breaks either capability, this test should fail
before relationship, sales, or memory code is blamed for the regression.
"""

from __future__ import annotations

import importlib.metadata
import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict


# Keep this value next to the compatibility test. Updating pyproject.toml alone
# must not silently bypass review of the runtime behavior below.
REVIEWED_LANGGRAPH_VERSION = "1.2.12"


class CompatState(TypedDict, total=False):
    """Smallest state needed to demonstrate pause and resume.

    Production state will contain intents, plans, Tool results, and pending
    confirmations. Those fields do not belong in this dependency-level test.
    """

    value: str
    approved: bool


def build_interruptible_graph():
    """Build a minimal graph using the same control flow as high-risk writes.

    Pseudocode for the later production node:

    - create a safe operation summary;
    - call ``interrupt(summary)`` before executing a write Tool;
    - persist the paused state through the graph checkpointer;
    - resume only with an authenticated answer on the same ``thread_id``.

    This compatibility graph intentionally stops before Tool execution. It tests
    LangGraph semantics without connecting to Neo4j, ChromaDB, or an LLM.
    """

    def request_approval(state: CompatState) -> dict[str, bool]:
        # The first invocation pauses here. On resume, interrupt() returns the
        # value carried by Command(resume=...), and the node continues normally.
        approved = interrupt({"value": state["value"]})
        return {"approved": approved is True}

    builder = StateGraph(CompatState)
    builder.add_node("request_approval", request_approval)
    builder.add_edge(START, "request_approval")
    builder.add_edge("request_approval", END)
    return builder.compile(checkpointer=InMemorySaver())


class LangGraphCompatibilityTests(unittest.TestCase):
    def test_project_uses_reviewed_langgraph_version(self):
        """Dependency updates require an explicit review of this gate."""

        installed = importlib.metadata.version("langgraph")
        self.assertEqual(installed, REVIEWED_LANGGRAPH_VERSION)

    def test_checkpoint_and_interrupt_resume(self):
        """A paused operation resumes from its checkpoint on the same thread."""

        app = build_interruptible_graph()
        config = {"configurable": {"thread_id": "compat-thread"}}

        paused = app.invoke({"value": "delete-person"}, config=config)
        self.assertIn("__interrupt__", paused)

        resumed = app.invoke(Command(resume=True), config=config)
        self.assertTrue(resumed["approved"])

    def test_resume_on_another_thread_does_not_reuse_paused_state(self):
        """Checkpoint state is isolated by thread_id.

        The authenticated HTTP layer will derive thread IDs server-side. This
        test protects the lower-level guarantee that a different thread cannot
        resume an operation paused elsewhere.
        """

        app = build_interruptible_graph()
        original = {"configurable": {"thread_id": "owner-a-thread"}}
        another = {"configurable": {"thread_id": "owner-b-thread"}}

        paused = app.invoke({"value": "delete-person"}, config=original)
        self.assertIn("__interrupt__", paused)

        # LangGraph does not promise a public exception message for resuming a
        # thread with no checkpoint. The stable contract is that it must fail
        # and must not consume the original thread's pending interrupt.
        with self.assertRaises(Exception):
            app.invoke(Command(resume=True), config=another)

        resumed = app.invoke(Command(resume=True), config=original)
        self.assertTrue(resumed["approved"])


if __name__ == "__main__":  # pragma: no cover - convenience for local study
    unittest.main()
