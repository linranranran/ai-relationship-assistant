// 业务对象与回执独立保存：删除 Person/RELATION 后仍可重放原操作的结果。
// 同一用户的一把 key 只能绑定一种操作和一份规范化参数摘要。
// 应先执行此约束，再启用业务写入；运行时 ensure_schema 也会安全初始化。
// 保留旧人物操作键约束，升级后的首次重放会复用已有旧节点并补建回执。
CREATE CONSTRAINT person_created_by_operation_unique IF NOT EXISTS
FOR (person:Person)
REQUIRE person.created_by_operation IS UNIQUE;

CREATE CONSTRAINT business_operation_owner_key_unique IF NOT EXISTS
FOR (operation:BusinessOperation)
REQUIRE (operation.owner_id, operation.idempotency_key) IS UNIQUE;
