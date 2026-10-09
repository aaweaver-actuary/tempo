-- Retain independent source results; this migration never retries old failures.
ALTER TABLE repertoire_coverage_nodes ADD COLUMN explorer_failure_code TEXT;
ALTER TABLE repertoire_coverage_nodes ADD COLUMN explorer_retry_at TEXT;
ALTER TABLE repertoire_coverage_nodes ADD COLUMN explorer_error TEXT;
CREATE INDEX coverage_explorer_recovery_node ON repertoire_coverage_nodes(run_id,explorer_failure_code,id)
    WHERE explorer_status='failed';
CREATE INDEX coverage_explorer_retry_node ON repertoire_coverage_nodes(run_id,id,explorer_retry_at)
    WHERE explorer_status='queued';
CREATE INDEX coverage_latest_scope_attempt ON repertoire_coverage_runs(repertoire_id,created_at DESC,id DESC);
INSERT INTO tempo_schema_migrations(version) VALUES(47);
