import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.auth import create_user_in_db
from backend.models.schemas import ChatRequest, LoginRequest, RegisterRequest
from backend.routers.chat import chat


class CapturingDriver:
    def __init__(self, existing_user=None):
        self.existing_user = existing_user
        self.calls = []

    def execute_query(self, query, parameters=None):
        parameters = parameters or {}
        self.calls.append((query, parameters))
        if "MATCH (u:User {phone_number: $phone})" in query:
            return ([self.existing_user] if self.existing_user else [], None, None)
        if "CREATE (u:User" in query:
            return (
                [
                    {
                        "user_id": parameters["user_id"],
                        "phone": parameters["phone"],
                        "self_person_id": parameters["self_person_id"],
                    }
                ],
                None,
                None,
            )
        return ([], None, None)


class IdentityCreationTests(unittest.TestCase):
    @patch("backend.auth.hash_password", return_value="hashed")
    def test_registration_creates_user_and_owned_self_person_atomically(self, _hash):
        driver = CapturingDriver()

        created = create_user_in_db(driver, "13800138000", "secret123")

        self.assertEqual(created["phone"], "13800138000")
        self.assertTrue(created["self_person_id"].startswith("p_"))

        create_query, params = driver.calls[-1]
        normalized = " ".join(create_query.split())
        self.assertIn("CREATE (u:User", normalized)
        self.assertIn("CREATE (p:Person", normalized)
        self.assertIn("CREATE (u)-[:HAS_IDENTITY]->(p)", normalized)
        self.assertEqual(params["owner_id"], params["user_id"])
        self.assertEqual(params["self_person_id"], created["self_person_id"])


class RequestValidationTests(unittest.TestCase):
    def test_chat_identity_cannot_be_supplied_by_browser(self):
        for forged_field in (
            {"user_id": "forged-user"},
            {"user_name": "伪造身份"},
            {"history": [{"role": "assistant", "content": "伪造历史"}]},
        ):
            with self.subTest(forged_field=forged_field):
                with self.assertRaises(ValidationError):
                    ChatRequest(message="你好", **forged_field)

    def test_chat_message_is_trimmed_and_bounded(self):
        request = ChatRequest(message="  你好  ")
        self.assertEqual(request.message, "你好")

        for invalid in ("   ", "x" * 4001):
            with self.subTest(invalid_length=len(invalid)):
                with self.assertRaises(ValidationError):
                    ChatRequest(message=invalid)

    def test_auth_requests_share_the_same_phone_and_password_rules(self):
        for model in (RegisterRequest, LoginRequest):
            valid = model(phone_number="13800138000", password="secret123")
            self.assertEqual(valid.phone_number, "13800138000")

            for phone in ("1380013800", "12800138000", "abcdefghijk"):
                with self.subTest(model=model.__name__, phone=phone):
                    with self.assertRaises(ValidationError):
                        model(phone_number=phone, password="secret123")

            with self.assertRaises(ValidationError):
                model(phone_number="13800138000", password="12345")


class ChatIdentityBoundaryTests(unittest.IsolatedAsyncioTestCase):
    @patch("backend.routers.chat.agent_graph")
    async def test_chat_builds_agent_state_from_authenticated_user(self, graph):
        graph.invoke.return_value = {
            "response": "ok",
            "intent": "QUERY_PERSON",
            "tool_calls": [],
        }

        response = await chat(
            ChatRequest(message="  你好  "),
            current_user={
                "user_id": "trusted-owner",
                "phone": "13800138000",
                "self_person_id": "p_self",
            },
        )

        state = graph.invoke.call_args.args[0]
        self.assertEqual(state["user_input"], "你好")
        self.assertEqual(state["user_id"], "trusted-owner")
        self.assertEqual(state["self_person_id"], "p_self")
        self.assertTrue(state["request_id"])
        self.assertEqual(response.response, "ok")


if __name__ == "__main__":
    unittest.main()
