ALTER TABLE operation_receipts ADD COLUMN retry_cycle BIGINT NOT NULL DEFAULT 0;
ALTER TABLE operation_receipts ADD COLUMN cycle_attempt_count BIGINT NOT NULL DEFAULT 0;
ALTER TABLE operation_receipts ADD COLUMN attempt_token TEXT;
-- Existing attempts remain in the first cycle, including exhausted historical work.
UPDATE operation_receipts SET cycle_attempt_count=attempt_count;
INSERT INTO tempo_schema_migrations(version) VALUES (18);
