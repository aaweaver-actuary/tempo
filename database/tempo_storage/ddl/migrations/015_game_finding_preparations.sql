-- Persist one prepared finding snapshot so each publication slice reads by cursor.
CREATE TABLE game_finding_preparations (
    game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
    derivation_version BIGINT NOT NULL,
    source_signature TEXT NOT NULL,
    item_count BIGINT NOT NULL CHECK (item_count >= 0),
    items_json JSONB NOT NULL,
    PRIMARY KEY (game_id,derivation_version)
);

INSERT INTO tempo_schema_migrations(version) VALUES (15);
