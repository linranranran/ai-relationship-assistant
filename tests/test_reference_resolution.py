"""会话人物焦点与歧义处理，不连接真实 LLM 或数据库。"""

import unittest

from backend.agent.memory.reference_resolution import derive_focus, resolve_reference
from backend.agent.nodes.ask_clarification import (
    _build_clarification_payload,
    _resume_after_reference_choice,
)
from backend.agent.nodes.plan_tasks import plan_tasks


def _found(step_id, person_id, name):
    return {
        "step_id": step_id, "tool": "find_person", "status": "SUCCESS",
        "result": {"success": True, "data": {"id": person_id, "name": name}},
    }


class ReferenceResolutionTests(unittest.TestCase):
    def test_single_verified_person_becomes_focus(self):
        focus, candidates = derive_focus(
            owner_id="a", conversation_id="default", request_id="r1",
            tool_results=[_found("find", "p1", "张建国")],
        )
        self.assertEqual(focus["person_id"], "p1")
        self.assertEqual(candidates, [{"person_id": "p1", "name": "张建国"}])

    def test_multiple_people_require_clarification(self):
        focus, candidates = derive_focus(
            owner_id="a", conversation_id="default", request_id="r1",
            tool_results=[_found("one", "p1", "张建国"), _found("two", "p2", "李四")],
        )
        self.assertIsNone(focus)
        self.assertEqual({item["person_id"] for item in candidates}, {"p1", "p2"})

    def test_pronoun_uses_only_same_owner_and_conversation_focus(self):
        base = {"user_id": "a", "conversation_id": "default", "user_input": "他的爱好是什么？"}
        focus = {"owner_id": "a", "conversation_id": "default", "person_id": "p1", "name": "张建国"}
        resolved = resolve_reference({**base, "person_focus": focus})
        self.assertEqual(resolved["status"], "RESOLVED")
        self.assertEqual(resolved["reference"]["person_id"], "p1")
        foreign = resolve_reference({**base, "person_focus": {**focus, "owner_id": "b"}})
        self.assertEqual(foreign["status"], "CLARIFY")

    def test_explicit_person_switch_does_not_reuse_old_focus(self):
        state = {
            "user_id": "a", "conversation_id": "default",
            "user_input": "李四的爱好是什么？",
            "person_focus": {"owner_id": "a", "conversation_id": "default", "person_id": "p1", "name": "张建国"},
        }
        self.assertEqual(resolve_reference(state)["status"], "SKIP")
        new_focus, _ = derive_focus(
            owner_id="a", conversation_id="default", request_id="r2",
            tool_results=[_found("find_li", "p2", "李四")],
        )
        self.assertEqual(new_focus["person_id"], "p2")

    def test_candidate_selection_resumes_with_checked_id(self):
        state = {
            "user_id": "a", "conversation_id": "default", "request_id": "r2",
            "pending_reference": {
                "owner_id": "a", "conversation_id": "default",
                "candidates": [{"person_id": "p1", "name": "张建国"},
                               {"person_id": "p2", "name": "李四"}],
            },
        }
        payload, _ = _build_clarification_payload(state)
        self.assertEqual(payload["clarification_type"], "CANDIDATE_SELECTION")
        command = _resume_after_reference_choice(state, {"candidate_id": "p2"})
        self.assertEqual(command.goto, "prepare_context")
        self.assertEqual(command.update["resolved_reference"]["person_id"], "p2")
        invalid = _resume_after_reference_choice(state, {"candidate_id": "p3"})
        self.assertNotEqual(invalid.goto, "prepare_context")

    def test_resolved_person_query_plans_exact_id_without_model(self):
        command = plan_tasks({
            "user_id": "a", "conversation_id": "default", "request_id": "r2",
            "intent": "QUERY_PERSON", "resolved_reference": {
                "owner_id": "a", "conversation_id": "default",
                "person_id": "p1", "name": "张建国",
            },
        })
        self.assertEqual(command.goto, "execute_tools")
        self.assertEqual(command.update["tool_calls"][0]["tool"], "get_person_detail")
        self.assertEqual(command.update["tool_calls"][0]["args"]["person_id"], "p1")

    def test_resolved_kinship_query_uses_id_and_path_dependency(self):
        command = plan_tasks({
            "user_id": "a", "conversation_id": "default", "request_id": "r3",
            "user_input": "他应该怎么称呼？", "intent": "QUERY_KINSHIP",
            "resolved_reference": {
                "owner_id": "a", "conversation_id": "default",
                "person_id": "p1", "name": "张建国",
            },
        })
        calls = command.update["tool_calls"]
        self.assertEqual([call["tool"] for call in calls], ["query_relation_path", "get_kinship_title"])
        self.assertEqual(calls[0]["args"]["target_person_id"], "p1")
        self.assertEqual(calls[1]["depends_on"], [calls[0]["step_id"]])


if __name__ == "__main__":
    unittest.main()
