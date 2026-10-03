-- Shadow facts only. None of these tables or triggers writes scheduling state.
CREATE TABLE opening_evidence_presentations (
    id BIGSERIAL PRIMARY KEY,
    content_fingerprint TEXT NOT NULL UNIQUE,
    card_id TEXT NOT NULL,
    revision BIGINT NOT NULL,
    start_fen TEXT NOT NULL,
    moves_json TEXT NOT NULL,
    trained_color TEXT,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX opening_evidence_presentation_card ON opening_evidence_presentations(card_id,revision);

CREATE FUNCTION opening_evidence_capture_presentation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.content_type='opening' THEN
        INSERT INTO opening_evidence_presentations(content_fingerprint,card_id,revision,start_fen,moves_json,trained_color)
        VALUES(md5(jsonb_build_array(NEW.id,NEW.revision,NEW.start_fen,NEW.moves_json,NEW.trained_color)::text),
               NEW.id,NEW.revision,NEW.start_fen,NEW.moves_json,NEW.trained_color)
        ON CONFLICT(content_fingerprint) DO NOTHING;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER opening_evidence_saved_presentation AFTER INSERT OR UPDATE OF
    revision,start_fen,moves_json,trained_color,content_type ON cards
    FOR EACH ROW EXECUTE FUNCTION opening_evidence_capture_presentation();
INSERT INTO opening_evidence_presentations(content_fingerprint,card_id,revision,start_fen,moves_json,trained_color)
SELECT md5(jsonb_build_array(id,revision,start_fen,moves_json,trained_color)::text),id,revision,start_fen,moves_json,trained_color
FROM cards WHERE content_type='opening' ON CONFLICT DO NOTHING;

-- Preserve the actual admission/presentation scope even if a card is replaced.
CREATE TABLE opening_evidence_queue_contexts (
    queue_entry_id BIGINT NOT NULL,
    presentation_snapshot_id BIGINT NOT NULL REFERENCES opening_evidence_presentations(id),
    repertoire_id TEXT NOT NULL,
    effective_trained_color TEXT NOT NULL CHECK(effective_trained_color IN ('white','black')),
    PRIMARY KEY(queue_entry_id,presentation_snapshot_id,repertoire_id)
);
CREATE FUNCTION opening_evidence_bind_queue(queue_identifier BIGINT, card_identifier TEXT, admission_repertoire TEXT)
RETURNS void LANGUAGE sql AS $$
    INSERT INTO opening_evidence_queue_contexts(queue_entry_id,presentation_snapshot_id,repertoire_id,effective_trained_color)
    SELECT queue_identifier,snapshot.id,scope.repertoire_id,learner.effective_trained_color
    FROM cards card JOIN opening_evidence_presentations snapshot
      ON snapshot.card_id=card.id AND snapshot.revision=card.revision
      AND snapshot.start_fen=card.start_fen AND snapshot.moves_json=card.moves_json
      AND snapshot.trained_color IS NOT DISTINCT FROM card.trained_color
    CROSS JOIN LATERAL (
      SELECT candidate.repertoire_id FROM (
        SELECT card.repertoire_id UNION SELECT link.repertoire_id FROM repertoire_cards link WHERE link.card_id=card.id
      ) candidate JOIN repertoires repertoire ON repertoire.id=candidate.repertoire_id
      WHERE NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
        WHERE block.card_id=card.id AND block.repertoire_id=candidate.repertoire_id)
      AND (candidate.repertoire_id=admission_repertoire OR
        (admission_repertoire IS NULL AND (SELECT COUNT(*) FROM (
          SELECT card.repertoire_id UNION SELECT link.repertoire_id FROM repertoire_cards link WHERE link.card_id=card.id
        ) owners JOIN repertoires existing ON existing.id=owners.repertoire_id
          WHERE NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
            WHERE block.card_id=card.id AND block.repertoire_id=owners.repertoire_id))=1))
    ) scope
    -- Legacy color belongs to this proven scope, not to the raw presentation.
    -- Capture once: repertoire edits must not change historical checkpoint identity.
    CROSS JOIN LATERAL (
      SELECT COALESCE(card.trained_color,(SELECT line.trained_color FROM repertoire_lines line
        WHERE line.repertoire_id=scope.repertoire_id ORDER BY line.created_at,line.id LIMIT 1)) effective_trained_color
    ) learner
    WHERE card.id=card_identifier AND card.content_type='opening'
      AND learner.effective_trained_color IN ('white','black')
    ON CONFLICT DO NOTHING;
$$;
CREATE FUNCTION opening_evidence_capture_queue_context() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM opening_evidence_bind_queue(NEW.id,NEW.card_id,NEW.admission_repertoire_id);
    RETURN NEW;
END $$;
CREATE TRIGGER opening_evidence_queue_scope AFTER INSERT OR UPDATE OF card_id,admission_repertoire_id ON daily_queue
    FOR EACH ROW EXECUTE FUNCTION opening_evidence_capture_queue_context();
-- Content edits snapshot the new presentation and scope without changing any queue row.
CREATE FUNCTION opening_evidence_capture_edited_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM opening_evidence_bind_queue(queue.id,NEW.id,queue.admission_repertoire_id)
      FROM daily_queue queue WHERE queue.card_id=NEW.id AND queue.status='queued';
    RETURN NEW;
END $$;
CREATE TRIGGER opening_evidence_edited_scope AFTER UPDATE OF revision,start_fen,moves_json,trained_color,content_type ON cards
    FOR EACH ROW EXECUTE FUNCTION opening_evidence_capture_edited_scope();
-- PostgreSQL invokes same-kind triggers alphabetically: the saved snapshot must exist first.
ALTER TRIGGER opening_evidence_saved_presentation ON cards RENAME TO opening_evidence_01_saved_presentation;
SELECT opening_evidence_bind_queue(id,card_id,admission_repertoire_id) FROM daily_queue;

CREATE TABLE opening_evidence_attempts (
    attempt_id TEXT PRIMARY KEY,
    manifest_id TEXT NOT NULL,
    presentation_snapshot_id BIGINT NOT NULL REFERENCES opening_evidence_presentations(id),
    repertoire_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    card_revision BIGINT NOT NULL,
    trained_color TEXT NOT NULL,
    context_json TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    study_timezone TEXT NOT NULL,
    queue_entry_id BIGINT,
    contiguous_sequence BIGINT NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','partial','complete')),
    terminal_json TEXT,
    completed_at TIMESTAMPTZ,
    review_id BIGINT,
    review_result_json TEXT
);
CREATE TABLE opening_evidence_events (
    attempt_id TEXT NOT NULL REFERENCES opening_evidence_attempts(attempt_id) ON DELETE CASCADE,
    sequence BIGINT NOT NULL CHECK(sequence BETWEEN 1 AND 256),
    event_json TEXT NOT NULL,
    PRIMARY KEY(attempt_id,sequence)
);
CREATE TABLE opening_evidence_observations (
    attempt_id TEXT NOT NULL REFERENCES opening_evidence_attempts(attempt_id) ON DELETE CASCADE,
    decision_index BIGINT NOT NULL,
    decision_id TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    failure_at TIMESTAMPTZ,
    observation_json TEXT NOT NULL,
    PRIMARY KEY(attempt_id,decision_index)
);
CREATE INDEX opening_evidence_recent ON opening_evidence_observations(decision_id,observed_at DESC,attempt_id DESC,decision_index DESC);
CREATE TABLE opening_evidence_summaries (
    decision_id TEXT PRIMARY KEY,
    first_responses BIGINT NOT NULL DEFAULT 0,
    clean_successes BIGINT NOT NULL DEFAULT 0,
    incorrect_responses BIGINT NOT NULL DEFAULT 0,
    assisted_responses BIGINT NOT NULL DEFAULT 0,
    manual_failures BIGINT NOT NULL DEFAULT 0,
    corrections BIGINT NOT NULL DEFAULT 0,
    distinct_clean_days BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE opening_evidence_clean_days (
    decision_id TEXT NOT NULL REFERENCES opening_evidence_summaries(decision_id),
    study_day DATE NOT NULL,
    PRIMARY KEY(decision_id,study_day)
);
INSERT INTO tempo_schema_migrations(version) VALUES(30);
