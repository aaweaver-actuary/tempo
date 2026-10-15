CREATE TABLE IF NOT EXISTS evidence_schema_migrations(version integer PRIMARY KEY);
CREATE TABLE IF NOT EXISTS workspace_inputs(
 workspace_id text PRIMARY KEY, epoch bigint NOT NULL DEFAULT 0, snapshot_digest text,
 changed_positions jsonb NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS snapshots(
 digest text PRIMARY KEY, workspace_id text NOT NULL, snapshot_id text NOT NULL,
 revision bigint NOT NULL, document jsonb NOT NULL,
 UNIQUE(workspace_id,snapshot_id,revision)
);
CREATE TABLE IF NOT EXISTS games(
 game_id text PRIMARY KEY, workspace_id text NOT NULL, provider text NOT NULL,
 provider_game_id text NOT NULL, player text NOT NULL, revision bigint NOT NULL,
 input_digest text NOT NULL, source_digest text NOT NULL, raw_source bytea NOT NULL,
 document jsonb NOT NULL, index_receipt jsonb, positions jsonb,
 report_revision bigint NOT NULL DEFAULT 0,
 UNIQUE(workspace_id,provider,provider_game_id,player)
);
CREATE TABLE IF NOT EXISTS jobs(
 job_key text PRIMARY KEY, workspace_id text NOT NULL, game_id text REFERENCES games,
 kind text NOT NULL CHECK(kind IN ('compare','rescope')), requested bigint NOT NULL DEFAULT 1,
 published bigint NOT NULL DEFAULT 0, cursor text NOT NULL DEFAULT '',
 lease_token text, leased_until timestamptz, next_attempt_at timestamptz NOT NULL DEFAULT now(),
 attempts integer NOT NULL DEFAULT 0, error text
);
CREATE TABLE IF NOT EXISTS game_sources(
 game_id text NOT NULL REFERENCES games,revision bigint NOT NULL,input_digest text NOT NULL,
 source_digest text NOT NULL,raw_source bytea NOT NULL,document jsonb NOT NULL,
 PRIMARY KEY(game_id,revision)
);
CREATE TABLE IF NOT EXISTS comparisons(
 game_id text NOT NULL REFERENCES games, revision bigint NOT NULL, input_token text NOT NULL,
 report jsonb NOT NULL, PRIMARY KEY(game_id,revision), UNIQUE(game_id,input_token)
);
CREATE TABLE IF NOT EXISTS outbox(
 delivery_id text PRIMARY KEY, game_id text NOT NULL REFERENCES games, evidence_revision bigint NOT NULL,
 payload jsonb, status text NOT NULL, reason text, acknowledgement jsonb,
 attempts integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(),
 lease_token text, leased_until timestamptz, UNIQUE(game_id,evidence_revision)
);
CREATE TABLE IF NOT EXISTS import_cursors(
 import_id text PRIMARY KEY, workspace_id text NOT NULL, configuration jsonb NOT NULL,
 cursor jsonb NOT NULL, next_attempt_at timestamptz NOT NULL DEFAULT now(),
 status text NOT NULL DEFAULT 'pending', reason text, lease_token text, leased_until timestamptz
);
CREATE TABLE IF NOT EXISTS import_pages(
 import_id text NOT NULL REFERENCES import_cursors, page_id text NOT NULL,
 raw_response bytea NOT NULL, source_digest text NOT NULL,
 PRIMARY KEY(import_id,page_id)
);
CREATE TABLE IF NOT EXISTS provider_leases(provider text PRIMARY KEY,token text NOT NULL,leased_until timestamptz NOT NULL);
INSERT INTO evidence_schema_migrations(version) VALUES(1) ON CONFLICT DO NOTHING;
