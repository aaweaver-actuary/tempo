-- A coverage generation stays hidden from Explorer claims until its nodes are seeded.
ALTER TABLE repertoire_coverage_runs
    DROP CONSTRAINT repertoire_coverage_runs_status_check;
ALTER TABLE repertoire_coverage_runs
    ADD CONSTRAINT repertoire_coverage_runs_status_check
    CHECK (status IN ('building', 'queued', 'running', 'complete', 'failed'));
CREATE INDEX idx_coverage_seed_line_cursor
    ON repertoire_lines (repertoire_id, id);
CREATE INDEX idx_coverage_seed_activation_cursor
    ON repertoire_coverage_nodes (run_id, id)
    WHERE explorer_status = 'staging';
INSERT INTO tempo_schema_migrations(version) VALUES (5);
