-- Explicit terminal abandonment preserves the original attempt and its history.
CREATE TABLE queue_attempt_abandonments (
    queue_entry_id BIGINT NOT NULL,
    card_id TEXT NOT NULL,
    revision BIGINT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    request_sha256 TEXT NOT NULL,
    origin_sha256 TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    abandoned_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (queue_entry_id, card_id, revision),
    FOREIGN KEY (queue_entry_id, card_id, revision)
        REFERENCES queue_attempt_origins(queue_entry_id, card_id, revision),
    CHECK (request_sha256 ~ '^[0-9a-f]{64}$' AND origin_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (queue_entry_id > 0 AND revision > 0),
    CHECK (length(actor) > 0 AND length(reason) > 0 AND length(operation_id) > 0)
);
INSERT INTO tempo_schema_migrations(version) VALUES (58);

CREATE FUNCTION protect_queue_attempt_abandonment() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'queue attempt abandonment receipts are immutable' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER queue_attempt_abandonment_immutable
    BEFORE UPDATE OR DELETE ON queue_attempt_abandonments
    FOR EACH ROW EXECUTE FUNCTION protect_queue_attempt_abandonment();

CREATE FUNCTION protect_abandoned_queue_origin() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM queue_attempt_abandonments a
        WHERE a.queue_entry_id=OLD.queue_entry_id AND a.card_id=OLD.card_id AND a.revision=OLD.revision)
        AND (TG_OP = 'DELETE' OR NEW IS DISTINCT FROM OLD) THEN
        RAISE EXCEPTION 'abandoned queue attempt origin is immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER queue_attempt_abandoned_origin_immutable
    BEFORE UPDATE OR DELETE ON queue_attempt_origins
    FOR EACH ROW EXECUTE FUNCTION protect_abandoned_queue_origin();

-- Queue projections remain replaceable. Once abandoned, their trigger must
-- leave the exact retained origin untouched while allowing projection changes.
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
        review_result_json=COALESCE(excluded.review_result_json,queue_attempt_origins.review_result_json)
    WHERE NOT EXISTS (SELECT 1 FROM queue_attempt_abandonments a
        WHERE a.queue_entry_id=queue_attempt_origins.queue_entry_id
          AND a.card_id=queue_attempt_origins.card_id AND a.revision=queue_attempt_origins.revision);
    IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
