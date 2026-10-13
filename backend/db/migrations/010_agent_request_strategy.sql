-- 与首次请求一起保存；恢复不受新的环境默认值影响。
ALTER TABLE agent_request ADD COLUMN IF NOT EXISTS strategy_mode VARCHAR(16) NOT NULL DEFAULT 'plan';
ALTER TABLE agent_request ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(16) NOT NULL DEFAULT '1';
ALTER TABLE agent_request ADD COLUMN IF NOT EXISTS catalog_schema_hash VARCHAR(64);
