-- Imported active runs are scanned one at a time for missing Explorer tasks.
CREATE INDEX idx_coverage_recovery_active_runs
    ON repertoire_coverage_runs (created_at, id)
    WHERE status IN ('queued', 'running');
INSERT INTO tempo_schema_migrations(version) VALUES (8);
