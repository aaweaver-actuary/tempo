-- Explicit Endgames retirement. Apply through the existing owner migration path
-- with writers stopped; this migration does not acquire another writer authority.
-- Immutable operation/review receipts and deleted-card identities remain replay fences.
CREATE TEMP TABLE retired_endgame_cards ON COMMIT DROP AS
SELECT id FROM cards WHERE content_type='endgame';
CREATE TEMP TABLE retired_endgame_days ON COMMIT DROP AS
SELECT DISTINCT queue_date FROM daily_queue WHERE card_id IN (SELECT id FROM retired_endgame_cards);
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM cards WHERE repertoire_id='__endgames__' AND content_type<>'endgame')
       OR EXISTS(SELECT 1 FROM endgame_templates e JOIN cards c ON c.id=e.card_id WHERE c.content_type<>'endgame')
       OR EXISTS(SELECT 1 FROM opening_graph_steps WHERE card_id IN (SELECT id FROM retired_endgame_cards)
                 OR parent_card_id IN (SELECT id FROM retired_endgame_cards)) THEN
        RAISE EXCEPTION 'Retired practice has cross-family ownership; migration rolled back';
    END IF;
END $$;
UPDATE operation_receipts SET state='failed',
    error_json='{"status_code":410,"detail":"Practice family retired","message":"Practice family retired","retryable":false}',
    updated_at=NOW()
WHERE command_name IN ('endgames.template.create','endgames.attempt.create') AND state NOT IN ('complete','failed');
INSERT INTO deleted_cards(card_id,deleted_at)
SELECT id, CURRENT_TIMESTAMP::text FROM retired_endgame_cards ON CONFLICT(card_id) DO NOTHING;
DELETE FROM study_attempts WHERE card_id IN (SELECT id FROM retired_endgame_cards)
    OR queue_entry_id IN (SELECT id FROM daily_queue WHERE card_id IN (SELECT id FROM retired_endgame_cards));
DELETE FROM card_revisions WHERE card_id IN (SELECT id FROM retired_endgame_cards);
DELETE FROM queue_projection_diagnostics WHERE card_id IN (SELECT id FROM retired_endgame_cards);
DELETE FROM review_schedule_snapshots WHERE review_id IN
    (SELECT id FROM reviews WHERE card_id IN (SELECT id FROM retired_endgame_cards));
DELETE FROM repertoire_cards WHERE card_id IN (SELECT id FROM retired_endgame_cards);
DELETE FROM cards WHERE id IN (SELECT id FROM retired_endgame_cards);
-- Deletion triggers may capture origins: remove the retired bodies after cascades.
DELETE FROM queue_attempt_origins WHERE content_type='endgame' OR card_id IN (SELECT id FROM retired_endgame_cards);
DELETE FROM repertoire_lines WHERE repertoire_id='__endgames__';
DELETE FROM repertoires WHERE id='__endgames__';
DROP TABLE endgame_attempts;
DROP TABLE endgame_templates;
DROP TABLE tablebase_cache;
ALTER TABLE cards ADD CONSTRAINT retired_practice_family CHECK(content_type<>'endgame');
UPDATE queue_projections SET refresh_pending=1 WHERE queue_date IN (SELECT queue_date FROM retired_endgame_days);
ALTER TABLE settings DROP COLUMN draw_hold_user_moves;
CREATE OR REPLACE FUNCTION advance_repertoire_game_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END) IN ('__tactics__','__game_mistakes__','__game_tactics__','__captured_tactics__','__retained_cards__') THEN RETURN NULL; END IF;
    IF TG_OP='UPDATE' AND ROW(OLD.canonical_prefix_moves_json,OLD.scope_source_revision,OLD.is_main) IS NOT DISTINCT FROM ROW(NEW.canonical_prefix_moves_json,NEW.scope_source_revision,NEW.is_main) THEN RETURN NULL; END IF;
    UPDATE repertoire_game_scope SET generation=generation+1 WHERE id=1;
    -- Existing priority generations also consume repertoire classification evidence.
    UPDATE priority_source_epoch SET version=version+1 WHERE id=1;
    RETURN NULL;
END $$;

INSERT INTO tempo_schema_migrations(version) VALUES (55);
