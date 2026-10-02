-- 人工交互也需要幂等：答案持久化与 LangGraph 恢复分两步，以回执衔接崩溃窗口。
CREATE TABLE IF NOT EXISTS agent_interaction (
    owner_id TEXT NOT NULL,
    interrupt_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    answer JSONB NOT NULL,
    answer_hash TEXT NOT NULL,
    create_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (owner_id, interrupt_id)
);
CREATE INDEX IF NOT EXISTS agent_interaction_request_idx
    ON agent_interaction (owner_id, request_id);
