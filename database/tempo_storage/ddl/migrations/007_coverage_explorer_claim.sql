-- One ordered Explorer position is read for each bounded background slice.
CREATE INDEX idx_coverage_explorer_queued_cursor
    ON repertoire_coverage_nodes (run_id, id)
    WHERE explorer_status = 'queued';
CREATE INDEX idx_coverage_explorer_run_progress
    ON repertoire_coverage_nodes (run_id, explorer_status);
INSERT INTO tempo_schema_migrations(version) VALUES (7);
