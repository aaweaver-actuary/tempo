-- The backend migration owner includes this exact installed module resource.
-- tempo-module-schema: tempo_learning_remediation/migrations/001_remediation.sql
-- sha256: 96907361dc003e78703dc57182bd020f96e82532bcea6724083326171242da0d
-- Direct execution without the owner inclusion must fail, never mark a partial schema ready.
DO $$
DECLARE missing text[];
BEGIN
    SELECT array_agg(name) INTO missing
    FROM unnest(ARRAY[
        'remediation_deliveries','remediation_evidence','remediation_source_heads',
        'remediation_game_heads','remediation_workspaces','remediation_cards',
        'remediation_incident_versions','remediation_contributions','remediation_obligation_heads',
        'remediation_attempts','remediation_review_receipts','remediation_settlements'
    ]) AS required(name)
    WHERE to_regclass('public.' || name) IS NULL;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Module schema inclusion missing: %', missing;
    END IF;
END $$;
-- Stable logical backend target; ordinary Study and harmless recapture do not
-- change it. This is backend metadata, not another remediation/frontier store.
CREATE TABLE legacy_learning_identity (
    id smallint PRIMARY KEY CHECK(id=1),
    target_id text NOT NULL UNIQUE DEFAULT ('legacy-target:' || gen_random_uuid()::text),
    workspace_id text NOT NULL DEFAULT 'legacy' CHECK(workspace_id='legacy')
);
INSERT INTO legacy_learning_identity(id) VALUES(1);
-- Backend ownership lookups are bounded by canonical game and repertoire.
CREATE INDEX remediation_legacy_scope_owner ON remediation_contributions (
    workspace_id,game_id,((payload_json::jsonb->'authority')->>'repertoire_id')
) WHERE payload_json::jsonb->'authority'->>'kind'='legacy_graph';
INSERT INTO tempo_schema_migrations(version) VALUES(50);
