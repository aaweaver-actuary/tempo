-- Durable, per-generation integrity aggregation and issue staging.
CREATE TABLE repertoire_integrity_position_accumulators (
    run_id TEXT NOT NULL,
    fen_key TEXT NOT NULL,
    fen TEXT NOT NULL,
    trained_color TEXT,
    moves_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    PRIMARY KEY (run_id, fen_key)
);
CREATE TABLE repertoire_integrity_issue_candidates (
    run_id TEXT NOT NULL,
    id TEXT NOT NULL,
    repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    fen_key TEXT,
    fen TEXT,
    trained_color TEXT,
    signature TEXT NOT NULL,
    moves_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);
CREATE INDEX idx_integrity_issue_candidates_repertoire
    ON repertoire_integrity_issue_candidates(repertoire_id, run_id);
INSERT INTO tempo_schema_migrations(version) VALUES (3);
