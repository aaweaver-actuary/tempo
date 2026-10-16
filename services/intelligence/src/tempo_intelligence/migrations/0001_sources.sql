CREATE SCHEMA IF NOT EXISTS intelligence;
REVOKE ALL ON SCHEMA intelligence FROM PUBLIC;
CREATE TABLE intelligence.migration_history (id text PRIMARY KEY, checksum text NOT NULL);
CREATE TABLE intelligence.instance (id integer PRIMARY KEY CHECK(id=1), workspace_id text NOT NULL);
CREATE TABLE intelligence.snapshots (identity text PRIMARY KEY, payload jsonb NOT NULL);
CREATE TABLE intelligence.requests (
 request_id text NOT NULL, revision bigint NOT NULL CHECK(revision>0),
 payload jsonb NOT NULL, bindings jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(request_id,revision));
CREATE TABLE intelligence.request_heads (request_id text PRIMARY KEY, revision bigint NOT NULL,
 FOREIGN KEY(request_id,revision) REFERENCES intelligence.requests(request_id,revision));
CREATE TABLE intelligence.caches (
 cache_id text PRIMARY KEY, descriptor jsonb NOT NULL, input_snapshot jsonb NOT NULL, kind text NOT NULL,
 generation bigint NOT NULL DEFAULT 1, execution_generation bigint NOT NULL DEFAULT 0,
 state text NOT NULL DEFAULT 'queued', attempts integer NOT NULL DEFAULT 0, retryable boolean NOT NULL DEFAULT true,
 next_attempt_at timestamptz, error_code text, lease_token text, leased_until timestamptz,
 accepted_id text, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE intelligence.evidence (
 evidence_id text PRIMARY KEY, cache_id text NOT NULL REFERENCES intelligence.caches(cache_id),
 generation bigint NOT NULL, execution_generation bigint NOT NULL,
 descriptor jsonb NOT NULL, input_snapshot jsonb NOT NULL, payload jsonb NOT NULL, input_digest text NOT NULL, output_digest text NOT NULL,
 accepted_at timestamptz NOT NULL DEFAULT now(), UNIQUE(cache_id,generation,execution_generation));
ALTER TABLE intelligence.caches ADD FOREIGN KEY(accepted_id) REFERENCES intelligence.evidence(evidence_id);
CREATE TABLE intelligence.commands (command_id text PRIMARY KEY, payload jsonb NOT NULL, result jsonb NOT NULL);
CREATE TABLE intelligence.provider_control (
 id integer PRIMARY KEY CHECK(id=1), next_attempt_at timestamptz, lease_token text, leased_until timestamptz);
INSERT INTO intelligence.provider_control(id) VALUES(1);
CREATE TABLE intelligence.credential_registrations (
 registration_id text PRIMARY KEY, generation bigint UNIQUE NOT NULL, status text NOT NULL,
 expires_at timestamptz, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE intelligence.credential_head (id integer PRIMARY KEY CHECK(id=1), generation bigint NOT NULL DEFAULT 0,
 registration_id text REFERENCES intelligence.credential_registrations(registration_id));
INSERT INTO intelligence.credential_head(id) VALUES(1);
CREATE TABLE intelligence.maia_jobs (
 job_id text NOT NULL, request_revision bigint NOT NULL, execution_generation bigint NOT NULL,
 cache_id text NOT NULL REFERENCES intelligence.caches(cache_id), payload jsonb NOT NULL,
 status text NOT NULL DEFAULT 'pending', status_payload jsonb, result_payload jsonb, evidence_id text REFERENCES intelligence.evidence(evidence_id),
 PRIMARY KEY(job_id,request_revision,execution_generation));
CREATE TABLE intelligence.model_profiles (model_id text NOT NULL, model_version text NOT NULL,
 artifact_sha256 text NOT NULL, ratings jsonb NOT NULL, PRIMARY KEY(model_id,model_version));
CREATE INDEX cache_eligible ON intelligence.caches(state,next_attempt_at,updated_at);
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA intelligence FROM PUBLIC;
