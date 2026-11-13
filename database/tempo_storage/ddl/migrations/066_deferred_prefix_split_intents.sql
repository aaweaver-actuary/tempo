-- Preserve an accepted manual split until its original issued review is saved.
-- The immutable accepting operation receipt remains the transport authority.
CREATE TABLE study_deferred_prefix_splits (
    intent_id text PRIMARY KEY,
    card_id text NOT NULL,
    source_revision bigint NOT NULL CHECK (source_revision > 0),
    queue_entry_id bigint NOT NULL CHECK (queue_entry_id > 0),
    state text NOT NULL CHECK (state IN ('waiting_review', 'ready', 'held', 'applied')),
    reason text,
    review_attempt_id text,
    review_id bigint,
    child_operation_id text,
    result_json jsonb,
    next_check_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (card_id, source_revision, queue_entry_id),
    CHECK ((state = 'applied') = (result_json IS NOT NULL))
);
CREATE INDEX study_deferred_prefix_splits_waiting ON study_deferred_prefix_splits(next_check_at, created_at, intent_id)
    WHERE state IN ('waiting_review', 'ready');
CREATE INDEX study_deferred_prefix_review_lookup ON review_attempt_receipts(queue_entry_id, card_id, attempt_id);
INSERT INTO tempo_schema_migrations(version) VALUES(66);
