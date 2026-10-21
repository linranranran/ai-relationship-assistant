"""从评测 schema 的一致性快照备份，再恢复到另一专用 schema。

不读取 public，不备份凭据。这里验证 PG 工作流数据的恢复；Neo4j 与 PG
并非同一数据库快照，不能把此实验描述为两个系统的跨库一致性备份。
"""
import hashlib
import json
from pathlib import Path
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from langgraph.checkpoint.postgres import PostgresSaver
from backend.db.postgres_client import build_postgres_conninfo
from scripts.evaluation.integration_setup import ROOT, SCHEMA


def main():
    restored = SCHEMA + "_restore"
    base = build_postgres_conninfo()
    folder = ROOT / "docs/评测文档/runs/integration-20261014/pg-backup"
    folder.mkdir(exist_ok=True)
    with psycopg.connect(base) as c:
        assert c.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (restored,)).fetchone() is None
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(restored)))
    restore_conninfo = make_conninfo(base, options="-c search_path=" + restored)
    for path in sorted((ROOT/"backend/db/migrations").glob("*.sql")):
        with psycopg.connect(restore_conninfo) as c:
            c.execute(path.read_text(encoding="utf-8"), prepare=False)
    with psycopg.connect(restore_conninfo, autocommit=True) as c:
        PostgresSaver(c).setup()
    records = []
    with psycopg.connect(make_conninfo(base, options="-c search_path=" + SCHEMA)) as source:
        source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        tables = [r[0] for r in source.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=%s ORDER BY table_name", (SCHEMA,)).fetchall()]
        # 外键父表先恢复。循环 FK 不在本项目 schema 中；若以后出现必须显式处理。
        edges = source.execute("SELECT child.relname,parent.relname FROM pg_constraint k JOIN pg_class child ON child.oid=k.conrelid "
            "JOIN pg_class parent ON parent.oid=k.confrelid JOIN pg_namespace n ON n.oid=child.relnamespace "
            "WHERE k.contype='f' AND n.nspname=%s", (SCHEMA,)).fetchall()
        ordered = []
        while len(ordered) < len(tables):
            ready = [t for t in tables if t not in ordered and all(p in ordered for c,p in edges if c == t)]
            if not ready:
                raise RuntimeError("Cyclic FK; refuse incomplete restoration")
            ordered.extend(ready)
        for table in ordered:
            backup = folder / (table + ".copy")
            count = source.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
            with backup.open("wb") as f, source.cursor().copy(sql.SQL("COPY {} TO STDOUT (FORMAT BINARY)").format(sql.Identifier(table))) as copy:
                for block in copy:
                    f.write(block)
            records.append({"table": table, "rows": count, "sha256": hashlib.sha256(backup.read_bytes()).hexdigest()})
    with psycopg.connect(restore_conninfo) as target:
        # setup() 自带迁移版本记录；仅清理明确的全新恢复 schema 中这一张表。
        target.execute("TRUNCATE checkpoint_migrations")
        for record in records:
            table = record["table"]
            with (folder/(table+".copy")).open("rb") as f, target.cursor().copy(
                    sql.SQL("COPY {} FROM STDIN (FORMAT BINARY)").format(sql.Identifier(table))) as copy:
                while block := f.read(65536):
                    copy.write(block)
            record["restored_rows"] = target.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
            record["pass"] = record["rows"] == record["restored_rows"]
    result = {"pass": all(r["pass"] for r in records), "source_schema": SCHEMA, "restored_schema": restored,
              "tables": records, "scope": "PostgreSQL complete evaluation schema at a repeatable-read snapshot",
              "neo4j_cross_database_snapshot": "not_verified", "secrets_in_backup": False}
    (folder.parent/"backup-restore.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"tables":len(records),"rows":sum(r["rows"] for r in records),"pass":result["pass"]}),flush=True)


if __name__ == "__main__":
    main()
