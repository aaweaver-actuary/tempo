// Operator decisions are separate from evidence collection and deployment I/O.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

export class TempoProblem extends Error {
  constructor(code, message, { action, verification, cause, exitCode = 1 } = {}) {
    super(message, { cause });
    this.code = code;
    this.action = action;
    this.verification = verification;
    this.exitCode = exitCode;
  }
}

export function verificationProblem(verification) {
  const { status, job, conclusion, message, run } = verification;
  const unfinishedConclusions = { cancelled: "was cancelled", timed_out: "timed out", skipped: "was skipped", action_required: "requires action", neutral: "did not report success" };
  const failedDescription = unfinishedConclusions[conclusion]
    ? `The required release check${job ? ` (${job})` : ""} ${unfinishedConclusions[conclusion]}. A successful release check is still required before this version can be installed.`
    : `The new version failed required release checks${job ? ` (${job})` : ""}. The failing job needs repair in the software or release workflow; retrying local startup cannot change that result.`;
  const descriptions = {
    pending: "The update is waiting for automated release checks.",
    failed: failedDescription,
    missing: `Required release-check evidence is missing${job ? ` (${job})` : " for the current main revision"}. The release workflow must complete before this version can be installed.`,
    unavailable: `GitHub release checks could not be read. This is an access or connection problem, not a failed test.${message ? ` Cause: ${message}` : ""}`,
  };
  return new TempoProblem(`verification_${status}`, descriptions[status] ?? message, {
    verification,
    action: status === "pending" ? "Run: tempo start\nTempo will wait up to 30 minutes, then update automatically; study may pause during maintenance."
      : status === "unavailable" ? /rate limit/i.test(message ?? "")
        ? "Next: wait for the reported GitHub rate limit to clear, then run tempo start."
        : "Next: restore GitHub connectivity/access, then run tempo start."
      : `Next: ${status === "missing" ? "complete or restore the required release workflow" : conclusion === "cancelled" ? "rerun the cancelled release workflow" : "repair or complete the required release workflow"}: ${run?.html_url ?? "https://github.com/aaweaver-actuary/tempo/actions/workflows/pages.yml"}`,
  });
}

export function recoveryAssessment({ maintenance, migrationRecovery, sourceProblem, verification, ledgerProblem, serviceProblem } = {}) {
  if (maintenance?.status === "active") return new TempoProblem("maintenance_active",
    "Another Tempo command is performing maintenance on this installation.",
    { action: "Next: let that command finish. Use tempo status to check progress." });
  if (maintenance?.status === "unknown") return new TempoProblem("maintenance_unknown",
    "The maintenance lock cannot be verified; starting another update is unsafe.",
    { action: `Next: preserve ${maintenance.path} and identify its owning process before retrying. Do not remove an active lock.` });
  if (migrationRecovery && migrationRecovery.status !== "clear") {
    const summary = migrationRecovery.code === "migration_pending"
      ? `The previous database update is unfinished. Original backup: ${migrationRecovery.backup}.`
      : migrationRecovery.code === "migration_guard_invalid"
        ? "Migration guard is invalid or belongs to another database. Ordinary startup and retry are unsafe."
        : migrationRecovery.code === "migration_guard_missing"
          ? "The previous database update failed without a durable original guard. Ordinary startup and retry are unsafe."
        : migrationRecovery.message;
    return new TempoProblem("migration_recovery", summary, { action:
      ["migration_guard_invalid", "migration_guard_missing"].includes(migrationRecovery.code)
        ? "Next: preserve the original backup, guard and operation records; recover their original history using docs/POSTGRES-MAINTENANCE.md. Do not delete the guard or run an ordinary retry."
        : migrationRecovery.status === "retry-authorized"
        ? "Next: the explicit recovery attempt must verify original history before study can resume."
        : "Next: preserve the original backup and guard. Read tempo doctor --verbose for the failed phase and evidence; repair that cause before tempo migrate --retry." });
  }
  if (ledgerProblem) return new TempoProblem("schema_unsafe", ledgerProblem,
    { action: "Next: preserve the database and use compatible source or repair the migration ledger; do not reset or restore older study data." });
  if (sourceProblem) return sourceProblem;
  if (verification?.status !== "verified") return verificationProblem(verification ?? { status: "unavailable" });
  if (serviceProblem) return serviceProblem;
  return new TempoProblem("eligible", "The update is eligible. Tempo handles image preparation, a verified backup, migrations, and readiness.",
    { action: "Run: tempo start" });
}

export function inspectMaintenance(stateDirectory) {
  const path = join(stateDirectory, "maintenance.lock");
  let owner;
  try { owner = JSON.parse(readFileSync(path, "utf8")); }
  catch (error) { return { status: error.code === "ENOENT" ? "idle" : "unknown", path }; }
  if (!Number.isInteger(owner.pid) || owner.pid <= 0 || typeof owner.token !== "string" || !owner.token)
    return { status: "unknown", path };
  try { process.kill(owner.pid, 0); return { status: "active", path, pid: owner.pid }; }
  catch (error) { return { status: error.code === "ESRCH" ? "stale" : "unknown", path, pid: owner.pid }; }
}

export function classifyRedisReply({ code, stdout = "", stderr = "", interrupted = code === null }) {
  const reason = stdout.trim() || stderr.trim() || "Redis readiness probe was interrupted or timed out";
  if (code === 0 && stdout.trim() === "PONG") return { status: "ready", reason };
  const terminal = /^(?:\(error\)\s*)?(?:ERR|NOAUTH|WRONGPASS|NOPERM|MISCONF|WRONGTYPE|BUSY|READONLY)\b/i.test(reason);
  const transient = !terminal && (interrupted || /^(?:\(error\)\s*)?LOADING\b/i.test(reason)
    || /^Error: Server closed the connection$/i.test(reason)
    || /(?:connection (?:refused|reset|closed)|could not connect to redis|timed? out|timeout|interrupted)/i.test(reason));
  return { status: transient ? "transient" : "terminal", reason };
}

export function phaseGuidance(phase) {
  const phases = {
    preparing_images: { services: [], action: "Next: correct the image build/download error above, then run tempo start. Study services were not stopped for image preparation." },
    checking_redis: { services: ["redis"], action: "Next: run tempo logs redis. Repair the reported Redis connection, authentication or configuration error before tempo start; keep Redis data." },
    checking_database: { services: ["postgres", "redis"], action: "Next: run tempo logs postgres and correct the database readiness error above before tempo start." },
    checking_schema: { services: ["postgres"], action: "Next: repair the reported schema or permission problem before tempo start; preserve study data." },
    verifying_backup: { services: ["postgres", "migration", "postgres-backup"], action: "Next: repair the backup/restore error using the saved evidence. Keep the original backup and study data; do not restore older data over new writes." },
    applying_migrations: { services: ["postgres", "migration"], action: "Next: repair the migration error using the saved evidence and original backup, then explicitly run tempo migrate --retry." },
    starting_services: { services: ["api", "foreground-worker", "background-worker", "background-scheduler", "web", "defense-engine", "maia-worker"], action: "Next: correct the affected service error in the saved logs, then run tempo start." },
    checking_readiness: { services: ["api", "foreground-worker", "background-worker", "background-scheduler", "web", "redis"], action: "Next: correct the readiness error in the saved logs, then run tempo start." },
  };
  return phases[phase] ?? { services: ["api", "foreground-worker", "background-worker", "postgres", "redis"],
    action: "Next: correct the reported error using the saved evidence before retrying. Study data and recovery records must be preserved." };
}

// Hash only operator state, never credentials or a database. Include absence so
// a concurrent stop/first deployment cannot be adopted by an older waiting start.
export function installationFingerprint(stateDirectory, configPath) {
  return Object.fromEntries([configPath, ...["operation.json", "deployment.json", "migration-guard.json"]
    .map(name => join(stateDirectory, name))].map(path => {
    try { return [path, createHash("sha256").update(readFileSync(path)).digest("hex")]; }
    catch (error) { if (error.code === "ENOENT") return [path, null]; throw error; }
  }));
}

export function assertInstallationUnchanged(expected, stateDirectory, configPath) {
  if (JSON.stringify(expected) !== JSON.stringify(installationFingerprint(stateDirectory, configPath))) {
    throw new TempoProblem("installation_changed", "Another Tempo command changed the installation while this start was waiting. This waiting start will not restart it.",
      { action: "Run: tempo start only when you intend to start or update Tempo again." });
  }
}

export async function waitForVerification({ assess, checkUnchanged = () => {}, log = console.log,
  signal, noWait = false, deadline, now = () => Number(process.hrtime.bigint() / 1_000_000n),
  wait = milliseconds => delay(milliseconds, undefined, { signal }), intervalMilliseconds = 60_000 }) {
  const started = now();
  const expires = deadline ?? started + 30 * 60_000;
  let announced = false;
  for (;;) {
    if (signal?.aborted) throw new TempoProblem("cancelled", "Waiting cancelled. No deployment was started.", { exitCode: 130 });
    await checkUnchanged();
    let assessment;
    try { assessment = await assess(signal); }
    catch (error) {
      if (signal?.aborted) throw new TempoProblem("cancelled", "Waiting cancelled. No deployment was started.", { exitCode: 130 });
      throw error;
    }
    await checkUnchanged();
    if (signal?.aborted) throw new TempoProblem("cancelled", "Waiting cancelled. No deployment was started.", { exitCode: 130 });
    if (assessment.verification.status !== "pending" || noWait) return assessment;
    if (now() >= expires) return { ...assessment, timedOut: true };
    if (!announced) {
      log("Tempo will wait up to 30 minutes for release checks, then update automatically. Study may pause during maintenance. Ctrl-C cancels waiting.");
      announced = true;
    }
    log(`Waiting for ${assessment.revision.slice(0, 12)} release checks (${Math.floor((now() - started) / 60_000)} minutes elapsed).${assessment.verification.run?.html_url ? ` ${assessment.verification.run.html_url}` : ""}`);
    try { await wait(Math.min(intervalMilliseconds, expires - now())); }
    catch (error) {
      if (signal?.aborted) throw new TempoProblem("cancelled", "Waiting cancelled. No deployment was started.", { exitCode: 130 });
      throw error;
    }
  }
}
