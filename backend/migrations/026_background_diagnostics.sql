-- Bounded observability only; no queue eligibility or scheduling changes.
ALTER TABLE background_tasks ADD COLUMN replaced_pending_generation BIGINT NOT NULL DEFAULT 0;
ALTER TABLE background_tasks ADD COLUMN pending_since TEXT;
ALTER TABLE background_tasks ADD COLUMN generation_started_at TEXT;
ALTER TABLE background_tasks ADD COLUMN age_origin_estimated BIGINT NOT NULL DEFAULT 0;
UPDATE background_tasks SET pending_since=created_at,generation_started_at=updated_at,age_origin_estimated=1;
CREATE INDEX idx_background_tasks_diagnostic_state ON background_tasks(state,next_attempt_at,pending_since,kind,id);
CREATE INDEX idx_background_tasks_diagnostic_age ON background_tasks(state,pending_since,next_attempt_at,id);
CREATE FUNCTION background_task_age_origins() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        NEW.pending_since=COALESCE(NEW.pending_since,NEW.created_at);
        NEW.generation_started_at=COALESCE(NEW.generation_started_at,NEW.created_at);
    ELSIF NEW.generation<>OLD.generation THEN
        NEW.replaced_pending_generation=CASE WHEN OLD.state IN ('complete','superseded') THEN 0 ELSE 1 END;
        NEW.generation_started_at=NEW.updated_at;
        IF OLD.state IN ('complete','superseded') THEN
            NEW.pending_since=NEW.updated_at;
            NEW.age_origin_estimated=0;
        ELSE
            NEW.pending_since=COALESCE(OLD.pending_since,OLD.created_at);
        END IF;
    ELSE
        NEW.pending_since=COALESCE(OLD.pending_since,OLD.created_at);
        NEW.generation_started_at=COALESCE(OLD.generation_started_at,OLD.created_at);
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER background_task_age_origins BEFORE INSERT OR UPDATE ON background_tasks
FOR EACH ROW EXECUTE FUNCTION background_task_age_origins();
CREATE TABLE background_metric_buckets (
    kind TEXT NOT NULL,
    shard BIGINT NOT NULL CHECK(shard>=0 AND shard<16),
    slot BIGINT NOT NULL CHECK(slot>=0 AND slot<288),
    bucket_start TEXT NOT NULL,
    generations_started BIGINT NOT NULL DEFAULT 0 CHECK (generations_started>=0),
    generation_replacements BIGINT NOT NULL DEFAULT 0 CHECK (generation_replacements>=0),
    generation_restarts BIGINT NOT NULL DEFAULT 0 CHECK (generation_restarts>=0),
    claims BIGINT NOT NULL DEFAULT 0 CHECK (claims>=0),
    slices BIGINT NOT NULL DEFAULT 0 CHECK (slices>=0),
    retries BIGINT NOT NULL DEFAULT 0 CHECK (retries>=0),
    completed_generations BIGINT NOT NULL DEFAULT 0 CHECK (completed_generations>=0),
    useful_completions BIGINT NOT NULL DEFAULT 0 CHECK (useful_completions>=0),
    stale_deliveries BIGINT NOT NULL DEFAULT 0 CHECK (stale_deliveries>=0),
    stale_results BIGINT NOT NULL DEFAULT 0 CHECK (stale_results>=0),
    lease_expiries BIGINT NOT NULL DEFAULT 0 CHECK (lease_expiries>=0),
    lease_reclaims BIGINT NOT NULL DEFAULT 0 CHECK (lease_reclaims>=0),
    contention_deferrals BIGINT NOT NULL DEFAULT 0 CHECK (contention_deferrals>=0),
    engine_completed_positions BIGINT NOT NULL DEFAULT 0 CHECK (engine_completed_positions>=0),
    engine_preemptions BIGINT NOT NULL DEFAULT 0 CHECK (engine_preemptions>=0),
    engine_timeouts BIGINT NOT NULL DEFAULT 0 CHECK (engine_timeouts>=0),
    engine_failures BIGINT NOT NULL DEFAULT 0 CHECK (engine_failures>=0),
    priority_calculator_calls BIGINT NOT NULL DEFAULT 0 CHECK (priority_calculator_calls>=0),
    priority_publications BIGINT NOT NULL DEFAULT 0 CHECK (priority_publications>=0),
    priority_published_records BIGINT NOT NULL DEFAULT 0 CHECK (priority_published_records>=0),
    engine_successful_seconds DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(engine_successful_seconds>=0),
    engine_preempted_seconds DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(engine_preempted_seconds>=0),
    engine_abandoned_seconds DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(engine_abandoned_seconds>=0),
    engine_successful_samples BIGINT NOT NULL DEFAULT 0,
    engine_abandoned_samples BIGINT NOT NULL DEFAULT 0,
    engine_unknown_timing_attempts BIGINT NOT NULL DEFAULT 0,
    engine_successful_max_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    engine_preempted_max_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    engine_abandoned_max_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY(kind,shard,slot)
);
CREATE INDEX idx_background_metric_window ON background_metric_buckets(bucket_start,kind);
ALTER TABLE game_analysis_jobs ADD COLUMN age_origin_estimated BIGINT NOT NULL DEFAULT 0;
ALTER TABLE game_analysis_jobs ADD COLUMN pending_since TEXT;
ALTER TABLE game_analysis_jobs ADD COLUMN generation_started_at TEXT;
UPDATE game_analysis_jobs SET pending_since=updated_at,generation_started_at=updated_at,age_origin_estimated=1;
CREATE FUNCTION game_analysis_age_origins() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        NEW.pending_since=COALESCE(NEW.pending_since,NEW.updated_at);
        NEW.generation_started_at=COALESCE(NEW.generation_started_at,NEW.updated_at);
    ELSIF NEW.analysis_version<>OLD.analysis_version OR NEW.analysis_evidence_version<>OLD.analysis_evidence_version THEN
        NEW.generation_started_at=NEW.updated_at;
        NEW.pending_since=CASE WHEN OLD.status='complete' THEN NEW.updated_at ELSE OLD.pending_since END;
        NEW.age_origin_estimated=CASE WHEN OLD.status='complete' THEN 0 ELSE OLD.age_origin_estimated END;
    ELSE
        NEW.pending_since=OLD.pending_since;
        NEW.generation_started_at=OLD.generation_started_at;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER game_analysis_age_origins BEFORE INSERT OR UPDATE ON game_analysis_jobs
FOR EACH ROW EXECUTE FUNCTION game_analysis_age_origins();
CREATE INDEX idx_game_analysis_diagnostic_state ON game_analysis_jobs(status,pending_since,game_id);
CREATE INDEX idx_threat_analysis_diagnostic_state ON threat_analysis_requests(state,created_at,id);
CREATE TABLE background_diagnostic_metadata (
    id BIGINT PRIMARY KEY CHECK(id=1),
    collection_started_at TEXT NOT NULL
);
INSERT INTO background_diagnostic_metadata(id,collection_started_at)
VALUES(1,to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00');
-- Existing compact enqueue histories predate the shared cap. Prune once during
-- this stopped-writer migration; request paths never sweep historical events.
DELETE FROM background_task_events WHERE id IN (
    SELECT id FROM (
        SELECT id,row_number() OVER(PARTITION BY task_id ORDER BY id DESC) AS ordinal
        FROM background_task_events
    ) retained WHERE ordinal>100
);
INSERT INTO tempo_schema_migrations(version) VALUES(26);
