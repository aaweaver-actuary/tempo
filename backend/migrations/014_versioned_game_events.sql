-- Stage gameplay events in bounded slices, then switch the visible generation atomically.
ALTER TABLE game_derivation_jobs
    ADD COLUMN published_events_version BIGINT NOT NULL DEFAULT 0;

ALTER TABLE gameplay_events RENAME TO gameplay_events_legacy;

CREATE TABLE gameplay_events_staged (
    LIKE gameplay_events_legacy INCLUDING DEFAULTS,
    derivation_version BIGINT NOT NULL,
    PRIMARY KEY (game_id,derivation_version,id),
    FOREIGN KEY (game_id) REFERENCES imported_games(id) ON DELETE CASCADE
);

CREATE INDEX idx_gameplay_events_staged_game_version
    ON gameplay_events_staged(game_id,derivation_version,ply);

CREATE VIEW gameplay_events AS
    SELECT legacy.* FROM gameplay_events_legacy legacy
    LEFT JOIN game_derivation_jobs job ON job.game_id=legacy.game_id
    WHERE COALESCE(job.published_events_version,0)=0
    UNION ALL
    SELECT staged.id,staged.game_id,staged.analysis_version,
           staged.classifier_version,staged.ply,staged.kind,staged.motif,
           staged.beneficiary_color,staged.created_by_color,staged.outcome,
           staged.confidence,staged.loss_cp,staged.best_move_uci,
           staged.actual_move_uci,staged.principal_variation_json,
           staged.evidence_json,staged.created_at,staged.updated_at
    FROM gameplay_events_staged staged
    JOIN game_derivation_jobs job ON job.game_id=staged.game_id
        AND job.published_events_version=staged.derivation_version
    WHERE job.published_events_version>0;

INSERT INTO tempo_schema_migrations(version) VALUES (14);
