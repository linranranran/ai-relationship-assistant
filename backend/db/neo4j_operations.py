"""Neo4j 业务写入回执：业务变更与去重凭据必须在同一事务提交。"""

import hashlib
import json
from collections.abc import Callable
from typing import Any


class OperationConflict(ValueError):
    """相同用户的幂等键被用于不同操作或不同业务参数。"""


class OperationNotFound(ValueError):
    """目标不存在或不属于当前用户；不泄露其他用户数据是否存在。"""


class OperationPermissionDenied(ValueError):
    """禁止改变账号绑定的本人等受保护业务对象。"""


RECEIPT_CONSTRAINT = """
CREATE CONSTRAINT business_operation_owner_key_unique IF NOT EXISTS
FOR (operation:BusinessOperation)
REQUIRE (operation.owner_id, operation.idempotency_key) IS UNIQUE
"""

LEGACY_PERSON_CONSTRAINT = """
CREATE CONSTRAINT person_created_by_operation_unique IF NOT EXISTS
FOR (person:Person)
REQUIRE person.created_by_operation IS UNIQUE
"""


def ensure_schema(driver) -> None:
    """可在启动时调用；IF NOT EXISTS 也允许多个进程并发初始化。

    唯一约束是并发 MERGE 的前提，不能仅靠 Python 锁或“先查再写”。
    DDL 在独立事务中完成，避免把 schema 变更混进业务写事务。
    初始化失败时向上抛错，禁止降级成没有唯一约束的幂等写入。
    """
    with driver.session() as session:
        # 升级时仍要支持旧版本已写入人物、但尚未返回调用成功的请求。
        # 新版本复用旧节点时，同样需要旧 created_by_operation 的唯一约束。
        session.run(LEGACY_PERSON_CONSTRAINT).consume()
        session.run(RECEIPT_CONSTRAINT).consume()


def canonical_json(value: Any) -> str:
    """固定字典顺序与空白；列表顺序仍属于业务参数，不能随意排序。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def execute_operation(driver, owner_id: str, idempotency_key: str | None,
                      operation: str, arguments: dict, write: Callable) -> Any:
    """返回首次提交的业务结果；回执不依赖业务节点或关系是否仍然存在。

    兼容旧的直接调用：缺少 key 时仍可执行，但不宣称跨调用去重。
    Agent 路径必须由服务端注入 key，不能让模型或浏览器自行选择。
    execute_write 可能重跑回调，所以回调只能访问 Neo4j，不能调用
    embedding、Chroma 或其他外部服务。事务回滚时业务和回执一起回滚。
    """
    if not owner_id:
        raise ValueError("业务写入缺少 owner_id")
    if idempotency_key is not None and (
        not isinstance(idempotency_key, str) or not idempotency_key.strip()
    ):
        raise ValueError("idempotency_key 必须是非空字符串")
    arguments_hash = hashlib.sha256(canonical_json(arguments).encode("utf-8")).hexdigest()
    if idempotency_key is not None:
        ensure_schema(driver)

    def commit(tx):
        if idempotency_key is None:
            return write(tx)
        # MERGE 依靠复合唯一约束确定唯一回执；SET 的读改写拿到节点写锁。
        # 已有回执同样需要这把锁，否则两个进程可能同时看到空结果并写业务。
        # 锁一直持有到业务变更和 result_json 一起提交，失败则整个事务回滚。
        receipt = tx.run(
            """
            MERGE (operation:BusinessOperation {
                owner_id: $owner_id, idempotency_key: $idempotency_key
            })
            ON CREATE SET operation.operation = $operation,
                          operation.arguments_hash = $arguments_hash,
                          operation.created_at = datetime()
            SET operation.lock_version = coalesce(operation.lock_version, 0) + 1
            RETURN operation.operation AS operation,
                   operation.arguments_hash AS arguments_hash,
                   operation.result_json AS result_json
            """, owner_id=owner_id, idempotency_key=idempotency_key,
            operation=operation, arguments_hash=arguments_hash,
        ).single()
        if receipt is None:
            raise RuntimeError("无法取得 Neo4j 业务操作回执")
        if receipt["operation"] != operation or receipt["arguments_hash"] != arguments_hash:
            raise OperationConflict("幂等键已绑定其他操作或参数，请使用原请求重试")
        if receipt["result_json"] is not None:
            # 必须先看回执，再查目标。删除后重放以及先更新后删除都可正常返回。
            return json.loads(receipt["result_json"])
        result = write(tx)
        snapshot = canonical_json(result)
        tx.run(
            """
            MATCH (operation:BusinessOperation {
                owner_id: $owner_id, idempotency_key: $idempotency_key
            })
            SET operation.result_json = $result_json,
                operation.completed_at = datetime()
            """, owner_id=owner_id, idempotency_key=idempotency_key,
            result_json=snapshot,
        ).consume()
        # 首次与重放都经过 JSON 往返，保证返回类型和内容完全一致。
        return json.loads(snapshot)

    with driver.session() as session:
        # 旧的无 key 调用和只实现 run 的本地适配器保留兼容；带 key 的
        # 路径绝不能走此分支，否则无法保证业务与回执原子提交。
        if idempotency_key is None and not hasattr(session, "execute_write"):
            return commit(session)
        return session.execute_write(commit)
