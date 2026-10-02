CREATE TABLE IF NOT EXISTS integrity_recommendation_requests (
    issue_id TEXT PRIMARY KEY REFERENCES repertoire_integrity_issues(id) ON DELETE CASCADE,
    repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
    signature TEXT NOT NULL,
    scan_generation TEXT,
    graph_generation INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('waiting','ready','unavailable','failed')),
    task_id TEXT NOT NULL,
    request_id TEXT REFERENCES threat_analysis_requests(id) ON DELETE SET NULL,
    source_json TEXT,
    accumulation_json TEXT,
    preview_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_integrity_recommendation_request ON integrity_recommendation_requests(request_id,issue_id);
INSERT INTO tempo_schema_migrations(version) VALUES (30);
