-- Advisory projections only; no card, review, or daily queue updates.
CREATE TABLE opening_segmentation_runs (
    id TEXT PRIMARY KEY,
    repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE
);
CREATE TABLE opening_segmentation_state (
    repertoire_id TEXT PRIMARY KEY REFERENCES repertoires(id) ON DELETE CASCADE,
    content_version BIGINT NOT NULL DEFAULT 0,
    graph_generation BIGINT,
    run_id TEXT,
    state TEXT NOT NULL DEFAULT 'stale' CHECK (state IN ('stale','building','ready')),
    published_at TEXT
);
CREATE TABLE opening_segmentation_occurrences (
    run_id TEXT NOT NULL REFERENCES opening_segmentation_runs(id) ON DELETE CASCADE,
    card_id TEXT NOT NULL,
    decision_index BIGINT NOT NULL,
    trunk_key TEXT NOT NULL,
    suffix_key TEXT NOT NULL,
    occurrence_json TEXT NOT NULL,
    PRIMARY KEY(run_id,card_id,decision_index)
);
CREATE INDEX idx_segmentation_occurrence_trunk ON opening_segmentation_occurrences(run_id,trunk_key,card_id,decision_index);
CREATE INDEX idx_segmentation_occurrence_suffix ON opening_segmentation_occurrences(run_id,suffix_key,card_id,decision_index);
CREATE TABLE opening_segmentation_groups (
    run_id TEXT NOT NULL REFERENCES opening_segmentation_runs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    group_key TEXT NOT NULL,
    source_count BIGINT NOT NULL DEFAULT 0,
    decisions_before BIGINT NOT NULL DEFAULT 0,
    segment_count BIGINT NOT NULL DEFAULT 0,
    decisions_after BIGINT NOT NULL DEFAULT 0,
    first_discriminator TEXT,
    diverse BOOLEAN NOT NULL DEFAULT FALSE,
    source_fingerprint TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(run_id,kind,group_key)
);
CREATE TABLE opening_segmentation_parts (
    run_id TEXT NOT NULL REFERENCES opening_segmentation_runs(id) ON DELETE CASCADE,
    recommendation_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    tested_decisions BIGINT NOT NULL,
    segment_json TEXT NOT NULL,
    PRIMARY KEY(run_id,recommendation_id,segment_id)
);
CREATE TABLE opening_segmentation_sources (
    run_id TEXT NOT NULL REFERENCES opening_segmentation_runs(id) ON DELETE CASCADE,
    recommendation_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    decision_count BIGINT NOT NULL,
    discriminator TEXT NOT NULL,
    PRIMARY KEY(run_id,recommendation_id,card_id)
);
CREATE TABLE opening_segmentation_recommendations (
    run_id TEXT NOT NULL REFERENCES opening_segmentation_runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    decisions_before BIGINT NOT NULL,
    decisions_after BIGINT NOT NULL,
    decisions_avoided BIGINT NOT NULL,
    additional_starts BIGINT NOT NULL,
    segment_count BIGINT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    PRIMARY KEY(run_id,id)
);
CREATE INDEX idx_segmentation_recommendation_rank ON opening_segmentation_recommendations(run_id,decisions_avoided DESC,additional_starts,segment_count,id);
CREATE TABLE opening_segmentation_preferences (
    repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
    recommendation_id TEXT NOT NULL,
    choice TEXT NOT NULL CHECK(choice IN ('dismissed','keep_current')),
    source_fingerprint TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(repertoire_id,recommendation_id)
);
INSERT INTO tempo_schema_migrations(version) VALUES (24);
