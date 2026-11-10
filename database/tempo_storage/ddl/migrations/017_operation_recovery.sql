ALTER TABLE operation_receipts DROP CONSTRAINT operation_receipts_state_check;
ALTER TABLE operation_receipts ADD CONSTRAINT operation_receipts_state_check
  CHECK (state IN ('pending','queued','executing','retrying','blocked','complete','failed'));
ALTER TABLE operation_receipts ADD COLUMN payload_json TEXT;
ALTER TABLE operation_receipts ADD COLUMN background BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE operation_receipts ADD COLUMN attempt_count BIGINT NOT NULL DEFAULT 0;
ALTER TABLE operation_receipts ADD COLUMN next_retry_at TIMESTAMPTZ;
ALTER TABLE operation_receipts ADD COLUMN lease_expires_at TIMESTAMPTZ;
ALTER TABLE operation_receipts ADD COLUMN last_error_json TEXT;
CREATE INDEX idx_operation_receipts_recoverable
  ON operation_receipts(next_retry_at, updated_at)
  WHERE state IN ('queued','executing','retrying');
INSERT INTO tempo_schema_migrations(version) VALUES (17);
