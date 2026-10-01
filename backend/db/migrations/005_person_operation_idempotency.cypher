// 在 Neo4j Browser 或 cypher-shell 中执行。
// created_by_operation 的格式是 owner_id:call_id，由服务端生成。

CREATE CONSTRAINT person_created_by_operation_unique IF NOT EXISTS
FOR (p:Person)
REQUIRE p.created_by_operation IS UNIQUE;
