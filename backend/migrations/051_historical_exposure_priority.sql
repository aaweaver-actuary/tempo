-- Explicit user-confirmed practice need, never a recall/remediation obligation.
-- Training intent has no age expiry; operational command leases are separate.
CREATE TABLE historical_exposure_intents (
    target_id text NOT NULL,
    workspace_id text NOT NULL CHECK(workspace_id='legacy'),
    card_id text NOT NULL,
    encounter_key text NOT NULL,
    state text NOT NULL CHECK(state IN ('active','completed','cancelled','revoked','superseded')),
    record_json jsonb NOT NULL,
    source_json jsonb NOT NULL,
    PRIMARY KEY(target_id,workspace_id,encounter_key)
);
CREATE INDEX historical_exposure_active_card
    ON historical_exposure_intents(target_id,workspace_id,card_id,encounter_key)
    WHERE state='active';
INSERT INTO tempo_schema_migrations(version) VALUES(51);
