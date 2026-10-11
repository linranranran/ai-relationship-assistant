"""预算短事务仓储。锁内只记账，绝不调用模型、Neo4j 或等待用户输入。"""

from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from psycopg.types.json import Jsonb

from backend.agent.budget import BudgetExceeded, current_run
from backend.db.postgres_client import get_postgres_connection
from backend.config import AGENT_RUN_LEASE_SECONDS


def ensure_budget_schema():
    with get_postgres_connection() as connection:
        connection.execute(
            Path(__file__)
            .with_name("migrations")
            .joinpath("009_agent_budget.sql")
            .read_text(encoding="utf-8")
        )


class BudgetStore:
    def __init__(self, owner_id, request_id):
        self.owner_id, self.request_id = owner_id, request_id

    def ensure(self, limits):
        # 只初始化一次，配置修改、HTTP 重试或人工恢复均不能充值现有请求。
        with get_postgres_connection() as connection:
            self._fence(connection)
            connection.execute(
                "INSERT INTO agent_request_budget(owner_id,request_id,limits) VALUES(%s,%s,%s) "
                "ON CONFLICT(owner_id,request_id) DO NOTHING",
                (self.owner_id, self.request_id, Jsonb(limits)),
            )

    def _fence(self, connection):
        run = current_run()
        if run is None:
            return  # 独立脚本/验收可在无运行租约时注入仓储；HTTP 路径总会传入。
        from backend.db.agent_stream_store import _fence, RunLeaseLost

        if run["owner_id"] != self.owner_id or run["request_id"] != self.request_id:
            raise RunLeaseLost("预算归属与当前运行不一致")
        _fence(connection, run)

    @contextmanager
    def _locked(self):
        with get_postgres_connection() as connection:
            self._fence(connection)
            row = connection.execute(
                "SELECT *, CURRENT_TIMESTAMP AS now FROM agent_request_budget "
                "WHERE owner_id=%s AND request_id=%s FOR UPDATE",
                (self.owner_id, self.request_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("请求预算未初始化")
            yield connection, dict(row)

    def _account(self, connection, row):
        if row["active_since"] is not None:
            elapsed = max(0, (row["now"] - row["active_since"]).total_seconds())
            # 崩溃后的停机时间不能全部算作执行时间；未知区间最多保守记一段租约。
            row["active_seconds"] += min(elapsed, AGENT_RUN_LEASE_SECONDS)
            connection.execute(
                "UPDATE agent_request_budget SET active_seconds=%s,active_since=CURRENT_TIMESTAMP "
                "WHERE owner_id=%s AND request_id=%s",
                (row["active_seconds"], self.owner_id, self.request_id),
            )

    @staticmethod
    def _reason(row):
        if row["exhausted_reason"]:
            return row["exhausted_reason"]
        if row["active_seconds"] >= row["limits"]["active_seconds"]:
            return "活跃执行时间已达到任务上限"
        return None

    def begin(self):
        reason = None
        with self._locked() as (connection, row):
            self._account(connection, row)
            reason = self._reason(row)
            connection.execute(
                "UPDATE agent_request_budget SET active_since=CASE WHEN %s THEN NULL ELSE CURRENT_TIMESTAMP END,active_run=%s, "
                "exhausted_reason=%s WHERE owner_id=%s AND request_id=%s",
                (
                    bool(reason),
                    (current_run() or {}).get("run_id"),
                    reason,
                    self.owner_id,
                    self.request_id,
                ),
            )
        # 必须提交后再抛异常；在事务内 raise 会回滚耗尽事实。
        if reason:
            raise BudgetExceeded(reason)

    def pause(self):
        with self._locked() as (connection, row):
            self._account(connection, row)
            connection.execute(
                "UPDATE agent_request_budget SET active_since=NULL,active_run=NULL "
                "WHERE owner_id=%s AND request_id=%s",
                (self.owner_id, self.request_id),
            )

    def snapshot(self):
        with self._locked() as (_, row):
            return {
                key: row[key]
                for key in (
                    "limits",
                    "model_calls",
                    "tool_attempts",
                    "charged_tokens",
                    "replans",
                    "clarifications",
                    "active_seconds",
                    "exhausted_reason",
                )
            }

    def tick(self):
        """运行心跳结算活跃时间；人工等待时 active_since=NULL，不会继续扣时间。"""
        with get_postgres_connection() as connection:
            if not connection.execute(
                "SELECT 1 FROM agent_request_budget WHERE owner_id=%s AND request_id=%s",
                (self.owner_id, self.request_id),
            ).fetchone():
                return None  # 兼容尚未初始化预算的旧请求。
        reason = None
        with self._locked() as (connection, row):
            self._account(connection, row)
            reason = self._reason(row)
            if reason:
                connection.execute(
                    "UPDATE agent_request_budget SET exhausted_reason=%s WHERE owner_id=%s AND request_id=%s",
                    (reason, self.owner_id, self.request_id),
                )
        return reason

    def reserve(self, kind, operation_key, tokens=0):
        column = {
            "model": "model_calls",
            "tool": "tool_attempts",
            "replan": "replans",
            "clarification": "clarifications",
        }[kind]
        reason, result = None, None
        with self._locked() as (connection, row):
            self._account(connection, row)
            # 只复用成功且已结算的模型结果；未知结果不得当作零成本重新调用。
            if kind in {"model", "replan", "clarification"}:
                cached = connection.execute(
                    "SELECT attempt_id,result FROM agent_budget_attempt WHERE owner_id=%s AND "
                    "request_id=%s AND kind=%s AND operation_key=%s AND status='settled' AND result IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT 1",
                    (self.owner_id, self.request_id, kind, operation_key),
                ).fetchone()
                if cached:
                    return {
                        "cached": True,
                        "result": cached["result"],
                        "attempt_id": str(cached["attempt_id"]),
                    }
            reason = self._reason(row)
            if not reason and row[column] >= row["limits"][column]:
                reason = f"任务 {column} 次数已达到上限"
            if not reason and row["charged_tokens"] + tokens > row["limits"]["tokens"]:
                reason = "剩余 Token 预算不足以启动本次模型调用"
            if reason:
                connection.execute(
                    "UPDATE agent_request_budget SET exhausted_reason=%s WHERE owner_id=%s AND request_id=%s",
                    (reason, self.owner_id, self.request_id),
                )
            else:
                attempt_id = str(uuid4())
                connection.execute(
                    f"UPDATE agent_request_budget SET {column}={column}+1,charged_tokens=charged_tokens+%s "
                    "WHERE owner_id=%s AND request_id=%s",
                    (tokens, self.owner_id, self.request_id),
                )
                connection.execute(
                    "INSERT INTO agent_budget_attempt(attempt_id,owner_id,request_id,kind,operation_key,reserved_tokens) "
                    "VALUES(%s,%s,%s,%s,%s,%s)",
                    (
                        attempt_id,
                        self.owner_id,
                        self.request_id,
                        kind,
                        operation_key,
                        tokens,
                    ),
                )
                result = {"cached": False, "attempt_id": attempt_id}
        if reason:
            raise BudgetExceeded(reason)
        return result

    def settle(self, attempt, actual_tokens=None, result=None):
        if attempt["cached"]:
            return
        reason = None
        with self._locked() as (connection, budget):
            row = connection.execute(
                "SELECT status,reserved_tokens FROM agent_budget_attempt WHERE attempt_id=%s "
                "AND owner_id=%s AND request_id=%s FOR UPDATE",
                (attempt["attempt_id"], self.owner_id, self.request_id),
            ).fetchone()
            if row is None or row["status"] != "reserved":
                return
            actual = max(0, int(actual_tokens)) if actual_tokens is not None else None
            connection.execute(
                "UPDATE agent_budget_attempt SET status=%s,actual_tokens=%s,usage_estimated=%s,result=%s "
                "WHERE attempt_id=%s",
                (
                    "settled" if result is not None else "uncertain",
                    actual,
                    actual is None,
                    Jsonb(result) if result is not None else None,
                    attempt["attempt_id"],
                ),
            )
            if actual is not None:
                connection.execute(
                    "UPDATE agent_request_budget SET charged_tokens=GREATEST(0,charged_tokens+%s) "
                    "WHERE owner_id=%s AND request_id=%s",
                    (actual - row["reserved_tokens"], self.owner_id, self.request_id),
                )
                if (
                    budget["charged_tokens"] + actual - row["reserved_tokens"]
                    > budget["limits"]["tokens"]
                ):
                    # 供应商真实用量可能高于本地估算。费用无法撤销，但必须立即
                    # 固化耗尽事实；最后一次回复也不能被误报成正常 COMPLETED。
                    reason = "供应商实际 Token 用量已超过任务预算"
                    connection.execute(
                        "UPDATE agent_request_budget SET exhausted_reason=%s WHERE owner_id=%s AND request_id=%s",
                        (reason, self.owner_id, self.request_id),
                    )
        if reason:
            raise BudgetExceeded(reason)
