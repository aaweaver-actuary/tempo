ALTER TABLE game_analysis_jobs DROP CONSTRAINT game_analysis_jobs_status_check;
ALTER TABLE game_analysis_jobs ADD CONSTRAINT game_analysis_jobs_status_check
    CHECK (status IN ('queued','leased','publishing','complete','failed'));

CREATE TABLE game_analysis_publications (
    game_id TEXT PRIMARY KEY REFERENCES imported_games(id) ON DELETE CASCADE,
    analysis_version BIGINT NOT NULL,
    analysis_evidence_version BIGINT NOT NULL,
    prepared_json TEXT NOT NULL,
    next_ply BIGINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','publishing','complete','failed')),
    result_json TEXT NOT NULL,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_game_analysis_publications_pending
    ON game_analysis_publications(updated_at,game_id)
    WHERE status IN ('queued','publishing');

INSERT INTO tempo_schema_migrations(version) VALUES (10);
