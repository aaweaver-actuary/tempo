-- Admission is not issuance. Historical origins remain unissued.
ALTER TABLE queue_attempt_origins ADD COLUMN issued_as_head BIGINT NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION retain_queue_attempt_origin() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE queue_row daily_queue;
BEGIN
    IF TG_OP='DELETE' THEN queue_row := OLD; ELSE queue_row := NEW; END IF;
    INSERT INTO queue_attempt_origins(queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,admission_repertoire_id,attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type)
    SELECT queue_row.id,queue_row.card_id,c.revision,queue_row.queue_date,queue_row.cycle,
           queue_row.admission_kind,queue_row.admission_repertoire_id,queue_row.attempt_failed,
           queue_row.status,queue_row.review_result_json,0,
           c.start_fen,c.moves_json,c.trained_color,c.content_type
    FROM cards c WHERE c.id=queue_row.card_id
    ON CONFLICT(queue_entry_id,card_id,revision) DO UPDATE SET
        attempt_failed=GREATEST(queue_attempt_origins.attempt_failed,excluded.attempt_failed),
        admission_kind=COALESCE(queue_attempt_origins.admission_kind,excluded.admission_kind),
        admission_repertoire_id=COALESCE(queue_attempt_origins.admission_repertoire_id,excluded.admission_repertoire_id),
        last_status=excluded.last_status,
        review_result_json=COALESCE(excluded.review_result_json,queue_attempt_origins.review_result_json);
    IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;

CREATE OR REPLACE FUNCTION retain_queue_attempt_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Guidance belongs to the displayed revision; keep its retained origin but
    -- reset unfinished projections before their new content origin is captured.
    UPDATE daily_queue SET attempt_failed=0
    WHERE card_id=NEW.id AND status!='complete' AND attempt_failed!=0;
    INSERT INTO queue_attempt_origins(queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,admission_repertoire_id,attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type)
    SELECT q.id,NEW.id,NEW.revision,q.queue_date,q.cycle,q.admission_kind,
           q.admission_repertoire_id,q.attempt_failed,q.status,q.review_result_json,0,
           NEW.start_fen,NEW.moves_json,NEW.trained_color,NEW.content_type
    FROM daily_queue q WHERE q.card_id=NEW.id AND q.status='queued'
    ON CONFLICT DO NOTHING;
    RETURN NEW;
END $$;

INSERT INTO tempo_schema_migrations(version) VALUES (42);
