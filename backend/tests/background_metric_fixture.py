"""One-table fixture for durable task SQL doubles; no runtime bootstrap."""

BACKGROUND_METRIC_SCHEMA = """
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
