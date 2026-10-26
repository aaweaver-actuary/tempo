-- Games-owned optional synchronization; no Study tables or automatic activation.

CREATE TABLE product_feedback_jobs (
    job_id TEXT PRIMARY KEY, version BIGINT NOT NULL, status TEXT NOT NULL,
    next_attempt_at DOUBLE PRECISION NOT NULL, lease_until DOUBLE PRECISION NOT NULL,
    attempts INTEGER NOT NULL, payload JSONB NOT NULL
);
CREATE TABLE product_feedback_pages (
    job_id TEXT NOT NULL REFERENCES product_feedback_jobs(job_id),
    page_key TEXT NOT NULL, input_continuation TEXT, page_json JSONB NOT NULL,
    PRIMARY KEY (job_id, page_key)
);
CREATE TABLE product_feedback_sources (
    source_key TEXT PRIMARY KEY, version BIGINT NOT NULL, payload JSONB NOT NULL
);
CREATE TABLE product_feedback_captures (
    source_key TEXT NOT NULL REFERENCES product_feedback_sources(source_key),
    source_revision BIGINT NOT NULL, source_digest TEXT NOT NULL,
    comparison_revision TEXT NOT NULL, payload JSONB NOT NULL,
    PRIMARY KEY (source_key, source_revision, source_digest, comparison_revision)
);
CREATE TABLE product_feedback_deliveries (
    operation_id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL REFERENCES product_feedback_sources(source_key),
    effect_key TEXT NOT NULL, command_kind TEXT NOT NULL, state TEXT NOT NULL,
    next_attempt_at DOUBLE PRECISION NOT NULL, payload JSONB NOT NULL
);
CREATE INDEX product_feedback_ready_jobs ON product_feedback_jobs(status, next_attempt_at, attempts);
CREATE INDEX product_feedback_ready_deliveries ON product_feedback_deliveries(state, next_attempt_at);

CREATE TABLE product_feedback_configuration (
    workspace_id TEXT NOT NULL, provider TEXT NOT NULL,
    revision BIGINT NOT NULL, next_run_at DOUBLE PRECISION NOT NULL,
    payload JSONB NOT NULL, PRIMARY KEY(workspace_id,provider)
);
CREATE TABLE product_feedback_sync_requests (
    workspace_id TEXT NOT NULL, operation_id TEXT NOT NULL,
    provider TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES product_feedback_jobs(job_id),
    requested_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(workspace_id,operation_id)
);
CREATE INDEX product_feedback_latest_request
    ON product_feedback_sync_requests(workspace_id,provider,requested_at DESC);
CREATE TABLE product_feedback_retry_requests (
    workspace_id TEXT NOT NULL, operation_id TEXT NOT NULL,
    source_key TEXT NOT NULL REFERENCES product_feedback_sources(source_key),
    delivery_id TEXT NOT NULL REFERENCES product_feedback_deliveries(operation_id),
    finding_digest TEXT NOT NULL, PRIMARY KEY(workspace_id,operation_id)
);
CREATE INDEX product_feedback_source_game ON product_feedback_sources
    ((payload->'normalized'->>'workspace_id'),(payload->'normalized'->>'source_id'),
     (payload->'normalized'->>'game_id'));

INSERT INTO tempo_schema_migrations(version) VALUES(52);
