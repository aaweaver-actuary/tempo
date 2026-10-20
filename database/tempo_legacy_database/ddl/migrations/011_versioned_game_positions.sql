-- Keep the imported position index visible until a complete replacement is ready.
ALTER TABLE game_derivation_jobs
    ADD COLUMN published_position_version BIGINT NOT NULL DEFAULT 0;

ALTER TABLE game_position_occurrences RENAME TO game_position_occurrences_legacy;

CREATE TABLE game_position_occurrences_staged (
    game_id TEXT NOT NULL REFERENCES imported_games(id) ON DELETE CASCADE,
    derivation_version BIGINT NOT NULL,
    ply BIGINT NOT NULL,
    fen_key TEXT NOT NULL,
    move_uci TEXT,
    PRIMARY KEY (game_id,derivation_version,ply)
);

CREATE INDEX idx_game_positions_staged_fen
    ON game_position_occurrences_staged(fen_key,game_id,derivation_version);

CREATE VIEW game_position_occurrences AS
    SELECT legacy.game_id,legacy.ply,legacy.fen_key,legacy.move_uci
    FROM game_position_occurrences_legacy legacy
    LEFT JOIN game_derivation_jobs job ON job.game_id=legacy.game_id
    WHERE COALESCE(job.published_position_version,0)=0
    UNION ALL
    SELECT staged.game_id,staged.ply,staged.fen_key,staged.move_uci
    FROM game_position_occurrences_staged staged
    JOIN game_derivation_jobs job ON job.game_id=staged.game_id
        AND job.published_position_version=staged.derivation_version
    WHERE job.published_position_version>0;

INSERT INTO tempo_schema_migrations(version) VALUES (11);
