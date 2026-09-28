-- Keep the currently published game analysis visible while a replacement is
-- written in bounded slices. Existing imported rows are generation zero.
ALTER TABLE imported_games ADD COLUMN published_analysis_generation BIGINT NOT NULL DEFAULT 0;

ALTER TABLE game_move_analysis RENAME TO game_move_analysis_legacy;
ALTER TABLE game_move_analysis_candidates RENAME TO game_move_analysis_candidates_legacy;

CREATE TABLE game_move_analysis_staged (
    LIKE game_move_analysis_legacy INCLUDING DEFAULTS,
    publication_generation BIGINT NOT NULL,
    PRIMARY KEY (game_id,publication_generation,ply),
    FOREIGN KEY (game_id) REFERENCES imported_games(id) ON DELETE CASCADE
);

CREATE TABLE game_move_analysis_candidates_staged (
    LIKE game_move_analysis_candidates_legacy INCLUDING DEFAULTS,
    publication_generation BIGINT NOT NULL,
    PRIMARY KEY (game_id,publication_generation,ply,rank),
    FOREIGN KEY (game_id,publication_generation,ply)
        REFERENCES game_move_analysis_staged(game_id,publication_generation,ply) ON DELETE CASCADE
);

CREATE INDEX idx_game_analysis_staged_generation
    ON game_move_analysis_staged(game_id,publication_generation);
CREATE INDEX idx_game_analysis_candidates_staged_move
    ON game_move_analysis_candidates_staged(game_id,publication_generation,ply,candidate_uci);

CREATE VIEW game_move_analysis AS
    SELECT legacy.* FROM game_move_analysis_legacy legacy
    JOIN imported_games game ON game.id=legacy.game_id
    WHERE game.published_analysis_generation=0
    UNION ALL
    SELECT staged.game_id,staged.ply,staged.eval_before_cp,staged.eval_after_cp,
           staged.loss_cp,staged.label,staged.depth,staged.best_move_uci,
           staged.principal_variation_json,staged.mate_before,staged.mate_after,
           staged.engine_version,staged.network_version,staged.mover_color,
           staged.is_player_move,staged.actual_move_uci,staged.position_fen
    FROM game_move_analysis_staged staged
    JOIN imported_games game ON game.id=staged.game_id
        AND game.published_analysis_generation=staged.publication_generation
    WHERE game.published_analysis_generation>0;

CREATE VIEW game_move_analysis_candidates AS
    SELECT legacy.* FROM game_move_analysis_candidates_legacy legacy
    JOIN imported_games game ON game.id=legacy.game_id
    WHERE game.published_analysis_generation=0
    UNION ALL
    SELECT staged.game_id,staged.ply,staged.rank,staged.candidate_uci,
           staged.score_cp,staged.mate,staged.score_text,
           staged.principal_variation_json,staged.depth,staged.position_fen,
           staged.engine_version,staged.network_version
    FROM game_move_analysis_candidates_staged staged
    JOIN imported_games game ON game.id=staged.game_id
        AND game.published_analysis_generation=staged.publication_generation
    WHERE game.published_analysis_generation>0;

INSERT INTO tempo_schema_migrations(version) VALUES (9);
