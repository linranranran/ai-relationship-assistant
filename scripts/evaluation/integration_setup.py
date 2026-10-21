"""真实集成使用隔离 schema；仅在该命名空间执行项目迁移。

当前数据库账号若没有 CREATEDB，可使用专用 schema 作为隔离替代，报告必须写明。
不改 .env，不 TRUNCATE public，不删除与评测无关的 Neo4j 节点。
"""
import json
from pathlib import Path
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from backend.db.postgres_client import build_postgres_conninfo

ROOT=Path(__file__).resolve().parents[2]
SCHEMA="relation_eval_20261014"


def setup():
    assert SCHEMA.startswith("relation_eval_") and SCHEMA.replace("_","").isalnum()
    base=build_postgres_conninfo()
    with psycopg.connect(base,row_factory=dict_row) as c:
        existing=c.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name=%s",(SCHEMA,)).fetchone()
        if existing:
            raise RuntimeError("测试 schema 已存在，不能悄悄复用旧数据；先检查评测记录")
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)))
    isolated=make_conninfo(base,options="-c search_path="+SCHEMA)
    migrations=sorted((ROOT/"backend/db/migrations").glob("*.sql"))
    for path in migrations:
        with psycopg.connect(isolated) as c: c.execute(path.read_text(encoding="utf-8"),prepare=False)
    with psycopg.connect(isolated,row_factory=dict_row) as c:
        tables=[r["table_name"] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s ORDER BY table_name",(SCHEMA,)).fetchall()]
    report={"isolation":"dedicated_schema_shared_postgres_database","schema":SCHEMA,"tables":tables,
            "database_cleared":False,"existing_business_rows_modified":False}
    target=ROOT/"docs/评测文档/runs/integration-20261014"; target.mkdir(parents=True,exist_ok=True)
    (target/"setup.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__=="__main__": setup()
