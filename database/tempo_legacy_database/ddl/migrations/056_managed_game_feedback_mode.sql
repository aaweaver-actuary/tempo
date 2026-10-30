-- Games-owned mirror of explicit managed release/operation mode intent.
-- This is not a Study ledger, provider configuration or automatic activation.
CREATE TABLE product_feedback_managed_mode (
    workspace_id text PRIMARY KEY,
    generation bigint NOT NULL CHECK(generation>0),
    mode text NOT NULL CHECK(mode IN ('collect-only','feedback')),
    phase text NOT NULL CHECK(phase IN ('inactive','transitioning','active')),
    payload jsonb NOT NULL
);

INSERT INTO tempo_schema_migrations(version) VALUES(56);
