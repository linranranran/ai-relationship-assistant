"""人物 ID 已确定时，事实查询不得回退到全用户文本相似度。"""

import unittest
from unittest.mock import patch

from backend.db.conversation_memory_store import PostgresConversationMemoryStore


class _Connection:
    def __init__(self):
        self.sql = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return self

    def fetchall(self):
        return []


class PersonMemoryLookupTests(unittest.TestCase):
    def test_relation_category_uses_prefix_and_owner_person_filter(self):
        connection = _Connection()
        with patch("backend.db.conversation_memory_store.get_postgres_connection", return_value=connection):
            results = PostgresConversationMemoryStore().search(
                owner_id="owner-a", conversation_id="default", query="他和我是什么关系",
                person_id="person-1", category="relationship", limit=5,
            )
        self.assertEqual(results, ())
        self.assertIn("person_id = %s", connection.sql)
        self.assertIn("category LIKE %s", connection.sql)
        self.assertEqual(connection.params[:3], ("owner-a", "person-1", "relationship:%"))


if __name__ == "__main__":
    unittest.main()
