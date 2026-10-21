-- 原文证据与用户↔Agent 的 conversation_message 分开；来源固定为模拟聊天。
-- Person 在 Neo4j，故这里不伪造跨数据库外键/分布式事务保证。
CREATE TABLE IF NOT EXISTS customer_chat_message (
    message_id UUID PRIMARY KEY,
    owner_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type = 'simulated_chat'),
    source_conversation_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    speaker TEXT NOT NULL CHECK (speaker IN ('customer', 'sales')),
    sent_at TIMESTAMPTZ NOT NULL,
    text TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 2000),
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, source_type, source_conversation_id, source_message_id)
);
CREATE INDEX IF NOT EXISTS idx_customer_chat_person_time
    ON customer_chat_message (owner_id, person_id, sent_at DESC, message_id DESC);
