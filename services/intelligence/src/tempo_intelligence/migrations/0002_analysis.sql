CREATE TABLE intelligence.analysis_inputs (
 input_id text NOT NULL, revision bigint NOT NULL, payload jsonb NOT NULL, digest text NOT NULL,
 state text NOT NULL DEFAULT 'staging', PRIMARY KEY(input_id,revision));
CREATE TABLE intelligence.analysis_pages (
 input_id text NOT NULL, revision bigint NOT NULL, page_id text NOT NULL, payload jsonb NOT NULL,
 digest text NOT NULL, PRIMARY KEY(input_id,revision,page_id),
 FOREIGN KEY(input_id,revision) REFERENCES intelligence.analysis_inputs(input_id,revision));
CREATE TABLE intelligence.analysis_runs (
 run_id text NOT NULL, revision bigint NOT NULL, payload jsonb NOT NULL, digest text NOT NULL,
 state text NOT NULL DEFAULT 'staging', budget integer NOT NULL,
 requested_generation bigint NOT NULL DEFAULT 1, published_generation bigint,
 last_complete_generation bigint, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,revision));
CREATE TABLE intelligence.analysis_heads (run_id text PRIMARY KEY, revision bigint NOT NULL,
 FOREIGN KEY(run_id,revision) REFERENCES intelligence.analysis_runs(run_id,revision));
CREATE TABLE intelligence.analysis_positions (
 run_id text NOT NULL, revision bigint NOT NULL, ordinal integer NOT NULL, position_id text NOT NULL,
 payload jsonb NOT NULL, state text NOT NULL, execution_generation bigint NOT NULL DEFAULT 0,
 stage text NOT NULL DEFAULT 'root', attempts integer NOT NULL DEFAULT 0,
 next_attempt_at timestamptz, lease_token text, leased_until timestamptz,
 accepted_root text, accepted_played text, error_code text,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,revision,position_id), UNIQUE(run_id,revision,ordinal),
 FOREIGN KEY(run_id,revision) REFERENCES intelligence.analysis_runs(run_id,revision));
CREATE TABLE intelligence.analysis_executions (
 job_id text NOT NULL, execution_generation bigint NOT NULL, run_id text NOT NULL,
 revision bigint NOT NULL, position_id text NOT NULL, stage text NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(job_id,execution_generation),
 FOREIGN KEY(run_id,revision,position_id) REFERENCES intelligence.analysis_positions(run_id,revision,position_id));
CREATE TABLE intelligence.analysis_results (
 result_id text PRIMARY KEY, job_id text NOT NULL, execution_generation bigint NOT NULL,
 payload jsonb NOT NULL, UNIQUE(job_id,execution_generation));
CREATE TABLE intelligence.analysis_reports (
 run_id text NOT NULL, revision bigint NOT NULL, generation bigint NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(run_id,revision,generation));
CREATE TABLE intelligence.analysis_report_items (
 run_id text NOT NULL, revision bigint NOT NULL, generation bigint NOT NULL,
 finding_id text NOT NULL, ordinal integer NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(run_id,revision,generation,finding_id));
CREATE TABLE intelligence.analysis_dismissals (
 finding_id text PRIMARY KEY, revision bigint NOT NULL, state text NOT NULL,
 supporting_games jsonb NOT NULL);
CREATE TABLE intelligence.analysis_commands (command_id text PRIMARY KEY, digest text NOT NULL, payload jsonb NOT NULL);
CREATE INDEX analysis_eligible ON intelligence.analysis_positions(state,next_attempt_at,created_at,ordinal);
CREATE INDEX analysis_report_order ON intelligence.analysis_report_items(run_id,revision,generation,ordinal);
CREATE TABLE intelligence.analysis_execution_events (
 event_id text PRIMARY KEY, job_id text NOT NULL, execution_generation bigint NOT NULL,
 payload jsonb NOT NULL,
 FOREIGN KEY(job_id,execution_generation) REFERENCES intelligence.analysis_executions(job_id,execution_generation));
