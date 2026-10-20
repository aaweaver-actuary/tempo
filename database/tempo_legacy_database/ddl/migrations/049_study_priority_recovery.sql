-- Due-state probes must not sort completed receipt history on every wake.
CREATE INDEX idx_operation_retry_due ON operation_receipts(next_retry_at,updated_at,operation_id)
    WHERE state='retrying' AND payload_json IS NOT NULL;
CREATE INDEX idx_operation_lease_due ON operation_receipts(lease_expires_at,updated_at,operation_id)
    WHERE state='executing' AND payload_json IS NOT NULL;
CREATE INDEX idx_operation_queue_due ON operation_receipts(updated_at,operation_id)
    WHERE state='queued' AND payload_json IS NOT NULL;

INSERT INTO tempo_schema_migrations(version) VALUES(49);
