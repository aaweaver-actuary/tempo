-- Permanent content exclusions carry identity only, never the deleted card body.
CREATE TABLE deleted_cards(card_id TEXT PRIMARY KEY,deleted_at TEXT NOT NULL);
CREATE FUNCTION prevent_deleted_card_recreation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('tempo:card-edit:'||NEW.id,0));
    IF EXISTS(SELECT 1 FROM deleted_cards WHERE card_id=NEW.id) THEN
        RAISE EXCEPTION 'This card was permanently deleted and cannot be recreated'
            USING ERRCODE='23514',CONSTRAINT='deleted_card_content';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER deleted_card_recreation_guard BEFORE INSERT OR UPDATE OF id ON cards
    FOR EACH ROW EXECUTE FUNCTION prevent_deleted_card_recreation();

-- These lookup indexes bound card/repertoire deletion cascades to owned rows.
CREATE INDEX deletion_cards_owner ON cards(repertoire_id);
CREATE INDEX deletion_cards_predecessor ON cards(unlock_after_card_id);
CREATE INDEX deletion_daily_queue_card ON daily_queue(card_id);
CREATE INDEX deletion_graph_source_line ON opening_graph_steps(line_id);
CREATE INDEX deletion_graph_legacy_card ON opening_graph_legacy_mappings(legacy_card_id);
CREATE INDEX deletion_graph_decision_card ON opening_graph_legacy_mappings(decision_card_id);
CREATE INDEX deletion_schedule_seed_source ON opening_card_schedule_seeds(source_card_id);
CREATE INDEX deletion_intro_priority_card ON repertoire_card_introduction_priorities(card_id);
CREATE INDEX deletion_priority_generation_card ON repertoire_card_priority_generations(card_id);
CREATE INDEX deletion_prepared_priority_card ON repertoire_priority_prepared_rows(card_id);
CREATE INDEX deletion_events_card ON repertoire_decision_events_staged(card_id);
CREATE INDEX deletion_opportunity_card ON repertoire_opportunities(card_id);
CREATE INDEX deletion_defense_attempt_card ON defense_attempts(card_id);
CREATE INDEX deletion_recognition_queue ON defense_recognition_submissions(queue_entry_id);
CREATE INDEX deletion_study_attempt_card ON study_attempts(card_id);
CREATE INDEX deletion_study_attempt_queue ON study_attempts(queue_entry_id);
CREATE INDEX deletion_prefix_shortened_card ON prefix_splits(shortened_card_id);
CREATE INDEX deletion_prefix_continuation_card ON prefix_splits(continuation_card_id);
CREATE INDEX deletion_legacy_match_repertoire ON game_repertoire_matches_legacy(repertoire_id);
CREATE INDEX deletion_staged_match_repertoire ON game_repertoire_matches_staged(repertoire_id);
CREATE INDEX deletion_admission_repertoire ON discovery_admission_intents(repertoire_id);
CREATE INDEX deletion_segmentation_repertoire ON opening_segmentation_runs(repertoire_id);
CREATE INDEX deletion_segmentation_recommendation_repertoire ON opening_segmentation_recommendations(repertoire_id);
CREATE INDEX deletion_coverage_repertoire ON repertoire_coverage_nodes(repertoire_id);
CREATE INDEX deletion_presentation_attempt ON opening_evidence_attempts(presentation_snapshot_id);
CREATE INDEX deletion_presentation_context ON opening_evidence_queue_contexts(presentation_snapshot_id);
CREATE OR REPLACE FUNCTION advance_repertoire_game_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END) IN ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__','__retained_cards__') THEN RETURN NULL; END IF;
    IF TG_OP='UPDATE' AND ROW(OLD.canonical_prefix_moves_json,OLD.scope_source_revision,OLD.is_main) IS NOT DISTINCT FROM ROW(NEW.canonical_prefix_moves_json,NEW.scope_source_revision,NEW.is_main) THEN RETURN NULL; END IF;
    UPDATE repertoire_game_scope SET generation=generation+1 WHERE id=1;
    -- Existing priority generations also consume repertoire classification evidence.
    UPDATE priority_source_epoch SET version=version+1 WHERE id=1;
    RETURN NULL;
END $$;

INSERT INTO tempo_schema_migrations(version) VALUES (37);
