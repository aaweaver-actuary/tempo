-- Study owns logical game effects independently of producer lifecycle/queue rows.
-- No cascading FK: deleted/rebound content cannot erase an owed receipt.
CREATE TABLE study_game_feedback (
    effect_key text PRIMARY KEY,
    identity_json text NOT NULL,
    source_revision text NOT NULL,
    payload_json text,
    payload_hash text,
    card_id text,
    card_revision bigint,
    decision_id text NOT NULL,
    occurred_at timestamptz,
    detected_at timestamptz,
    received_at timestamptz NOT NULL,
    available_by timestamptz,
    status text NOT NULL CHECK(status IN ('pending','held','applied','settled')),
    reason text,
    applied_at timestamptz,
    review_id bigint,
    settled_at timestamptz,
    settlement_review_id bigint,
    hold_revision text,
    hold_json text,
    conflict_json text,
    updated_at timestamptz NOT NULL,
    CHECK((payload_json IS NULL) = (payload_hash IS NULL)),
    CHECK(available_by IS NULL OR detected_at IS NOT NULL)
);
CREATE INDEX study_game_feedback_owed ON study_game_feedback(card_id,effect_key)
    WHERE status IN ('pending','applied');
INSERT INTO tempo_schema_migrations(version) VALUES(51);
