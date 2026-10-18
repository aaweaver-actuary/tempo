CREATE TABLE queue_retry_policies (
    review_id BIGINT NOT NULL PRIMARY KEY REFERENCES reviews(id) ON DELETE CASCADE,
    source_queue_entry_id BIGINT NOT NULL,
    retry_queue_entry_id BIGINT NOT NULL UNIQUE,
    card_id TEXT NOT NULL,
    revision BIGINT NOT NULL,
    policy_version TEXT NOT NULL,
    delay_seconds BIGINT NOT NULL CHECK(delay_seconds BETWEEN 300 AND 600),
    ready_at TEXT NOT NULL,
    order_token TEXT NOT NULL CHECK(length(order_token)=32),
    released_at TEXT,
    release_reason TEXT CHECK(release_reason IN ('deadline','no_ready_work')),
    issued_at TEXT,
    issued_position BIGINT,
    issued_attempt_state TEXT,
    CHECK((issued_at IS NULL)=(issued_position IS NULL)),
    CHECK((issued_at IS NULL)=(issued_attempt_state IS NULL)),
    CHECK((released_at IS NULL AND release_reason IS NULL AND issued_at IS NULL)
       OR (released_at IS NOT NULL AND release_reason IS NOT NULL))
);
CREATE INDEX idx_queue_retry_card_review ON queue_retry_policies(card_id,review_id);
CREATE INDEX idx_queue_retry_pending ON queue_retry_policies(ready_at,review_id)
    WHERE released_at IS NULL;
