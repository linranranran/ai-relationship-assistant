-- 请求级预算不依赖某次 run_id；模型/工具调用期间不能持有本表的行锁。
CREATE TABLE IF NOT EXISTS agent_request_budget (
    owner_id VARCHAR(128) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    limits JSONB NOT NULL,
    model_calls INTEGER NOT NULL DEFAULT 0,
    tool_attempts INTEGER NOT NULL DEFAULT 0,
    charged_tokens BIGINT NOT NULL DEFAULT 0,
    replans INTEGER NOT NULL DEFAULT 0,
    clarifications INTEGER NOT NULL DEFAULT 0,
    active_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    active_since TIMESTAMPTZ,
    active_run VARCHAR(128),
    exhausted_reason TEXT,
    PRIMARY KEY (owner_id, request_id)
);
CREATE TABLE IF NOT EXISTS agent_budget_attempt (
    attempt_id UUID PRIMARY KEY,
    owner_id VARCHAR(128) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    operation_key VARCHAR(128) NOT NULL,
    kind VARCHAR(24) NOT NULL CHECK (kind IN ('model', 'tool', 'replan', 'clarification')),
    status VARCHAR(24) NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved', 'settled', 'uncertain')),
    reserved_tokens BIGINT NOT NULL DEFAULT 0,
    actual_tokens BIGINT,
    usage_estimated BOOLEAN NOT NULL DEFAULT TRUE,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (owner_id, request_id) REFERENCES agent_request_budget(owner_id, request_id)
);
CREATE INDEX IF NOT EXISTS agent_budget_operation_idx
    ON agent_budget_attempt(owner_id, request_id, kind, operation_key);
