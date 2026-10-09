import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { APIRequestContext } from "@playwright/test";
import { redact } from "../../scripts/tempo-deployment.mjs";
import { assertDisposableTarget } from "./disposable-target";

type AttachDiagnostic = (name: string, options: { body: Buffer; contentType: string }) => Promise<void>;
type DiagnosticOptions = {
  composeProject?: string;
  secretValues?: string[];
  readDockerOutput?: (dockerArguments: string[]) => string;
  request?: APIRequestContext;
  api?: string;
};

// One current row and a capped kind/priority summary; never export task payloads
// or lease tokens. The owning disposable PostgreSQL runner supplies the project.
const queueTaskDiagnosticSql = `BEGIN READ ONLY;
SET LOCAL statement_timeout='100ms';
SELECT json_build_object(
  'projections', (
    SELECT COALESCE(json_agg(projection),'[]'::json) FROM (
      SELECT queue_date,state,generation,refresh_pending,last_error,blocked_count,updated_at
      FROM queue_projections ORDER BY queue_date DESC LIMIT 7
    ) projection
  ),
  'current_daily_queue', (
    SELECT row_to_json(current_task) FROM (
      SELECT id,kind,generation,priority,state,phase,
        payload_json::jsonb->>'queue_date' AS queue_date,
        payload_json::jsonb->>'_queue_phase' AS queue_phase,
        payload_json::jsonb->>'after_card_id' AS after_card_id,
        payload_json::jsonb->>'after_entry_id' AS after_entry_id,
        next_attempt_at,lease_token IS NOT NULL AS lease_present,
        lease_expires_at,attempt_count,max_attempts,transaction_timeout_count,
        transaction_timeout_checkpoint IS NOT NULL AS deadline_checkpoint_present,
        CASE WHEN jsonb_typeof(transaction_timeout_checkpoint::jsonb->0)='number'
          THEN transaction_timeout_checkpoint::jsonb->0 END AS deadline_generation,
        CASE WHEN jsonb_typeof(transaction_timeout_checkpoint::jsonb->0)='number'
          THEN transaction_timeout_checkpoint::jsonb->>1
          ELSE transaction_timeout_checkpoint::jsonb->>0 END AS deadline_phase,
        pending_since,generation_started_at,updated_at,
        GREATEST(0,EXTRACT(EPOCH FROM now()-next_attempt_at::timestamptz)) AS eligible_age_seconds
      FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'
    ) current_task
  ),
  'competing_work', (
    SELECT COALESCE(json_agg(summary),'[]'::json) FROM (
      SELECT kind,priority,state,COUNT(*) AS count,
        MIN(next_attempt_at) AS earliest_eligibility
      FROM background_tasks WHERE state IN ('queued','retrying','leased')
      GROUP BY kind,priority,state ORDER BY priority,kind,state LIMIT 20
    ) summary
  )
);
COMMIT;`;

// Capture before subsequent tests replace workers. Diagnostics must never turn
// the original readiness failure into a pass or inspect a study instance.
export async function verifyQueueReadinessWithDiagnostics(
  verifyReady: () => Promise<void>, attach: AttachDiagnostic, options: DiagnosticOptions = {},
) {
  try { await verifyReady(); }
  catch (readinessError) {
    try {
      const composeProject = options.composeProject ?? process.env.TEMPO_TEST_COMPOSE_PROJECT;
      if (!composeProject || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(composeProject))
        throw new Error("Queue diagnostics require the owning disposable runner.");
      const secretValues = options.secretValues ?? ["admin_password", "reader_password", "writer_password"].map(name =>
        readFileSync(join(process.env.TEMPO_PG_TEST_SECRETS ?? "", name), "utf8").trim());
      if (options.request && options.api) {
        const request = options.request;
        const api = options.api;
        const health = await request.get(`${api}/health`, { timeout: 2_000 });
        assertDisposableTarget(health.ok() ? await health.json() : null);
        const summaries = await Promise.allSettled(
          ["system/background-diagnostics"].map(async (route) => {
            const response = await request.get(`${api}/${route}`, {
              timeout: 2_000, headers: { "X-Tempo-Work-Class": "background" },
            });
            return { route, status: response.status(), body: await response.json() };
          }),
        );
        const diagnostic = JSON.stringify(summaries.map((summary) => summary.status === "fulfilled"
          ? summary.value : { unavailable: String(summary.reason) }), null, 2);
        await attach("queue-readiness-diagnostics", {
          body: Buffer.from(redact(diagnostic, secretValues)), contentType: "application/json",
        });
      }
      const readDockerOutput = options.readDockerOutput ?? (dockerArguments => execFileSync("docker", dockerArguments, {
        encoding: "utf8", timeout: 5_000, maxBuffer: 128 * 1024,
      }));
      const compose = ["compose", "-p", composeProject, "-f", "docker-compose.postgres.test.yml"];
      const reads: [string, string[]][] = [
        ["queue-state-at-readiness-failure", [...compose, "exec", "-T", "postgres", "psql", "-X", "-U", "postgres", "-d", "tempo", "-At", "-v", "ON_ERROR_STOP=1", "-c", queueTaskDiagnosticSql]],
        ["worker-history-at-readiness-failure", [...compose, "logs", "--no-color", "--timestamps", "--tail=200", "background-worker", "background-scheduler"]],
        ["background-services-at-readiness-failure", [...compose, "ps", "--all", "--format", "json", "background-worker", "background-scheduler", "redis"]],
        ["scheduler-process-at-readiness-failure", [...compose, "exec", "-T", "background-scheduler", "python", "-c",
          "from pathlib import Path; print(Path('/proc/1/status').read_text()); print('wait_channel:', Path('/proc/1/wchan').read_text())"]],
      ];
      for (const [name, dockerArguments] of reads) {
        let diagnostic: string;
        try { diagnostic = readDockerOutput(dockerArguments); }
        catch (error) { diagnostic = `Read-only diagnostic unavailable: ${String(error)}`; }
        await attach(name, { body: Buffer.from(redact(diagnostic, secretValues)), contentType: "text/plain" });
      }
    } catch (diagnosticError) {
      try {
        await attach("queue-readiness-task-evidence-unavailable", {
          body: Buffer.from(String(diagnosticError)), contentType: "text/plain",
        });
      } catch { /* The original readiness error remains authoritative. */ }
    }
    throw readinessError;
  }
}
