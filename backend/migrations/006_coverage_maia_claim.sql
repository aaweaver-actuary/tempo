-- Keep external Maia lease claims and expired-lease recovery indexed.
CREATE INDEX idx_coverage_maia_claim
    ON repertoire_coverage_nodes (maia_status, run_id, ply, id)
    WHERE explorer_status = 'complete';
CREATE INDEX idx_coverage_maia_expired_lease
    ON repertoire_coverage_nodes (lease_expires_at, id)
    WHERE explorer_status = 'complete' AND maia_status = 'leased';
INSERT INTO tempo_schema_migrations(version) VALUES (6);
