-- Keep historical intent IDs and task/receipt references intact while permitting
-- the same move to be accepted again for a new evidence revision.
ALTER TABLE discovery_admission_intents
    DROP CONSTRAINT discovery_admission_intents_opportunity_id_selected_move_uc_key;
ALTER TABLE discovery_admission_intents
    ADD CONSTRAINT discovery_admission_intents_revision_choice_key
    UNIQUE (opportunity_id, selected_move_uci, evidence_fingerprint);
INSERT INTO tempo_schema_migrations(version) VALUES (28);
