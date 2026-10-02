# ============================================================
# Neo4j 连接管理
# ============================================================
import hashlib

from neo4j import GraphDatabase, Driver, Query
from backend.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from backend.common import common_tool
from backend.db.neo4j_operations import (
    canonical_json, execute_operation, ensure_schema,
    OperationNotFound, OperationPermissionDenied,
)

ALLOWED_FIELDS_CN = {"name":"姓名", "gender":"性别", "birth_year":"出生年月", "occupation":"职业","education":"学历", "hobbies":"爱好", "personality":"性格", "tags":"标签", "note":"备注"}

# 动态生成更新CQL
def build_update_cql(properties: dict) -> str:
    set_clauses = [
        f"p.{field} = ${field}"
        for field in ALLOWED_FIELDS_CN.keys()
        if properties is None or field in properties
    ]
    return  ", ".join(set_clauses)

# 动态生成新增CQL
def build_create_cql() -> str:
    set_clauses = [
        f"{field}: ${field}"
        for field in ALLOWED_FIELDS_CN.keys()
    ]
    return  ",".join(set_clauses)

COMMON_PERSON_FIELD_NAME = f"""
    id: $id,
    owner_id: $owner_id,
    created_at: datetime(),{build_create_cql()}
"""

COMMON_PERSON_RETURN_FIELD_NAME = ".id, ." + ", .".join(ALLOWED_FIELDS_CN.keys())

def get_neo4j_driver() -> Driver:
    """
    创建 Neo4j 驱动实例。
    使用完毕后调用 driver.close() 释放资源。
    """
    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
    )
    # 验证连接
    driver.verify_connectivity()
    return driver


# ============================================================
# TO-DO: 你来实现这些 Cypher 操作函数
# 每个函数签名+docstring 已经写好，你只需要实现函数体
# ============================================================

def _owned_person(tx, person_id: str, owner_id: str) -> dict:
    """事务内读取本人保护标志；所有查询都以服务端 owner_id 隔离。"""
    record = tx.run(
        """
        OPTIONAL MATCH (p:Person {id: $person_id})
        WHERE p.owner_id = $owner_id
        RETURN p {.*} AS person
        """, person_id=person_id, owner_id=owner_id,
    ).single()
    if record is None or record["person"] is None:
        raise OperationNotFound(f"人物不存在或无权修改: {person_id}")
    return dict(record["person"])


def create_person(driver: Driver, properties: dict, owner_id: str,
                  idempotency_key: str) -> dict:
    """创建人物和独立业务回执；人物后来被删除也不会因旧请求复活。"""
    if not idempotency_key:
        raise ValueError("create_person — 缺少 idempotency_key")
    defaults = {"name": "", "gender": "未知", "hobbies": [], "tags": []}
    # 对实际写入的完整默认值做摘要，相同含义的省略字段可稳定重放。
    values = {field: properties.get(field, defaults.get(field))
              for field in ALLOWED_FIELDS_CN}
    person_id = common_tool.generate_prefix_uuid("p_")
    # 旧版本用原始 key 建立唯一属性；新版本回执按 owner+key 隔离。
    # 新节点使用带版本标记的 owner/key 摘要，避免不同用户复用原始 key 时
    # 被旧的全局人物约束误判冲突。旧节点仍按原始 key 和 owner 一起查找。
    operation_person_key = "receipt:v2:" + hashlib.sha256(
        canonical_json([owner_id, idempotency_key]).encode("utf-8")
    ).hexdigest()

    def write(tx):
        # 旧节点没有原始参数摘要或历史结果，升级只能接纳其当前快照并补回执；
        # 不能追溯验证旧请求最初的参数。补建后所有重放都受新摘要约束保护。
        # 复用旧节点时不执行 SET，尤其不能覆盖升级之前后续请求修改的字段。
        record = tx.run(
            f"""
            OPTIONAL MATCH (legacy:Person {{created_by_operation: $idempotency_key}})
            WHERE legacy.owner_id = $owner_id
            WITH legacy
            CALL {{
                WITH legacy
                WITH legacy WHERE legacy IS NOT NULL
                RETURN legacy AS p
                UNION
                WITH legacy
                WITH legacy WHERE legacy IS NULL
                MERGE (p:Person {{created_by_operation: $operation_person_key}})
                ON CREATE SET p.id = $id, p.owner_id = $owner_id,
                              p.created_at = datetime(),
                              {build_update_cql(None)}
                RETURN p
            }}
            WITH p WHERE p.owner_id = $owner_id
            RETURN p {{{COMMON_PERSON_RETURN_FIELD_NAME}}} AS person
            """, id=person_id, owner_id=owner_id, idempotency_key=idempotency_key,
            operation_person_key=operation_person_key, **values,
        ).single()
        if record is None:
            raise RuntimeError("创建人物未返回业务结果")
        return dict(record["person"])

    return execute_operation(driver, owner_id, idempotency_key, "add_person", values, write)


def update_person(driver: Driver, person_id: str, properties: dict, owner_id: str,
                  idempotency_key: str | None = None) -> dict:
    """只写本次补丁；重试读取历史回执，绝不覆盖之后其他请求修改的字段。"""
    allowed = {key: value for key, value in properties.items() if key in ALLOWED_FIELDS_CN}

    def write(tx):
        current = _owned_person(tx, person_id, owner_id)
        if not allowed:
            return {key: current.get(key) for key in ("id", *ALLOWED_FIELDS_CN)}
        record = tx.run(
            f"""
            MATCH (p:Person {{id: $id}})
            WHERE p.owner_id = $owner_id
            SET {build_update_cql(allowed)}
            RETURN p {{{COMMON_PERSON_RETURN_FIELD_NAME}}} AS person
            """, id=person_id, owner_id=owner_id, **allowed,
        ).single()
        if record is None:
            raise OperationNotFound(f"人物不存在或无权修改: {person_id}")
        return dict(record["person"])

    return execute_operation(driver, owner_id, idempotency_key, "update_person",
                             {"person_id": person_id, "properties": allowed}, write)


def delete_person(driver: Driver, person_id: str, owner_id: str,
                  idempotency_key: str | None = None) -> bool:
    """删除人物和边，但保留 BusinessOperation 回执以支持删除后的重放。"""
    def write(tx):
        person = _owned_person(tx, person_id, owner_id)
        if person.get("is_self"):
            raise OperationPermissionDenied("不能删除当前账号绑定的本人节点")
        record = tx.run(
            """
            MATCH (p:Person {id: $id, owner_id: $owner_id})
            DETACH DELETE p
            RETURN count(p) AS deleted
            """, id=person_id, owner_id=owner_id,
        ).single()
        if record is None or record["deleted"] == 0:
            raise OperationNotFound(f"人物不存在或无权删除: {person_id}")
        return True

    return execute_operation(driver, owner_id, idempotency_key, "delete_person",
                             {"person_id": person_id}, write)


def find_person_exact(driver: Driver, name: str, owner_id: str) -> dict | None:
    """
    精确查找人物（按姓名）。

    Args:
        driver: Neo4j 驱动
        name: 精确姓名
        owner_id: 当前用户 ID

    Returns:
        人物信息 dict，如果没找到返回 None
    """
    with driver.session() as session:
        result = session.run(
            f"""
            MATCH (p:Person)
            WHERE p.name = $name and p.owner_id = $owner_id
            RETURN p {{{COMMON_PERSON_RETURN_FIELD_NAME}}} AS person
            """
            ,
            name = name,
            owner_id = owner_id)
        result_data = result.single()
        if result_data is None:
            return None
        else:
            return result_data["person"]


def find_person_fuzzy(driver: Driver, query: str, owner_id: str, limit: int = 5) -> list[dict]:
    """
    模糊查找人物（按姓名模糊匹配）。

    Args:
        driver: Neo4j 驱动
        query: 搜索关键词
        owner_id: 当前用户 ID
        limit: 返回数量上限

    Returns:
        匹配的人物列表，按相似度排序
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 用 Cypher 的 CONTAINS 或 =~ 正则做模糊匹配
    # 2. 比如 "小张" 应该能匹配到 "张伟"
    # 3. 只返回当前用户的关系网中的人

    with driver.session() as session:
        result = session.run(
            f"""
            MATCH (p:Person)
            WHERE p.name CONTAINS $search_term AND p.owner_id = $owner_id
            RETURN p {{{COMMON_PERSON_RETURN_FIELD_NAME}}} AS person
            ORDER BY p.name
            LIMIT $limit
            """,
            search_term = query,
            owner_id = owner_id,
            limit = limit)
        return [record["person"] for record in result]


def add_relation(driver: Driver, from_person_id: str, to_person_id: str,
                 relation_type: str, owner_id: str, through_person_id: str | None = None,
                 note: str | None = None, idempotency_key: str | None = None) -> dict:
    """只在首次事务内创建关系；同一 key 的并发重试拿到同一个 relation_id。"""
    rel_id = common_tool.generate_prefix_uuid("r_")
    arguments = {"from_person_id": from_person_id, "to_person_id": to_person_id,
                 "relation_type": relation_type, "through_person_id": through_person_id,
                 "note": note}

    def write(tx):
        if through_person_id is not None:
            _owned_person(tx, through_person_id, owner_id)
        record = tx.run(
            """
            MATCH (a:Person {id: $from_person_id, owner_id: $owner_id}),
                  (b:Person {id: $to_person_id, owner_id: $owner_id})
            CREATE (a)-[:RELATION {
                id: $rel_id, type: $type, owner: $owner_id,
                through: $through, note: $note, created_at: datetime()
            }]->(b)
            RETURN a.name AS from_name, b.name AS to_name, $rel_id AS relation_id
            """, from_person_id=from_person_id, to_person_id=to_person_id,
            rel_id=rel_id, type=relation_type, owner_id=owner_id,
            through=through_person_id, note=note,
        ).single()
        if record is None:
            raise OperationNotFound("关系端点人物不存在或无权访问")
        # Record 直接进入 tool_response 会被当成非 JSON 数据；保留原有三个
        # 字段名称，但立即转成普通 dict，后续回执和 Agent 引用都使用该结构。
        return dict(record)

    return execute_operation(driver, owner_id, idempotency_key, "add_relation", arguments, write)


def delete_relation(driver: Driver, relation_id: str, owner_id: str,
                    idempotency_key: str | None = None) -> bool:
    """删除边与记录成功回执同事务提交，重复删除不再依赖边的存在。"""
    def write(tx):
        record = tx.run(
            """MATCH (a:Person)-[r:RELATION {id: $id}]->(b:Person)
               WHERE r.owner = $owner_id
                 AND a.owner_id = $owner_id AND b.owner_id = $owner_id
               DELETE r
               RETURN count(r) AS deleted""",
            id=relation_id, owner_id=owner_id,
        ).single()
        if record is None or record["deleted"] == 0:
            raise OperationNotFound(f"关系不存在或无权删除: {relation_id}")
        return True

    return execute_operation(driver, owner_id, idempotency_key, "delete_relation",
                             {"relation_id": relation_id}, write)


def update_relation(driver: Driver, relation_id: str, new_type: str | None,
                    owner_id: str, new_note: str | None, new_through: str | None,
                    idempotency_key: str | None = None) -> bool:
    """部分修改在事务内合并；摘要只绑定请求补丁，不能绑定重试时的新旧值。"""
    def write(tx):
        if new_through is not None:
            _owned_person(tx, new_through, owner_id)
        record = tx.run(
            """MATCH (a:Person)-[r:RELATION {id: $id}]->(b:Person)
            WHERE r.owner = $owner_id
              AND a.owner_id = $owner_id AND b.owner_id = $owner_id
            SET r.type = coalesce($new_type, r.type),
                r.note = coalesce($new_note, r.note),
                r.through = coalesce($new_through, r.through)
            RETURN count(r) AS updated""", id=relation_id, owner_id=owner_id,
            new_type=new_type, new_note=new_note, new_through=new_through,
        ).single()
        if record is None or record["updated"] == 0:
            raise OperationNotFound(f"关系不存在或无权修改: {relation_id}")
        return True

    return execute_operation(driver, owner_id, idempotency_key, "update_relation",
                             {"relation_id": relation_id, "new_type": new_type,
                              "new_note": new_note, "new_through": new_through}, write)


def get_relation_by_id(driver: Driver, relation_id: str, owner_id: str) -> dict | None:
    # 1. 查出旧边信息（拿到 from / to / 旧属性）
    with driver.session() as session:
        old = session.run(
            """MATCH (a:Person)-[r:RELATION {id: $rid}]->(b:Person)
               WHERE r.owner = $owner_id
                 AND a.owner_id = $owner_id
                 AND b.owner_id = $owner_id
               RETURN a.id AS from_id, a.name AS from_name,b.id AS to_id,b.name AS to_name,
                      r.type AS type, r.owner AS owner,
                      r.through AS through, r.note AS note""",
            rid=relation_id,
            owner_id=owner_id,
        ).single()
        if old is None:
            return None
        return old.data() if hasattr(old, "data") else dict(old)


def get_relation_path(
    driver: Driver, from_person_id: str, to_person_id: str,owner_id: str, max_depth: int = 7
) -> list[dict] | None:
    """
    查询两个人之间的最短关系路径。

    Args:
        driver: Neo4j 驱动
        from_person_id: 起点人物 ID（通常是当前用户）
        to_person_id: 终点人物 ID
        owner_id: 所属人物id，数据隔离
        max_depth: 最大搜索深度（跳数）

    Returns:
        路径列表，每一步包含 {from, to, relation_type, step}
        如果找不到路径返回 None

    实例：查询“我->三叔“
    [
        {"step": 1, "from": "我",  "to": "张大", "relation": "父亲", "note": "我爸"},
        {"step": 2, "from": "张大", "to": "张三", "relation": "弟弟", "note": None},
    ]
    """
    with driver.session() as session:
        result = session.run(
            """
            MATCH path = shortestPath(
                (a:Person {id: $from_id})-[r:RELATION*1..""" + str(max_depth) + """]-(b:Person {id: $to_id})
            )
            WHERE a.owner_id = $owner_id
              AND b.owner_id = $owner_id
              AND ALL(node IN nodes(path) WHERE node.owner_id = $owner_id)
              AND ALL(rel IN relationships(path) WHERE rel.owner = $owner_id)
            RETURN path
            """,
            from_id=from_person_id,
            to_id=to_person_id,
            owner_id = owner_id
        )

        record = result.single()
        if record is None:
            return None

        # 解析路径：path 里有交替的节点和关系
        # 结构：node1 → rel1 → node2 → rel2 → node3 → ...
        path = record["path"]
        steps = []

        for i, rel in enumerate(path.relationships):
            forward = (path.nodes[i]["id"] == rel.start_node["id"])
            steps.append({
                "step": i + 1,
                "from": path.nodes[i]["name"],
                "to": path.nodes[i + 1]["name"],
                "relation": rel["type"],
                "direction": "forward" if forward else "reverse",
                "note": rel.get("note"),
            })

        return steps


def get_person_detail(driver: Driver, person_id: str,owner_id: str) -> dict | None:
    """
    获取一个人的完整信息：属性 + 所有直接关系。

    Args:
        driver: Neo4j 驱动
        person_id: 人物 ID
        owner_id: 所属ID，数据隔离

    Returns:
        {
            "person": {...},
            "relations": [{"to": {...}, "type": "朋友", "note": "..."}, ...]
        }
    """
    with driver.session() as session:
        check = session.run(
            """
            MATCH (p:Person {id: $person_id})
            WHERE p.owner_id = $owner_id
            OPTIONAL MATCH (p)-[r:RELATION]-(other:Person)
            WHERE r.owner = $owner_id AND other.owner_id = $owner_id
            RETURN p {.*} AS person,
               [rel IN collect(DISTINCT {
                   relation_id: r.id, type: r.type, note: r.note,
                   through: r.through, other_name: other.name, other_id: other.id
               }) WHERE rel.relation_id IS NOT NULL] AS relations
            """
            ,
            person_id=person_id,owner_id=owner_id)
        check_result = check.single()
        if check_result is None:
            return None
        else:
            return {"person": check_result["person"], "relations": check_result["relations"]}

def list_person_relations(
    driver: Driver, person_id: str, owner_id: str, relation_type: str | None = None):
    """
    列出某人的所有直接关系。

    Args:
        driver: Neo4j 驱动
        person_id: 人物 ID
        relation_type: 可选筛选，只返回特定类型

    Returns:
        关系列表
    """
    # 根据是否传了 relation_type，选不同的 Cypher
    if relation_type:
        cypher = """
                MATCH (p:Person {id: $id})-[r:RELATION {type: $type}]-(other:Person)
                WHERE p.owner_id = $owner_id
                  AND other.owner_id = $owner_id
                  AND r.owner = $owner_id
                RETURN r.id AS relation_id, r.type AS type,
                       other.name AS person_name, other.id AS person_id,
                       r.note AS note
            """
        params = {"id": person_id, "type": relation_type, "owner_id": owner_id}
    else:
        cypher = """
                MATCH (p:Person {id: $id})-[r:RELATION]-(other:Person)
                WHERE p.owner_id = $owner_id
                  AND other.owner_id = $owner_id
                  AND r.owner = $owner_id
                RETURN r.id AS relation_id, r.type AS type,
                       other.name AS person_name, other.id AS person_id,
                       r.note AS note
            """
        params = {"id": person_id,"owner_id":owner_id}

    with driver.session() as session:
        result = session.run(cypher, **params)
        return result.data()  # data() 返回所有行，每行是一个 dict


def person_exists(driver: Driver, person_id: str, owner_id: str) -> bool:
    """检查人物是否存在。"""
    return get_person_detail(driver, person_id, owner_id) is not None

if __name__ == "__main__":
    driver = get_neo4j_driver()
    owner_id = "1"

    #p_47a7b055
    # map = dict()
    # map["name"] = "朱珠"
    # map["gender"] = "女"
    # map["birth_year"] = "1998-01-05"
    # map["occupation"] = "上市公司CEO"
    # map["education"] = "本科"
    # map["hobbies"] = ["电视剧","游泳"]
    # map["personality"] = "自信,随和"
    # map["tags"] = ["年轻","漂亮"]
    # map["note"] = ""
    # create_person(driver , map , "1")

    # map = dict()
    # map["occupation"] = "CEO"
    # map["education"] = "研究生"
    # update_person(driver  ,"p_8b8b9e1a" , map)

    # delete_person(driver , "p_8b8b9e1a")

    #p_a8423730
    # map = dict()
    # map["name"] = "林志达"
    # map["gender"] = "男"
    # map["birth_year"] = "1998-08-05"
    # map["occupation"] = "程序员"
    # map["education"] = "本科"
    # map["hobbies"] = ["篮球", "电子游戏"]
    # map["personality"] = "幽默,随和"
    # map["tags"] = ["帅气", "强壮"]
    # map["note"] = ""
    # create_person(driver, map, "1")

    # p_65d18bce
    # map = dict()
    # map["name"] = "段汶狄"
    # map["gender"] = "男"
    # map["birth_year"] = "1998-05-15"
    # map["occupation"] = "高级程序员"
    # map["education"] = "本科"
    # map["hobbies"] = ["羽毛球","健身"]
    # map["personality"] = "幽默,随和"
    # map["tags"] = ["强壮","精干"]
    # map["note"] = ""
    # create_person(driver , map , "1")

    #p_e2997a7d
    # map = dict()
    # map["name"] = "林建强"
    # map["gender"] = "男"
    # map["birth_year"] = "1960-09-15"
    # map["occupation"] = "个体户"
    # map["education"] = "高中"
    # map["hobbies"] = ["钓鱼","开车"]
    # map["personality"] = "善良,随和"
    # map["tags"] = ["老实","文化人"]
    # map["note"] = ""
    # create_person(driver , map , "1")

    #p_e39afa55
    # map = dict()
    # map["name"] = "林建伟"
    # map["gender"] = "男"
    # map["birth_year"] = "1963-10-25"
    # map["occupation"] = "厂长"
    # map["education"] = "高中"
    # map["hobbies"] = ["摄影", "旅游"]
    # map["personality"] = "传统"
    # map["tags"] = ["商人", "精明"]
    # map["note"] = ""
    # create_person(driver, map, "1")

    #p_c96a3986
    # map = dict()
    # map["name"] = "林翰星"
    # map["gender"] = "男"
    # map["birth_year"] = "1997-09-25"
    # map["occupation"] = "个体"
    # map["education"] = "高中"
    # map["hobbies"] = ["赚钱", "旅游"]
    # map["personality"] = "传统"
    # map["tags"] = ["规矩", "勤勉"]
    # map["note"] = ""
    # create_person(driver, map, "1")

    # 建立关系
    # print(add_relation(driver , "p_47a7b055" , "p_a8423730" , "夫妻",owner_id))
    # print(add_relation(driver, "p_47a7b055", "p_65d18bce", "弟弟", owner_id))

    # print(add_relation(driver, "p_a8423730", "p_e2997a7d", "父亲", owner_id))
    # print(add_relation(driver, "p_e2997a7d", "p_e39afa55", "弟弟", owner_id))
    # print(add_relation(driver, "p_e39afa55", "p_c96a3986", "儿子", owner_id))

    #查询关系
    # print(find_person_exact(driver , "朱",owner_id))
    # print(find_person_fuzzy(driver, "朱", owner_id , 1))
    # print(get_relation_path(driver, "p_65d18bce","p_a8423730",5))
    # print(get_relation_path(driver, "p_a8423730", "p_65d18bce", 5))

    # print(get_relation_path(driver, "p_47a7b055", "p_c96a3986", 5))
