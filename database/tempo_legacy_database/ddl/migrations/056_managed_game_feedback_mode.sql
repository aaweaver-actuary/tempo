-- Games-owned mirror of explicit managed release/operation mode intent.
-- This is not a Study ledger, provider configuration or automatic activation.
CREATE TABLE product_feedback_managed_mode (
    workspace_id text PRIMARY KEY,
    generation bigint NOT NULL CHECK(generation>0),
    mode text NOT NULL CHECK(mode IN ('collect-only','feedback')),
    phase text NOT NULL CHECK(phase IN ('inactive','transitioning','active')),
    payload jsonb NOT NULL
);

-- The qualified handoff's existing control fence runs in the short borrowed
-- writer. Match its original-effect lookup without scanning retained history.
CREATE INDEX product_feedback_effect_controls ON product_feedback_deliveries(effect_key)
    WHERE command_kind='control' AND state IN ('pending','acknowledged');

INSERT INTO tempo_schema_migrations(version) VALUES(56);
