-- Add receipts without certifying any unverified historical/partial index.
CREATE TABLE game_position_index_sources (
    game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
    derivation_version BIGINT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    algorithm_version INTEGER NOT NULL,
    verified_from_start INTEGER NOT NULL CHECK(verified_from_start IN (0,1)),
    position_count BIGINT,
    published_at TEXT,
    PRIMARY KEY(game_id,derivation_version)
);
INSERT INTO tempo_schema_migrations(version) VALUES(44);
