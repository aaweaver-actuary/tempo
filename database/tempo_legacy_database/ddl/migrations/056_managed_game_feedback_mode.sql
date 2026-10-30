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

-- A derived read sidecar keeps head-time absence/FIFO checks off retained
-- unqualified collection history. Original payload/command/time are unchanged.
ALTER TABLE product_feedback_deliveries ADD COLUMN handoff_priority text;
-- Only actual stored control/witness facts contribute. Legacy status/time labels
-- without a witness remain NULL and await ordinary owner qualification.
UPDATE product_feedback_deliveries SET handoff_priority=''
    WHERE state='pending' AND command_kind='control' AND payload->'control'<>'null'::jsonb;
UPDATE product_feedback_deliveries SET handoff_priority=
    CASE WHEN payload->'qualification'->>'stream'='forward' THEN '0:' ELSE '1:' END
    ||to_char((payload->'qualification'->>'determined_at')::timestamptz AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00:'||COALESCE(payload->>'owner_effect_id','')
    WHERE state='pending' AND command_kind IN ('encounter','lapse')
      AND (payload->>'qualification_status'='ready' OR (payload->>'qualification_status'='held'
        AND COALESCE((payload->>'qualification_retryable')::boolean,true)))
      AND payload->'qualification'->'selection'->>'status'='own_actionable'
      AND payload->'qualification'->'selection'->'decision'<>'null'::jsonb
      AND payload->'qualification'->>'stream' IN ('forward','reconciliation','manual')
      AND payload->'qualification'->>'determined_at' ~
        '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$';
CREATE INDEX product_feedback_determined_pending ON product_feedback_deliveries
    ((COALESCE(payload->'encounter'->'key'->>'workspace_id',payload->'lapse'->'key'->>'workspace_id',
        payload->'control'->'key'->>'workspace_id')),handoff_priority COLLATE "C",next_attempt_at)
    WHERE state='pending' AND handoff_priority IS NOT NULL;

INSERT INTO tempo_schema_migrations(version) VALUES(56);
