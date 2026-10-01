"""回归：人物查找的两个分支都能为关系步骤提供真实 ID。"""

import unittest
from unittest.mock import patch

from backend.agent.execution_enums import ExecutionClaimAction
from backend.agent.nodes.execute_tools import (
    _resolve_and_validate_args,
    _runtime_args,
    execute_tools,
)
from backend.agent.tool_execution_repository import ExecutionClaim


PERSON_REF = {"$ref": {"step_id": "add_weishankai", "path": "data.id"}}


def _plan(*, relation_from="@self", relation_to=PERSON_REF):
    return [
        {
            "step_id": "find_weishankai",
            "call_id": "call_find",
            "tool": "find_person",
            "args": {"query": "魏山凯", "search_mode": "exact"},
            "depends_on": [],
        },
        {
            "step_id": "add_weishankai",
            "call_id": "call_add",
            "tool": "add_person",
            "args": {"name": "魏山凯"},
            "depends_on": ["find_weishankai"],
        },
        {
            "step_id": "add_friend_relation",
            "call_id": "call_relation",
            "tool": "add_relation",
            "args": {
                "from_person_id": relation_from,
                "to_person_id": relation_to,
                "relation_type": "朋友",
            },
            "depends_on": ["add_weishankai"],
        },
    ]


def _state(tool_calls):
    return {
        "user_id": "owner-1",
        "self_person_id": "p_self",
        "request_id": "request-1",
        "thread_id": "thread-1",
        "tool_calls": tool_calls,
        "tool_results": [],
        "execution_errors": [],
        "next_tool_index": 0,
    }


class RelationPlanExecutionTests(unittest.TestCase):
    def _execute(self, *, person_exists, tool_calls):
        invoked = []

        def fake_tool(tool_name, **kwargs):
            invoked.append((tool_name, kwargs))
            if tool_name == "find_person":
                if person_exists:
                    return {
                        "success": True,
                        "msg": "查询成功",
                        "data": {"id": "p_wei", "name": "魏山凯"},
                    }
                return {
                    "success": False,
                    "msg": "未找到人物",
                    "error_code": "NOT_FOUND",
                    "data": {"query": "魏山凯"},
                }
            if tool_name == "add_person":
                return {
                    "success": True,
                    "msg": "新增成功",
                    "data": {"id": "p_wei", "name": "魏山凯"},
                }
            if tool_name == "add_relation":
                valid = (
                    kwargs["from_person_id"] == "p_self"
                    and kwargs["to_person_id"] == "p_wei"
                )
                return {
                    "success": valid,
                    "msg": "关系创建成功" if valid else "人物 ID 错误",
                    "data": {"relation_id": "r_friend"} if valid else {},
                    **({} if valid else {"error_code": "INVALID_ARGUMENT"}),
                }
            raise AssertionError(f"意外执行 Tool：{tool_name}")

        with (
            patch(
                "backend.agent.nodes.execute_tools.claim_execution",
                return_value=ExecutionClaim(
                    ExecutionClaimAction.EXECUTE, execution_token="token-1"
                ),
            ),
            patch("backend.agent.nodes.execute_tools.mark_success"),
            patch("backend.agent.nodes.execute_tools.mark_failed"),
            patch("backend.agent.nodes.execute_tools.execute_tool", side_effect=fake_tool),
        ):
            command = execute_tools(_state(tool_calls))
        return command.update, invoked

    def test_existing_person_satisfies_add_step_without_duplicate_write(self):
        update, invoked = self._execute(person_exists=True, tool_calls=_plan())

        self.assertEqual(
            [record["status"] for record in update["tool_results"]],
            ["SUCCESS", "RESOLVED", "SUCCESS"],
        )
        self.assertEqual([name for name, _ in invoked], ["find_person", "add_relation"])
        self.assertEqual(update["execution_errors"], [])
        self.assertEqual(
            update["tool_calls"][2]["args"]["to_person_id"], "p_wei"
        )

    def test_new_person_then_relation_uses_authenticated_self_id(self):
        update, invoked = self._execute(person_exists=False, tool_calls=_plan())

        self.assertEqual(
            [record["status"] for record in update["tool_results"]],
            ["SUCCESS", "SUCCESS"],
        )
        self.assertEqual([name for name, _ in invoked[-2:]], ["add_person", "add_relation"])
        self.assertEqual(update["tool_calls"][2]["args"]["from_person_id"], "p_self")
        self.assertEqual(update["tool_calls"][2]["args"]["to_person_id"], "p_wei")
        self.assertEqual(update["execution_errors"], [])

    def test_legacy_reversed_friend_plan_uses_target_person_not_self(self):
        update, _ = self._execute(
            person_exists=True,
            tool_calls=_plan(
                relation_from=PERSON_REF,
                relation_to="self_person_id",
            ),
        )

        self.assertEqual(update["tool_results"][-1]["status"], "SUCCESS")
        self.assertEqual(update["tool_calls"][2]["args"]["from_person_id"], "p_self")
        self.assertEqual(update["tool_calls"][2]["args"]["to_person_id"], "p_wei")

    def test_relation_between_other_people_keeps_its_planned_direction(self):
        call = {
            "tool": "add_relation",
            "call_id": "call_other_people",
            "args": {
                "from_person_id": "p_alice",
                "to_person_id": "p_bob",
                "relation_type": "朋友",
            },
        }
        state = _state([call])

        resolved = _resolve_and_validate_args(state, call, {})
        runtime = _runtime_args(state, call, resolved)

        self.assertEqual(runtime["from_person_id"], "p_alice")
        self.assertEqual(runtime["to_person_id"], "p_bob")


if __name__ == "__main__":
    unittest.main()
