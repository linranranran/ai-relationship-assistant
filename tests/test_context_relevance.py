"""上下文选择的关键行为，使用内存端口避免访问外部数据库。"""

import unittest

from backend.agent.memory.context_builder import build_context_bundle
from backend.agent.memory.models import (
    ConversationSummary,
    MemoryFact,
    MemoryFactStatus,
    MemoryMessage,
    MemoryRole,
    MemorySettings,
)
from backend.agent.memory.ports import NoOpMemoryJobStore
from backend.agent.memory.runtime import MemoryRuntime


class _Counter:
    def count_messages(self, messages):
        return sum(len(str(message["content"])) + 4 for message in messages)


class _MessageStore:
    persistent = True

    def __init__(self, messages):
        self.messages = tuple(messages)

    def load_recent(self, **kwargs):
        return self.messages[-kwargs["limit"] :]


class _SummaryStore:
    persistent = True

    def __init__(self, summary):
        self.summary = summary

    def load(self, **kwargs):
        return self.summary


class _FactStore:
    persistent = True

    def __init__(self, facts):
        self.facts = tuple(facts)
        self.last_search = None

    def search(self, **kwargs):
        self.last_search = kwargs
        return self.facts


def _message(message_id, request_id, role, content):
    return MemoryMessage(
        message_id=message_id,
        owner_id="owner-1",
        conversation_id="default",
        request_id=request_id,
        role=role,
        content=content,
    )


def _runtime(messages, summary=None, facts=(), *, budget=210):
    return MemoryRuntime(
        message_store=_MessageStore(messages),
        summary_store=_SummaryStore(summary),
        long_term_store=_FactStore(facts),
        job_store=NoOpMemoryJobStore(),
        token_counter=_Counter(),
        settings=MemorySettings(
            max_input_tokens=budget,
            reserved_output_tokens=0,
            safety_tokens=0,
            recent_message_limit=12,
            summary_trigger_ratio=0.7,
            memory_retrieval_limit=5,
        ),
    )


class ContextRelevanceTests(unittest.TestCase):
    def test_latest_complete_turn_survives_large_summary_and_fact(self):
        messages = [
            _message("u1", "r1", MemoryRole.USER, "张建国和我是什么关系？"),
            _message("a1", "r1", MemoryRole.ASSISTANT, "张建国是你的同事。"),
        ]
        summary = ConversationSummary(
            owner_id="owner-1",
            conversation_id="default",
            content={"user_goals": [{"text": "旧目标" * 35, "evidence_message_ids": ["old"]}]},
            covered_until_message_id="old",
        )
        fact = MemoryFact(
            fact_id="f1", owner_id="owner-1", person_id="p1", category="person_profile",
            value={"note": "旧资料" * 25}, status=MemoryFactStatus.ACTIVE, version=1,
        )
        bundle = build_context_bundle(
            owner_id="owner-1", conversation_id="default", current_input="他的爱好是什么？",
            runtime=_runtime(messages, summary, [fact], budget=250),
        )
        contents = [message["content"] for message in bundle.messages]
        self.assertIn("张建国和我是什么关系？", contents)
        self.assertIn("张建国是你的同事。", contents)
        self.assertEqual(bundle.stats.selected_message_ids, ["u1", "a1"])

    def test_orphan_message_is_not_sent_without_its_turn(self):
        messages = [
            _message("a0", "r0", MemoryRole.ASSISTANT, "缺失用户问题的回答"),
            _message("u1", "r1", MemoryRole.USER, "张建国是谁？"),
            _message("a1", "r1", MemoryRole.ASSISTANT, "张建国是你的同事。"),
        ]
        bundle = build_context_bundle(
            owner_id="owner-1", conversation_id="default", current_input="他的爱好是什么？",
            runtime=_runtime(messages),
        )
        self.assertNotIn("缺失用户问题的回答", [message["content"] for message in bundle.messages])

    def test_summary_item_covered_by_recent_turn_is_not_repeated(self):
        messages = [
            _message("u1", "r1", MemoryRole.USER, "张建国是我的同事"),
            _message("a1", "r1", MemoryRole.ASSISTANT, "已记录"),
        ]
        summary = ConversationSummary(
            owner_id="owner-1", conversation_id="default",
            content={
                "confirmed_relations": [
                    {"text": "张建国是我的同事", "evidence_message_ids": ["u1"]},
                    {"text": "李四是我的朋友", "evidence_message_ids": ["old"]},
                ]
            },
            covered_until_message_id="a1",
        )
        bundle = build_context_bundle(
            owner_id="owner-1", conversation_id="default", current_input="继续",
            runtime=_runtime(messages, summary, budget=500),
        )
        summary_messages = [m["content"] for m in bundle.messages if m.get("context_type") == "conversation_summary"]
        self.assertEqual(len(summary_messages), 1)
        self.assertIn("李四是我的朋友", summary_messages[0])
        self.assertNotIn("张建国是我的同事", summary_messages[0])

    def test_resolved_pronoun_retrieves_only_its_person_profile(self):
        runtime = _runtime([])
        build_context_bundle(
            owner_id="owner-1", conversation_id="default", current_input="他的爱好是什么？",
            runtime=runtime,
            resolved_reference={
                "person_id": "p1", "name": "张建国", "owner_id": "owner-1",
                "conversation_id": "default",
            },
        )
        self.assertEqual(runtime.long_term_store.last_search["person_id"], "p1")
        self.assertEqual(runtime.long_term_store.last_search["category"], "person_profile")


if __name__ == "__main__":
    unittest.main()
