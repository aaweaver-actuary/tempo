import { execFileSync } from "node:child_process";
import type { APIRequestContext, TestInfo } from "@playwright/test";
import { assertDisposableTarget } from "./disposable-target";

// One current row and a capped kind/priority summary; never export task payloads
// or lease tokens. The owning disposable PostgreSQL runner supplies the project.
const queueTaskDiagnosticSql = `BEGIN READ ONLY;
SET LOCAL statement_timeout='100ms';
SELECT json_build_object(
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

export async function attachQueueReadinessDiagnostics(
  request: APIRequestContext, api: string, testInfo: TestInfo,
): Promise<void> {
  const summaries = await Promise.allSettled(
    ["system/tasks", "system/background-diagnostics"].map(async (route) => {
      const response = await request.get(`${api}/${route}`, {
        timeout: 2_000, headers: { "X-Tempo-Work-Class": "background" },
      });
      return { route, status: response.status(), body: await response.json() };
    }),
  );
  await testInfo.attach("queue-readiness-diagnostics", {
    body: JSON.stringify(summaries.map((summary) => summary.status === "fulfilled"
      ? summary.value : { unavailable: String(summary.reason) }), null, 2),
    contentType: "application/json",
  });
  try {
    const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
    if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project))
      throw new Error("Queue task evidence requires the owning disposable PostgreSQL runner");
    const health = await request.get(`${api}/health`, { timeout: 2_000 });
    assertDisposableTarget(health.ok() ? await health.json() : null);
    const evidence = execFileSync("docker", ["compose", "-p", project,
      "-f", "docker-compose.postgres.test.yml", "exec", "-T", "postgres",
      "psql", "-X", "-At", "-U", "postgres", "-d", "tempo", "-v",
      "ON_ERROR_STOP=1", "-c", queueTaskDiagnosticSql], {
      encoding: "utf8", timeout: 5_000, maxBuffer: 128 * 1024,
    });
    await testInfo.attach("queue-readiness-durable-task", { body: evidence, contentType: "text/plain" });
  } catch (error) {
    await testInfo.attach("queue-readiness-task-evidence-unavailable", {
      body: String(error), contentType: "text/plain",
    });
  }
}
