ALTER TABLE agent_request_budget ADD COLUMN IF NOT EXISTS discovery_calls INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_budget_attempt DROP CONSTRAINT IF EXISTS agent_budget_attempt_kind_check;
ALTER TABLE agent_budget_attempt ADD CONSTRAINT agent_budget_attempt_kind_check
    CHECK (kind IN ('model','tool','replan','clarification','discovery'));
-- 旧请求的额度保持不变，只补充以前不存在的发现维度。
UPDATE agent_request_budget SET limits = limits || '{"discovery_calls":8}'::jsonb
    WHERE NOT (limits ? 'discovery_calls');
