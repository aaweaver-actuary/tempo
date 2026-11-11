-- Keep imported comparisons visible while a complete replacement is staged.
ALTER TABLE game_derivation_jobs
    ADD COLUMN published_repertoire_version BIGINT NOT NULL DEFAULT 0;

ALTER TABLE game_repertoire_matches RENAME TO game_repertoire_matches_legacy;
ALTER TABLE repertoire_decision_events RENAME TO repertoire_decision_events_legacy;
ALTER TABLE repertoire_comparisons RENAME TO repertoire_comparisons_legacy;

CREATE TABLE game_repertoire_matches_staged (
    LIKE game_repertoire_matches_legacy INCLUDING DEFAULTS,
    derivation_version BIGINT NOT NULL,
    PRIMARY KEY (game_id,derivation_version,repertoire_id),
    FOREIGN KEY (game_id) REFERENCES imported_games(id) ON DELETE CASCADE,
    FOREIGN KEY (repertoire_id) REFERENCES repertoires(id) ON DELETE CASCADE
);

CREATE TABLE repertoire_decision_events_staged (
    LIKE repertoire_decision_events_legacy INCLUDING DEFAULTS,
    derivation_version BIGINT NOT NULL,
    PRIMARY KEY (game_id,derivation_version,id),
    UNIQUE (game_id,derivation_version,repertoire_id,ply),
    FOREIGN KEY (game_id) REFERENCES imported_games(id) ON DELETE CASCADE,
    FOREIGN KEY (repertoire_id) REFERENCES repertoires(id) ON DELETE CASCADE,
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE SET NULL,
    CHECK (outcome IN ('miss','success'))
);

CREATE TABLE repertoire_comparisons_staged (
    LIKE repertoire_comparisons_legacy INCLUDING DEFAULTS,
    derivation_version BIGINT NOT NULL,
    PRIMARY KEY (game_id,derivation_version),
    FOREIGN KEY (game_id) REFERENCES imported_games(id) ON DELETE CASCADE
);

CREATE INDEX idx_game_repertoire_matches_staged_primary
    ON game_repertoire_matches_staged(game_id,derivation_version,is_primary);
CREATE INDEX idx_repertoire_decision_events_staged_repertoire
    ON repertoire_decision_events_staged(repertoire_id,game_id,derivation_version);

CREATE VIEW game_repertoire_matches AS
    SELECT legacy.* FROM game_repertoire_matches_legacy legacy
    LEFT JOIN game_derivation_jobs job ON job.game_id=legacy.game_id
    WHERE COALESCE(job.published_repertoire_version,0)=0
    UNION ALL
    SELECT staged.game_id,staged.repertoire_id,staged.is_primary,
           staged.classification,staged.matched_player_decisions,
           staged.repertoire_opportunities,staged.deepest_covered_ply,
           staged.first_player_deviation_ply,staged.first_player_deviation_fen,
           staged.first_player_deviation_expected_json,
           staged.first_player_deviation_actual_uci,staged.deviation_card_id,
           staged.first_opponent_gap_ply,staged.out_of_book_ply,
           staged.timeline_json,staged.updated_at
    FROM game_repertoire_matches_staged staged
    JOIN game_derivation_jobs job ON job.game_id=staged.game_id
        AND job.published_repertoire_version=staged.derivation_version
    WHERE job.published_repertoire_version>0;

CREATE VIEW repertoire_decision_events AS
    SELECT legacy.* FROM repertoire_decision_events_legacy legacy
    LEFT JOIN game_derivation_jobs job ON job.game_id=legacy.game_id
    WHERE COALESCE(job.published_repertoire_version,0)=0
    UNION ALL
    SELECT staged.id,staged.game_id,staged.repertoire_id,staged.card_id,
           staged.ply,staged.fen_key,staged.expected_uci,staged.actual_uci,
           staged.outcome,staged.played_at,staged.updated_at
    FROM repertoire_decision_events_staged staged
    JOIN game_derivation_jobs job ON job.game_id=staged.game_id
        AND job.published_repertoire_version=staged.derivation_version
    WHERE job.published_repertoire_version>0;

CREATE VIEW repertoire_comparisons AS
    SELECT legacy.* FROM repertoire_comparisons_legacy legacy
    LEFT JOIN game_derivation_jobs job ON job.game_id=legacy.game_id
    WHERE COALESCE(job.published_repertoire_version,0)=0
    UNION ALL
    SELECT staged.game_id,staged.repertoire_id,staged.classification,
           staged.divergence_ply,staged.divergence_fen,staged.expected_json,
           staged.actual_uci,staged.updated_at
    FROM repertoire_comparisons_staged staged
    JOIN game_derivation_jobs job ON job.game_id=staged.game_id
        AND job.published_repertoire_version=staged.derivation_version
    WHERE job.published_repertoire_version>0;

INSERT INTO tempo_schema_migrations(version) VALUES (12);
