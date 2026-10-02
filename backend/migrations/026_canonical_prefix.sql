-- Opening scope is repertoire metadata; scheduled prefix cards remain unchanged.
ALTER TABLE repertoires ADD COLUMN canonical_prefix_moves_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE repertoires ADD COLUMN canonical_prefix_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE repertoires ADD COLUMN canonical_prefix_preview_id TEXT;
ALTER TABLE repertoires ADD COLUMN scope_source_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE repertoire_opportunities ADD COLUMN canonical_prefix_revision BIGINT NOT NULL DEFAULT 0;

CREATE TABLE canonical_prefix_previews(
    id TEXT PRIMARY KEY,repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
    moves_json TEXT NOT NULL,expected_revision BIGINT NOT NULL,source_revision BIGINT NOT NULL,
    state TEXT NOT NULL DEFAULT 'checking',suggestion_json TEXT NOT NULL DEFAULT '[]',
    last_error TEXT,created_at TEXT NOT NULL
);
CREATE TABLE canonical_prefix_results(
    preview_id TEXT NOT NULL REFERENCES canonical_prefix_previews(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,reason TEXT,
    disagreement_ply BIGINT,origin_json TEXT,scope_start_ply BIGINT,
    PRIMARY KEY(preview_id,item_id)
);
CREATE TABLE canonical_prefix_positions(
    preview_id TEXT NOT NULL REFERENCES canonical_prefix_previews(id) ON DELETE CASCADE,
    fen_key TEXT NOT NULL,in_scope BIGINT NOT NULL,fen TEXT NOT NULL,route_json TEXT NOT NULL,
    ply BIGINT NOT NULL,PRIMARY KEY(preview_id,fen_key,in_scope)
);

CREATE FUNCTION advance_canonical_prefix_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME='cards' THEN
        UPDATE repertoires SET scope_source_revision=scope_source_revision+1
        WHERE id IN (SELECT repertoire_id FROM repertoire_cards WHERE card_id=NEW.id);
    ELSIF TG_OP='DELETE' THEN
        UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=OLD.repertoire_id;
    ELSE
        UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=NEW.repertoire_id;
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER canonical_line_source AFTER INSERT OR UPDATE OR DELETE ON repertoire_lines
    FOR EACH ROW EXECUTE FUNCTION advance_canonical_prefix_source();
CREATE TRIGGER canonical_link_source AFTER INSERT OR UPDATE OR DELETE ON repertoire_cards
    FOR EACH ROW EXECUTE FUNCTION advance_canonical_prefix_source();
CREATE TRIGGER canonical_card_source AFTER UPDATE OF start_fen,moves_json,archived ON cards
    FOR EACH ROW WHEN (OLD.start_fen IS DISTINCT FROM NEW.start_fen OR OLD.moves_json IS DISTINCT FROM NEW.moves_json OR OLD.archived IS DISTINCT FROM NEW.archived)
    EXECUTE FUNCTION advance_canonical_prefix_source();

-- Readers must never expose a comparison made against a previous opening scope.
ALTER TABLE game_repertoire_matches_legacy ADD COLUMN canonical_prefix_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE game_repertoire_matches_staged ADD COLUMN canonical_prefix_revision BIGINT NOT NULL DEFAULT 0;
CREATE OR REPLACE VIEW game_repertoire_matches AS
    SELECT legacy.* FROM game_repertoire_matches_legacy legacy
    JOIN repertoires repertoire ON repertoire.id=legacy.repertoire_id
        AND repertoire.canonical_prefix_revision=legacy.canonical_prefix_revision
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
           staged.timeline_json,staged.updated_at,staged.canonical_prefix_revision
    FROM game_repertoire_matches_staged staged
    JOIN repertoires repertoire ON repertoire.id=staged.repertoire_id
        AND repertoire.canonical_prefix_revision=staged.canonical_prefix_revision
    JOIN game_derivation_jobs job ON job.game_id=staged.game_id
        AND job.published_repertoire_version=staged.derivation_version
    WHERE job.published_repertoire_version>0;

CREATE OR REPLACE VIEW repertoire_decision_events AS
    SELECT legacy.* FROM repertoire_decision_events_legacy legacy
    LEFT JOIN game_derivation_jobs job ON job.game_id=legacy.game_id
    WHERE COALESCE(job.published_repertoire_version,0)=0
      AND (EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id=legacy.repertoire_id AND scope.canonical_prefix_revision=0)
        OR EXISTS(SELECT 1 FROM game_repertoire_matches match
                 WHERE match.game_id=legacy.game_id AND match.repertoire_id=legacy.repertoire_id))
    UNION ALL
    SELECT staged.id,staged.game_id,staged.repertoire_id,staged.card_id,
           staged.ply,staged.fen_key,staged.expected_uci,staged.actual_uci,
           staged.outcome,staged.played_at,staged.updated_at
    FROM repertoire_decision_events_staged staged
    JOIN game_derivation_jobs job ON job.game_id=staged.game_id
        AND job.published_repertoire_version=staged.derivation_version
    WHERE job.published_repertoire_version>0
      AND (EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id=staged.repertoire_id AND scope.canonical_prefix_revision=0)
        OR EXISTS(SELECT 1 FROM game_repertoire_matches match
                 WHERE match.game_id=staged.game_id AND match.repertoire_id=staged.repertoire_id));

CREATE OR REPLACE VIEW repertoire_comparisons AS
    SELECT legacy.* FROM repertoire_comparisons_legacy legacy
    LEFT JOIN game_derivation_jobs job ON job.game_id=legacy.game_id
    WHERE COALESCE(job.published_repertoire_version,0)=0
      AND (legacy.repertoire_id IS NULL OR EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id=legacy.repertoire_id AND scope.canonical_prefix_revision=0) OR EXISTS(SELECT 1 FROM game_repertoire_matches match
           WHERE match.game_id=legacy.game_id AND match.repertoire_id=legacy.repertoire_id))
    UNION ALL
    SELECT staged.game_id,staged.repertoire_id,staged.classification,
           staged.divergence_ply,staged.divergence_fen,staged.expected_json,
           staged.actual_uci,staged.updated_at
    FROM repertoire_comparisons_staged staged
    JOIN game_derivation_jobs job ON job.game_id=staged.game_id
        AND job.published_repertoire_version=staged.derivation_version
    WHERE job.published_repertoire_version>0
      AND (staged.repertoire_id IS NULL OR EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id=staged.repertoire_id AND scope.canonical_prefix_revision=0) OR EXISTS(SELECT 1 FROM game_repertoire_matches match
           WHERE match.game_id=staged.game_id AND match.repertoire_id=staged.repertoire_id));

INSERT INTO tempo_schema_migrations(version) VALUES (26);
