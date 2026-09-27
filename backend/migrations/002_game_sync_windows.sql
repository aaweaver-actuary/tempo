-- Durable provider fetch windows; each published record is a separate task.
CREATE TABLE game_sync_windows (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES game_sync_jobs(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('lichess', 'chess.com')),
    window_kind TEXT NOT NULL CHECK (window_kind IN ('archive_index', 'games')),
    window_start_ms BIGINT NOT NULL,
    window_end_ms BIGINT NOT NULL,
    source_url TEXT,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'staged', 'draining', 'complete', 'split', 'failed')),
    records_json TEXT,
    source_digest TEXT,
    next_record_index BIGINT NOT NULL DEFAULT 0,
    total_records BIGINT NOT NULL DEFAULT 0,
    fetched_count BIGINT NOT NULL DEFAULT 0,
    filtered_count BIGINT NOT NULL DEFAULT 0,
    rejected_count BIGINT NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (window_end_ms > window_start_ms),
    CHECK (next_record_index >= 0 AND next_record_index <= total_records),
    UNIQUE (job_id, provider, window_kind, window_start_ms, window_end_ms)
);
CREATE INDEX idx_game_sync_windows_job_status
    ON game_sync_windows(job_id, status, window_start_ms);
INSERT INTO tempo_schema_migrations(version) VALUES (2);
