"""SQLite compatibility schema for bounded background diagnostics."""
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS background_metric_buckets (
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
    engine_successful_seconds REAL NOT NULL DEFAULT 0,
    engine_preempted_seconds REAL NOT NULL DEFAULT 0,
    engine_abandoned_seconds REAL NOT NULL DEFAULT 0,
    engine_successful_samples BIGINT NOT NULL DEFAULT 0,
    engine_abandoned_samples BIGINT NOT NULL DEFAULT 0,
    engine_unknown_timing_attempts BIGINT NOT NULL DEFAULT 0,
    engine_successful_max_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    engine_preempted_max_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    engine_abandoned_max_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY(kind,shard,slot)
);
CREATE INDEX IF NOT EXISTS idx_background_metric_window ON background_metric_buckets(bucket_start,kind);
"""


def install(database):
    for statement in SCHEMA.split(";"):
        if statement.strip():
            database.execute(statement)
    database.execute("UPDATE background_tasks SET pending_since=created_at,generation_started_at=updated_at,age_origin_estimated=1 WHERE pending_since IS NULL")
    database.execute("CREATE INDEX IF NOT EXISTS idx_background_tasks_diagnostic_state ON background_tasks(state,next_attempt_at,pending_since,kind,id)")
    database.execute("CREATE INDEX IF NOT EXISTS idx_background_tasks_diagnostic_age ON background_tasks(state,pending_since,next_attempt_at,id)")
    database.execute("""CREATE TRIGGER IF NOT EXISTS background_age_insert AFTER INSERT ON background_tasks BEGIN
        UPDATE background_tasks SET pending_since=COALESCE(NEW.pending_since,NEW.created_at),
        generation_started_at=COALESCE(NEW.generation_started_at,NEW.created_at) WHERE id=NEW.id;
    END""")
    database.execute("""CREATE TRIGGER IF NOT EXISTS background_age_generation AFTER UPDATE OF generation ON background_tasks
        WHEN NEW.generation<>OLD.generation BEGIN
        UPDATE background_tasks SET pending_since=CASE WHEN OLD.state IN ('complete','superseded')
            THEN NEW.updated_at ELSE COALESCE(OLD.pending_since,OLD.created_at) END,
        replaced_pending_generation=CASE WHEN OLD.state IN ('complete','superseded') THEN 0 ELSE 1 END,
        generation_started_at=NEW.updated_at,
        age_origin_estimated=CASE WHEN OLD.state IN ('complete','superseded') THEN 0 ELSE OLD.age_origin_estimated END
        WHERE id=NEW.id;
    END""")
    database.execute("UPDATE game_analysis_jobs SET pending_since=updated_at,generation_started_at=updated_at,age_origin_estimated=1 WHERE pending_since IS NULL")
    database.execute("CREATE INDEX IF NOT EXISTS idx_game_analysis_diagnostic_state ON game_analysis_jobs(status,pending_since,game_id)")
    database.execute("CREATE INDEX IF NOT EXISTS idx_threat_analysis_diagnostic_state ON threat_analysis_requests(state,created_at,id)")
    database.execute("""CREATE TRIGGER IF NOT EXISTS game_age_insert AFTER INSERT ON game_analysis_jobs BEGIN
        UPDATE game_analysis_jobs SET pending_since=COALESCE(NEW.pending_since,NEW.updated_at),
        generation_started_at=COALESCE(NEW.generation_started_at,NEW.updated_at) WHERE game_id=NEW.game_id;
    END""")
    database.execute("""CREATE TRIGGER IF NOT EXISTS game_age_generation AFTER UPDATE OF analysis_version,analysis_evidence_version ON game_analysis_jobs
        WHEN NEW.analysis_version<>OLD.analysis_version OR NEW.analysis_evidence_version<>OLD.analysis_evidence_version BEGIN
        UPDATE game_analysis_jobs SET pending_since=CASE WHEN OLD.status='complete' THEN NEW.updated_at ELSE OLD.pending_since END,
        generation_started_at=NEW.updated_at,
        age_origin_estimated=CASE WHEN OLD.status='complete' THEN 0 ELSE OLD.age_origin_estimated END WHERE game_id=NEW.game_id;
    END""")
    database.execute("CREATE TABLE IF NOT EXISTS background_diagnostic_metadata(id INTEGER PRIMARY KEY CHECK(id=1),collection_started_at TEXT NOT NULL)")
    database.execute("INSERT OR IGNORE INTO background_diagnostic_metadata(id,collection_started_at) VALUES(1,?)",(datetime.now(timezone.utc).isoformat(),))
