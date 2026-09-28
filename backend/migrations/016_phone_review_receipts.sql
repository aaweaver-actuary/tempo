CREATE TABLE review_attempt_receipts (
    attempt_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    queue_entry_id BIGINT,
    outcome TEXT NOT NULL,
    guided BIGINT NOT NULL,
    completed_at TEXT,
    review_id BIGINT,
    scheduling_status TEXT NOT NULL,
    warning TEXT,
    result_json TEXT NOT NULL
);

CREATE TABLE review_schedule_snapshots (
    review_id BIGINT PRIMARY KEY,
    state_json TEXT NOT NULL
);

INSERT INTO tempo_schema_migrations(version) VALUES (16);
