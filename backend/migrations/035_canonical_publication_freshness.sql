-- Authoritative routes, graph materialization, and global game classification
-- have independent identities. Preserve all saved cards and study state.
ALTER TABLE cards ADD COLUMN canonical_route_source BIGINT NOT NULL DEFAULT 1 CHECK(canonical_route_source IN (0,1));
ALTER TABLE repertoire_cards ADD COLUMN canonical_route_source BIGINT NOT NULL DEFAULT 1 CHECK(canonical_route_source IN (0,1));
ALTER TABLE imported_games ADD COLUMN repertoire_scope_generation BIGINT NOT NULL DEFAULT 0;
ALTER TABLE game_findings ADD COLUMN game_scope_generation BIGINT NOT NULL DEFAULT 0;
ALTER TABLE repertoire_opportunities ADD COLUMN canonical_scope_source_revision BIGINT NOT NULL DEFAULT -1;
ALTER TABLE repertoire_opportunities ADD COLUMN canonical_scope_preview_id TEXT;
ALTER TABLE repertoire_opportunities ADD COLUMN game_scope_generation BIGINT NOT NULL DEFAULT 0;
CREATE TABLE repertoire_game_scope(id BIGINT PRIMARY KEY CHECK(id=1),generation BIGINT NOT NULL);
INSERT INTO repertoire_game_scope VALUES(1,0);
DROP TRIGGER canonical_line_source ON repertoire_lines;
DROP TRIGGER canonical_link_source ON repertoire_cards;
DROP TRIGGER canonical_card_source ON cards;
DROP TRIGGER canonical_card_insert_source ON cards;
DROP TRIGGER canonical_card_delete_source ON cards;
UPDATE cards SET canonical_route_source=0 WHERE EXISTS(SELECT 1 FROM opening_graph_steps step WHERE step.card_id=cards.id AND step.starting_fen=cards.start_fen AND step.moves_json=cards.moves_json);
UPDATE repertoire_cards SET canonical_route_source=0 WHERE EXISTS(SELECT 1 FROM opening_graph_steps step JOIN cards card ON card.id=step.card_id WHERE step.card_id=repertoire_cards.card_id AND step.repertoire_id=repertoire_cards.repertoire_id AND card.canonical_route_source=0);
CREATE FUNCTION promote_canonical_card_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(OLD.start_fen,OLD.moves_json,OLD.trained_color,OLD.content_type) IS DISTINCT FROM ROW(NEW.start_fen,NEW.moves_json,NEW.trained_color,NEW.content_type) THEN
        NEW.canonical_route_source=1;
        UPDATE repertoire_cards SET canonical_route_source=1 WHERE card_id=NEW.id AND canonical_route_source=0;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER canonical_card_promote BEFORE UPDATE ON cards FOR EACH ROW EXECUTE FUNCTION promote_canonical_card_source();
-- Explicit membership provenance overrides the card owner fallback. Card
-- promotion alone never adopts a generated membership in another repertoire.
CREATE OR REPLACE FUNCTION advance_canonical_prefix_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME='cards' THEN
        IF TG_OP='DELETE' THEN
            IF OLD.canonical_route_source=0 OR OLD.content_type<>'opening' OR OLD.moves_json='[]' THEN RETURN NULL; END IF;
            UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE (id=OLD.repertoire_id AND NOT EXISTS(SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id=OLD.id AND owner_link.repertoire_id=OLD.repertoire_id AND owner_link.canonical_route_source=0)) OR id IN (SELECT repertoire_id FROM repertoire_cards WHERE card_id=OLD.id AND canonical_route_source=1);
        ELSIF TG_OP='INSERT' THEN
            IF NEW.canonical_route_source=0 OR NEW.content_type<>'opening' OR NEW.moves_json='[]' THEN RETURN NULL; END IF;
            UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE (id=NEW.repertoire_id AND NOT EXISTS(SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id=NEW.id AND owner_link.repertoire_id=NEW.repertoire_id AND owner_link.canonical_route_source=0)) OR id IN (SELECT repertoire_id FROM repertoire_cards WHERE card_id=NEW.id AND canonical_route_source=1);
        ELSE
            IF OLD.canonical_route_source=0 AND NEW.canonical_route_source=0 THEN RETURN NULL; END IF;
            IF ROW(OLD.start_fen,OLD.moves_json,OLD.archived,OLD.repertoire_id,OLD.content_type,OLD.trained_color,OLD.canonical_route_source) IS NOT DISTINCT FROM ROW(NEW.start_fen,NEW.moves_json,NEW.archived,NEW.repertoire_id,NEW.content_type,NEW.trained_color,NEW.canonical_route_source) THEN RETURN NULL; END IF;
            IF (OLD.content_type<>'opening' AND NEW.content_type<>'opening') OR (OLD.moves_json='[]' AND NEW.moves_json='[]') THEN RETURN NULL; END IF;
            -- Owner-only reassignment does not change explicitly linked source sets.
            IF ROW(OLD.start_fen,OLD.moves_json,OLD.archived,OLD.content_type,OLD.trained_color,OLD.canonical_route_source) IS NOT DISTINCT FROM ROW(NEW.start_fen,NEW.moves_json,NEW.archived,NEW.content_type,NEW.trained_color,NEW.canonical_route_source) THEN
                UPDATE repertoires SET scope_source_revision=scope_source_revision+1
                WHERE (id=OLD.repertoire_id AND OLD.canonical_route_source=1 AND NOT EXISTS(SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id=OLD.id AND owner_link.repertoire_id=OLD.repertoire_id))
                   OR (id=NEW.repertoire_id AND NEW.canonical_route_source=1 AND NOT EXISTS(SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id=NEW.id AND owner_link.repertoire_id=NEW.repertoire_id));
                RETURN NULL;
            END IF;
            UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE (id=OLD.repertoire_id AND OLD.canonical_route_source=1 AND NOT EXISTS(SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id=OLD.id AND owner_link.repertoire_id=OLD.repertoire_id AND owner_link.canonical_route_source=0)) OR (id=NEW.repertoire_id AND NEW.canonical_route_source=1 AND NOT EXISTS(SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id=NEW.id AND owner_link.repertoire_id=NEW.repertoire_id AND owner_link.canonical_route_source=0)) OR id IN (SELECT repertoire_id FROM repertoire_cards WHERE card_id=NEW.id AND canonical_route_source=1);
        END IF;
    ELSE
        IF TG_TABLE_NAME='repertoire_cards' THEN
            IF TG_OP='DELETE' AND OLD.canonical_route_source=0 THEN RETURN NULL; END IF;
            IF TG_OP='INSERT' AND NEW.canonical_route_source=0 THEN RETURN NULL; END IF;
            IF TG_OP='UPDATE' AND OLD.canonical_route_source=0 AND NEW.canonical_route_source=0 THEN RETURN NULL; END IF;
        ELSIF TG_OP='UPDATE' AND ROW(OLD.repertoire_id,OLD.start_fen,OLD.moves_json,OLD.trained_color) IS NOT DISTINCT FROM ROW(NEW.repertoire_id,NEW.start_fen,NEW.moves_json,NEW.trained_color) THEN RETURN NULL;
        END IF;
        IF TG_OP='DELETE' THEN
            UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=OLD.repertoire_id;
        ELSIF TG_OP='UPDATE' THEN
            UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id IN (OLD.repertoire_id,NEW.repertoire_id);
        ELSE
            UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id=NEW.repertoire_id;
        END IF;
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER canonical_line_source AFTER INSERT OR UPDATE OR DELETE ON repertoire_lines FOR EACH ROW EXECUTE FUNCTION advance_canonical_prefix_source();
CREATE TRIGGER canonical_link_source AFTER INSERT OR UPDATE OR DELETE ON repertoire_cards FOR EACH ROW EXECUTE FUNCTION advance_canonical_prefix_source();
CREATE TRIGGER canonical_card_source AFTER UPDATE ON cards FOR EACH ROW EXECUTE FUNCTION advance_canonical_prefix_source();
CREATE TRIGGER canonical_card_insert_source AFTER INSERT ON cards FOR EACH ROW EXECUTE FUNCTION advance_canonical_prefix_source();
CREATE TRIGGER canonical_card_delete_source AFTER DELETE ON cards FOR EACH ROW EXECUTE FUNCTION advance_canonical_prefix_source();
CREATE FUNCTION advance_repertoire_game_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END) IN ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__') THEN RETURN NULL; END IF;
    IF TG_OP='UPDATE' AND ROW(OLD.canonical_prefix_moves_json,OLD.scope_source_revision,OLD.is_main) IS NOT DISTINCT FROM ROW(NEW.canonical_prefix_moves_json,NEW.scope_source_revision,NEW.is_main) THEN RETURN NULL; END IF;
    UPDATE repertoire_game_scope SET generation=generation+1 WHERE id=1;
    -- Existing priority generations also consume repertoire classification evidence.
    UPDATE priority_source_epoch SET version=version+1 WHERE id=1;
    RETURN NULL;
END $$;
CREATE TRIGGER canonical_game_scope AFTER INSERT OR UPDATE OR DELETE ON repertoires FOR EACH ROW EXECUTE FUNCTION advance_repertoire_game_scope();
CREATE FUNCTION stamp_new_game_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.repertoire_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1);
    RETURN NEW;
END $$;
CREATE TRIGGER canonical_game_insert BEFORE INSERT ON imported_games FOR EACH ROW EXECUTE FUNCTION stamp_new_game_scope();
CREATE OR REPLACE VIEW game_repertoire_matches AS
    SELECT legacy.* FROM game_repertoire_matches_legacy legacy
    JOIN repertoires repertoire ON repertoire.id=legacy.repertoire_id
        AND repertoire.canonical_prefix_revision=legacy.canonical_prefix_revision
    LEFT JOIN game_derivation_jobs job ON job.game_id=legacy.game_id
    WHERE COALESCE(job.published_repertoire_version,0)=0 AND EXISTS(SELECT 1 FROM imported_games current_game WHERE current_game.id=legacy.game_id AND current_game.repertoire_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1))
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
    WHERE job.published_repertoire_version>0 AND EXISTS(SELECT 1 FROM imported_games current_game WHERE current_game.id=staged.game_id AND current_game.repertoire_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1));

CREATE OR REPLACE VIEW repertoire_decision_events AS
    SELECT legacy.* FROM repertoire_decision_events_legacy legacy
    LEFT JOIN game_derivation_jobs job ON job.game_id=legacy.game_id
    WHERE COALESCE(job.published_repertoire_version,0)=0 AND EXISTS(SELECT 1 FROM imported_games current_game WHERE current_game.id=legacy.game_id AND current_game.repertoire_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1))
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
    WHERE job.published_repertoire_version>0 AND EXISTS(SELECT 1 FROM imported_games current_game WHERE current_game.id=staged.game_id AND current_game.repertoire_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1))
      AND (EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id=staged.repertoire_id AND scope.canonical_prefix_revision=0)
        OR EXISTS(SELECT 1 FROM game_repertoire_matches match
                 WHERE match.game_id=staged.game_id AND match.repertoire_id=staged.repertoire_id));

CREATE OR REPLACE VIEW repertoire_comparisons AS
    SELECT legacy.* FROM repertoire_comparisons_legacy legacy
    LEFT JOIN game_derivation_jobs job ON job.game_id=legacy.game_id
    WHERE COALESCE(job.published_repertoire_version,0)=0 AND EXISTS(SELECT 1 FROM imported_games current_game WHERE current_game.id=legacy.game_id AND current_game.repertoire_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1))
      AND (legacy.repertoire_id IS NULL OR EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id=legacy.repertoire_id AND scope.canonical_prefix_revision=0) OR EXISTS(SELECT 1 FROM game_repertoire_matches match
           WHERE match.game_id=legacy.game_id AND match.repertoire_id=legacy.repertoire_id))
    UNION ALL
    SELECT staged.game_id,staged.repertoire_id,staged.classification,
           staged.divergence_ply,staged.divergence_fen,staged.expected_json,
           staged.actual_uci,staged.updated_at
    FROM repertoire_comparisons_staged staged
    JOIN game_derivation_jobs job ON job.game_id=staged.game_id
        AND job.published_repertoire_version=staged.derivation_version
    WHERE job.published_repertoire_version>0 AND EXISTS(SELECT 1 FROM imported_games current_game WHERE current_game.id=staged.game_id AND current_game.repertoire_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1))
      AND (staged.repertoire_id IS NULL OR EXISTS(SELECT 1 FROM repertoires scope WHERE scope.id=staged.repertoire_id AND scope.canonical_prefix_revision=0) OR EXISTS(SELECT 1 FROM game_repertoire_matches match
           WHERE match.game_id=staged.game_id AND match.repertoire_id=staged.repertoire_id));

CREATE VIEW current_game_repertoire_matches AS SELECT * FROM game_repertoire_matches;
CREATE VIEW current_repertoire_comparisons AS SELECT * FROM repertoire_comparisons;
CREATE VIEW current_repertoire_decision_events AS SELECT * FROM repertoire_decision_events;
CREATE VIEW current_game_findings AS SELECT * FROM game_findings WHERE kind NOT IN ('repertoire lapse','repertoire gap') OR game_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1);
CREATE FUNCTION stamp_new_finding_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.game_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1);
    RETURN NEW;
END $$;
CREATE TRIGGER canonical_finding_insert BEFORE INSERT ON game_findings FOR EACH ROW EXECUTE FUNCTION stamp_new_finding_scope();
CREATE VIEW current_gameplay_card_priorities AS SELECT priority.* FROM gameplay_card_priorities priority WHERE priority.finding_id IS NULL OR EXISTS(SELECT 1 FROM current_game_findings finding WHERE finding.id=priority.finding_id);
CREATE FUNCTION stamp_new_opportunity_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.game_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1);
    RETURN NEW;
END $$;
CREATE TRIGGER canonical_opportunity_insert BEFORE INSERT ON repertoire_opportunities FOR EACH ROW EXECUTE FUNCTION stamp_new_opportunity_scope();
UPDATE repertoire_card_priority_generations SET evidence_json=(evidence_json::jsonb || jsonb_build_object('game_scope_generation',0))::text;
UPDATE repertoire_card_introduction_priorities SET evidence_json=(evidence_json::jsonb || jsonb_build_object('game_scope_generation',0))::text;
CREATE VIEW current_repertoire_card_priority_generations AS SELECT publication.* FROM repertoire_card_priority_generations publication JOIN repertoires scope ON scope.id=publication.repertoire_id WHERE COALESCE((publication.evidence_json::jsonb->>'canonical_prefix_revision')::bigint,0)=scope.canonical_prefix_revision AND (scope.canonical_prefix_moves_json='[]' OR ((publication.evidence_json::jsonb->>'canonical_scope_source_revision')::bigint=scope.scope_source_revision AND publication.evidence_json::jsonb->>'canonical_scope_preview_id'=scope.canonical_prefix_preview_id)) AND COALESCE((publication.evidence_json::jsonb->>'game_scope_generation')::bigint,0)=(SELECT generation FROM repertoire_game_scope WHERE id=1);
CREATE VIEW current_repertoire_card_introduction_priorities AS SELECT publication.* FROM repertoire_card_introduction_priorities publication JOIN repertoires scope ON scope.id=publication.repertoire_id WHERE COALESCE((publication.evidence_json::jsonb->>'canonical_prefix_revision')::bigint,0)=scope.canonical_prefix_revision AND (scope.canonical_prefix_moves_json='[]' OR ((publication.evidence_json::jsonb->>'canonical_scope_source_revision')::bigint=scope.scope_source_revision AND publication.evidence_json::jsonb->>'canonical_scope_preview_id'=scope.canonical_prefix_preview_id)) AND COALESCE((publication.evidence_json::jsonb->>'game_scope_generation')::bigint,0)=(SELECT generation FROM repertoire_game_scope WHERE id=1);
CREATE VIEW current_repertoire_opportunities AS SELECT opportunity.* FROM repertoire_opportunities opportunity JOIN repertoires scope ON scope.id=opportunity.repertoire_id WHERE opportunity.canonical_prefix_revision=scope.canonical_prefix_revision AND (scope.canonical_prefix_moves_json='[]' OR (opportunity.canonical_scope_source_revision=scope.scope_source_revision AND opportunity.canonical_scope_preview_id=scope.canonical_prefix_preview_id)) AND opportunity.game_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1);
INSERT INTO tempo_schema_migrations(version) VALUES (35);
