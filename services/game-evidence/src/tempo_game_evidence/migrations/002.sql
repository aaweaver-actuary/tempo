ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK(kind IN ('compare','rescope','reconcile'));
ALTER TABLE games ADD COLUMN heads_initialized boolean NOT NULL DEFAULT false;
ALTER TABLE games ADD COLUMN evidence_revision bigint NOT NULL DEFAULT 0;
ALTER TABLE games ADD COLUMN reconciled_revision bigint NOT NULL DEFAULT 0;
ALTER TABLE games ADD COLUMN reconciliation_status text NOT NULL DEFAULT 'pending';
ALTER TABLE games ADD COLUMN reconciliation_reason text;
ALTER TABLE outbox ADD COLUMN comparison_revision bigint;
UPDATE outbox SET comparison_revision=evidence_revision;
ALTER TABLE outbox ALTER COLUMN comparison_revision SET NOT NULL;
ALTER TABLE outbox ADD FOREIGN KEY(game_id,comparison_revision) REFERENCES comparisons(game_id,revision);
UPDATE games game SET evidence_revision=greatest(report_revision,coalesce((SELECT max(evidence_revision) FROM outbox WHERE game_id=game.game_id),0)),
 heads_initialized=NOT EXISTS(SELECT 1 FROM outbox WHERE game_id=game.game_id);
ALTER TABLE games ALTER COLUMN heads_initialized SET DEFAULT true;
CREATE TABLE accepted_heads(
 game_id text NOT NULL REFERENCES games, bucket integer NOT NULL, obligation_id text NOT NULL,
 obligation_revision bigint NOT NULL, active boolean NOT NULL, obligation jsonb NOT NULL, encounter jsonb NOT NULL,
 PRIMARY KEY(game_id,obligation_id)
);
CREATE INDEX outbox_game_unresolved ON outbox(game_id,evidence_revision,bucket) WHERE attempts>0;
INSERT INTO jobs(job_key,workspace_id,game_id,kind)
 SELECT 'reconcile:'||game_id,workspace_id,game_id,'reconcile' FROM games WHERE report_revision>0;
ALTER TABLE import_pages ADD COLUMN validation_status text NOT NULL DEFAULT 'unvalidated'
 CHECK(validation_status IN ('unvalidated','valid','invalid'));
ALTER TABLE import_pages ADD COLUMN validation_reason text;
ALTER TABLE provider_leases ALTER COLUMN token DROP NOT NULL;
ALTER TABLE provider_leases ALTER COLUMN leased_until DROP NOT NULL;
ALTER TABLE provider_leases ADD COLUMN cooldown_until timestamptz NOT NULL DEFAULT '-infinity';
INSERT INTO provider_leases(provider,cooldown_until)
 SELECT configuration->>'provider',max(next_attempt_at) FROM import_cursors
 WHERE reason='remote_rate_limited' AND status='retrying' GROUP BY configuration->>'provider'
 ON CONFLICT(provider) DO UPDATE SET cooldown_until=greatest(provider_leases.cooldown_until,excluded.cooldown_until);
INSERT INTO evidence_schema_migrations VALUES(2);
