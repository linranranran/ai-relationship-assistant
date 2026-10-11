-- 页面按递增 id 做向前翻页，不使用 Token 记忆窗口，也不使用 OFFSET 深分页。
CREATE INDEX IF NOT EXISTS idx_conversation_message_history
ON conversation_message (owner_id, conversation_id, id DESC)
WHERE role IN ('user', 'assistant');
