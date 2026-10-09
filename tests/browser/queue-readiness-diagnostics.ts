import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { redact } from "../../scripts/tempo-deployment.mjs";

type AttachDiagnostic = (name: string, options: { body: Buffer; contentType: string }) => Promise<void>;
type DiagnosticOptions = { composeProject?: string; secretValues?: string[]; readDockerOutput?: (dockerArguments: string[]) => string };

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
      const readDockerOutput = options.readDockerOutput ?? (dockerArguments => execFileSync("docker", dockerArguments, {
        encoding: "utf8", timeout: 5_000, maxBuffer: 16 * 1024 * 1024,
      }));
      const compose = ["compose", "-p", composeProject, "-f", "docker-compose.postgres.test.yml"];
      const reads: [string, string[]][] = [
        ["queue-state-at-readiness-failure", [...compose, "exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo", "-At", "-c",
          "SELECT json_build_object('projection',(SELECT json_agg(q) FROM queue_projections q),'tasks',(SELECT json_agg(t) FROM (SELECT id,kind,generation,phase,state,attempt_count,next_attempt_at,lease_expires_at,last_error,payload_json,updated_at FROM background_tasks WHERE kind='daily_queue') t))"]],
        ["worker-history-at-readiness-failure", [...compose, "logs", "--no-color", "--timestamps", "background-worker", "background-scheduler"]],
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
    } catch {
      // The readiness assertion remains authoritative even if capture fails.
    }
    throw readinessError;
  }
}
