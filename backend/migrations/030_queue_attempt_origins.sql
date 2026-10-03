-- Admission proof survives queue deletion/replacement. No cascading foreign key:
-- removed content must produce an explicit conflict rather than erase its origin.
CREATE TABLE queue_attempt_origins (
    queue_entry_id BIGINT NOT NULL, card_id TEXT NOT NULL, revision BIGINT NOT NULL,
    queue_date TEXT NOT NULL, cycle BIGINT NOT NULL, admission_kind TEXT,
    admission_repertoire_id TEXT, attempt_failed BIGINT NOT NULL DEFAULT 0,
    last_status TEXT NOT NULL, review_result_json TEXT, legacy BIGINT NOT NULL DEFAULT 0,
    start_fen TEXT NOT NULL, moves_json TEXT NOT NULL, trained_color TEXT, content_type TEXT NOT NULL,
    PRIMARY KEY(queue_entry_id,card_id,revision)
);
CREATE INDEX idx_queue_attempt_origin_cycle ON queue_attempt_origins(queue_date,card_id,cycle);
ALTER TABLE review_attempt_receipts ADD COLUMN request_json TEXT;

INSERT INTO queue_attempt_origins
SELECT q.id,q.card_id,c.revision,q.queue_date,q.cycle,q.admission_kind,
       q.admission_repertoire_id,q.attempt_failed,q.status,q.review_result_json,1,
       c.start_fen,c.moves_json,c.trained_color,c.content_type
FROM daily_queue q JOIN cards c ON c.id=q.card_id;

CREATE FUNCTION retain_queue_attempt_origin() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE queue_row daily_queue;
BEGIN
    IF TG_OP='DELETE' THEN queue_row := OLD; ELSE queue_row := NEW; END IF;
    INSERT INTO queue_attempt_origins
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

CREATE TRIGGER queue_attempt_origin_insert AFTER INSERT ON daily_queue
FOR EACH ROW EXECUTE FUNCTION retain_queue_attempt_origin();
CREATE TRIGGER queue_attempt_origin_update AFTER UPDATE OF card_id,attempt_failed,status,review_result_json,admission_kind,admission_repertoire_id ON daily_queue
FOR EACH ROW EXECUTE FUNCTION retain_queue_attempt_origin();
CREATE TRIGGER queue_attempt_origin_delete BEFORE DELETE ON daily_queue
FOR EACH ROW EXECUTE FUNCTION retain_queue_attempt_origin();

CREATE FUNCTION retain_queue_attempt_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO queue_attempt_origins
    SELECT q.id,NEW.id,NEW.revision,q.queue_date,q.cycle,q.admission_kind,
           q.admission_repertoire_id,q.attempt_failed,q.status,q.review_result_json,0,
           NEW.start_fen,NEW.moves_json,NEW.trained_color,NEW.content_type
    FROM daily_queue q WHERE q.card_id=NEW.id AND q.status='queued'
    ON CONFLICT DO NOTHING;
    RETURN NEW;
END $$;
CREATE TRIGGER queue_attempt_origin_revision AFTER UPDATE OF revision ON cards
FOR EACH ROW WHEN (NEW.revision IS DISTINCT FROM OLD.revision)
EXECUTE FUNCTION retain_queue_attempt_revision();

INSERT INTO tempo_schema_migrations(version) VALUES (30);
