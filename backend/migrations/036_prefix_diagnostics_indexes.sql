-- Read indexes only: no scheduling, presentation, or shadow fact changes.
CREATE INDEX opening_evidence_presentation_attempt_window ON opening_evidence_attempts
    (presentation_snapshot_id,repertoire_id,trained_color,started_at DESC,attempt_id DESC);
CREATE INDEX opening_graph_multi_prefix_page ON opening_graph_steps
    (repertoire_id,generation,card_id,trained_color)
    WHERE segment_kind='prefix' AND last_decision_index>first_decision_index;
INSERT INTO tempo_schema_migrations(version) VALUES (36);
