import unittest

from backend.db import neo4j_client
from backend.tools.person import delete_person as delete_person_tool


class FakeResult:
    def __init__(self, single=None, rows=None):
        self._single = single
        self._rows = rows or []

    def single(self):
        return self._single

    def data(self):
        return self._rows


class FakeSession:
    def __init__(self, owner_id="owner-a", is_self=False):
        self.owner_id = owner_id
        self.is_self = is_self
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, query, **params):
        self.calls.append((query, params))
        normalized = " ".join(query.split())

        if "OPTIONAL MATCH" in normalized:
            if params.get("owner_id") != self.owner_id:
                return FakeResult()
            return FakeResult(
                {
                    "person": {
                        "id": params["person_id"],
                        "name": "本人",
                        "is_self": self.is_self,
                    },
                    "relations": [],
                }
            )

        if "SET p.name" in normalized:
            return FakeResult({"person": {"id": params["id"], "name": params["name"]}})

        if "DETACH DELETE" in normalized:
            return FakeResult({"deleted": 1})

        if "RETURN a.id AS from_id" in normalized:
            if params.get("owner_id") != self.owner_id:
                return FakeResult()
            return FakeResult(
                {
                    "from_id": "p_1",
                    "to_id": "p_2",
                    "type": "朋友",
                    "owner": self.owner_id,
                    "through": None,
                    "note": None,
                }
            )

        if "RETURN r.id AS relation_id" in normalized:
            return FakeResult(rows=[])

        return FakeResult()


class FakeDriver:
    def __init__(self, owner_id="owner-a", is_self=False):
        self.fake_session = FakeSession(owner_id, is_self)

    def session(self):
        return self.fake_session


class OwnerScopingTests(unittest.TestCase):
    def test_delete_tool_requires_a_real_boolean_confirmation(self):
        for unconfirmed in (None, False, "false", "yes", 1):
            with self.subTest(unconfirmed=unconfirmed):
                result = delete_person_tool(
                    person_id="p_1",
                    owner_id="owner-a",
                    confirmed=unconfirmed,
                )
                self.assertFalse(result["success"])
                self.assertTrue(result["needs_confirmation"])

    def test_update_person_requires_matching_owner(self):
        driver = FakeDriver()

        updated = neo4j_client.update_person(driver, "p_1", {"name": "新名字"}, "owner-a")
        self.assertEqual(updated["name"], "新名字")

        with self.assertRaises(ValueError):
            neo4j_client.update_person(driver, "p_1", {"name": "越权修改"}, "owner-b")

        update_query, update_params = next(
            (query, params)
            for query, params in driver.fake_session.calls
            if "SET p.name" in " ".join(query.split())
        )
        self.assertIn("p.owner_id = $owner_id", " ".join(update_query.split()))
        self.assertEqual(update_params["owner_id"], "owner-a")

    def test_delete_person_scopes_match_to_owner(self):
        driver = FakeDriver()

        self.assertTrue(neo4j_client.delete_person(driver, "p_1", "owner-a"))

        delete_query, params = next(
            (query, params)
            for query, params in driver.fake_session.calls
            if "DETACH DELETE" in " ".join(query.split())
        )
        self.assertIn("owner_id: $owner_id", " ".join(delete_query.split()))
        self.assertEqual(params["owner_id"], "owner-a")

    def test_delete_person_rejects_the_bound_self_node(self):
        driver = FakeDriver(is_self=True)

        with self.assertRaisesRegex(ValueError, "不能删除"):
            neo4j_client.delete_person(driver, "p_self", "owner-a")

        self.assertFalse(
            any(
                "DETACH DELETE" in " ".join(query.split())
                for query, _params in driver.fake_session.calls
            )
        )

    def test_relation_read_and_list_are_owner_scoped(self):
        driver = FakeDriver()

        relation = neo4j_client.get_relation_by_id(driver, "r_1", "owner-a")
        self.assertEqual(relation["owner"], "owner-a")
        self.assertIsNone(neo4j_client.get_relation_by_id(driver, "r_1", "owner-b"))

        neo4j_client.list_person_relations(driver, "p_1", "owner-a")
        list_query, params = driver.fake_session.calls[-1]
        normalized = " ".join(list_query.split())
        self.assertIn("p.owner_id = $owner_id", normalized)
        self.assertIn("other.owner_id = $owner_id", normalized)
        self.assertEqual(params["owner_id"], "owner-a")


if __name__ == "__main__":
    unittest.main()
