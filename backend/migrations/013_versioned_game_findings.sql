-- A finding derivation stages one evidence item per short background section.
-- The final transaction publishes all items for a generation together.
CREATE TABLE game_finding_publication_items (
    game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
    derivation_version BIGINT NOT NULL,
    item_kind TEXT NOT NULL CHECK (item_kind IN ('finding', 'opportunity', 'priority')),
    item_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (game_id, derivation_version, item_kind, item_key)
);

INSERT INTO tempo_schema_migrations(version) VALUES (13);
