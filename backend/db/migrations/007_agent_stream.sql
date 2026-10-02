-- Web 进程直接运行 LangGraph；受理、执行和进度保存不能放进一个长事务。
-- agent_request 继续负责业务幂等；下面的表保存执行批次和回放进度。
CREATE TABLE IF NOT EXISTS agent_task (
    owner_id VARCHAR(128) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    thread_id VARCHAR(128) NOT NULL,
    message_hash CHAR(64) NOT NULL,
    input JSONB NOT NULL,
    current_run_id VARCHAR(64),
    seq BIGINT NOT NULL DEFAULT 0,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_id, request_id)
);
CREATE TABLE IF NOT EXISTS agent_run (
    run_id VARCHAR(64) PRIMARY KEY,
    owner_id VARCHAR(128) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    action VARCHAR(16) NOT NULL CHECK (action IN ('CHAT', 'RESUME', 'STOP')),
    interrupt_id VARCHAR(256),
    status VARCHAR(16) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'waiting', 'completed', 'failed', 'cancelled')),
    response JSONB,
    execution_token VARCHAR(64),
    lease_until TIMESTAMPTZ,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    FOREIGN KEY (owner_id, request_id) REFERENCES agent_task(owner_id, request_id)
);
ALTER TABLE agent_run ADD COLUMN IF NOT EXISTS queued_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_agent_run_active ON agent_run(owner_id, status);
CREATE TABLE IF NOT EXISTS agent_event (
    owner_id VARCHAR(128) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    seq BIGINT NOT NULL,
    run_id VARCHAR(64) NOT NULL REFERENCES agent_run(run_id),
    event JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_id, request_id, seq),
    FOREIGN KEY (owner_id, request_id) REFERENCES agent_task(owner_id, request_id)
);
-- 升级前已有 agent_outbox 表不删除；当前代码不再创建、读取或写入它。
CREATE INDEX IF NOT EXISTS idx_agent_run_pending ON agent_run(status, queued_at);
