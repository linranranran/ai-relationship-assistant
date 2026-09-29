"""创建项目 PostgreSQL 数据库并执行基础 DDL。

运行方式：

    uv run python -m backend.db.init_postgres

脚本只读取 ``.env``，不会打印密码或完整连接串。创建数据库需要当前账号拥有
CREATEDB 权限；若云数据库限制该权限，请先在控制台创建数据库，再运行本脚本
创建表。
"""

from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.config import (
    POSTGRES_ADMIN_DATABASE,
    POSTGRES_DATABASE,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_POOL_TIMEOUT_SECONDS,
    POSTGRES_PORT,
    POSTGRES_USER,
)


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _conninfo(database: str) -> str:
    return make_conninfo(
        "",
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=database,
        connect_timeout=POSTGRES_POOL_TIMEOUT_SECONDS,
    )


def create_database_if_missing() -> bool:
    """创建目标数据库；返回 True 表示本次实际创建。"""
    with psycopg.connect(_conninfo(POSTGRES_ADMIN_DATABASE), autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (POSTGRES_DATABASE,),
        ).fetchone()
        if exists:
            return False

        # 数据库名不能作为普通 %s 参数，只能通过 Identifier 安全引用。
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(POSTGRES_DATABASE))
        )
        return True


def apply_schema() -> None:
    """按文件名顺序执行迁移；每个文件使用独立事务。"""
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        raise RuntimeError(f"没有找到 PostgreSQL 迁移：{MIGRATIONS_DIR}")

    for migration_file in migration_files:
        migration_sql = migration_file.read_text(encoding="utf-8")
        with psycopg.connect(_conninfo(POSTGRES_DATABASE)) as connection:
            # DDL 文件来自代码仓库，不包含用户输入，也不做字符串拼接。
            connection.execute(migration_sql)


def verify_schema() -> list[str]:
    """确认业务幂等表和记忆表存在，返回已找到的表名。"""
    expected = [
        "agent_request",
        "conversation_message",
        "conversation_summary",
        "memory_fact",
        "memory_job",
        "tool_execution",
    ]
    with psycopg.connect(_conninfo(POSTGRES_DATABASE)) as connection:
        rows = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = ANY(%s)
            ORDER BY table_name
            """,
            (expected,),
        ).fetchall()
    return [row[0] for row in rows]


def main() -> None:
    created = create_database_if_missing()
    apply_schema()
    tables = verify_schema()
    expected = {
        "agent_request",
        "conversation_message",
        "conversation_summary",
        "memory_fact",
        "memory_job",
        "tool_execution",
    }
    if set(tables) != expected:
        raise RuntimeError(f"PostgreSQL 初始化不完整，当前表：{tables}")
    action = "已创建" if created else "已存在"
    print(f"数据库 {POSTGRES_DATABASE!r} {action}；业务表初始化完成：{', '.join(tables)}")


if __name__ == "__main__":
    main()
