import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { setTimeout as waitForTimer } from "node:timers/promises";
import { assertDisposableTarget } from "./disposable-target";

const executeFile = promisify(execFile);
export const permanentDeletionYieldMessage =
  "Permanent deletion yielded to a structural or queue writer; retry the original operation.";

export type PostgresFixtureTarget = {
  health: unknown;
  apiUrl: string;
  project: string | undefined;
};

export type FixtureResetRuntime = {
  runDocker: (argumentsList: string[], timeoutMs: number) => Promise<string>;
  now: () => number;
  wait: (durationMs: number) => Promise<void>;
  retryBudgetMs: number;
};

const defaultRuntime: FixtureResetRuntime = {
  async runDocker(argumentsList, timeoutMs) {
    const result = await executeFile("docker", argumentsList, {
      encoding: "utf8", timeout: timeoutMs, maxBuffer: 256 * 1024,
    });
    return result.stdout;
  },
  now: () => performance.now(),
  wait: async (durationMs) => { await waitForTimer(durationMs); },
  retryBudgetMs: 10_000,
};

const reservationDiagnosticsSql = `
  WITH reservation AS (
    SELECT hashtextextended('tempo:prefix-transition:reservations',0) AS key
  )
  SELECT COALESCE(json_agg(json_build_object(
    'pid',locks.pid,'mode',locks.mode,'granted',locks.granted,
    'application_name',activity.application_name,
    'transaction_age_ms',EXTRACT(EPOCH FROM (clock_timestamp()-activity.xact_start))*1000
  )), '[]'::json)
  FROM pg_locks locks CROSS JOIN reservation
  LEFT JOIN pg_stat_activity activity ON activity.pid=locks.pid
  WHERE locks.locktype='advisory' AND locks.objsubid=1
    AND locks.classid=((reservation.key >> 32) & 4294967295)::oid
    AND locks.objid=(reservation.key & 4294967295)::oid`;

export function postgresFixtureComposeArguments(project: string | undefined): string[] {
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project))
    throw new Error("Product fixture reset requires its owning disposable PostgreSQL runner");
  return ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml"];
}

export function postgresFixtureSqlArguments(containerId: string, sql: string, timeoutMs: number): string[] {
  if (!/^[a-f0-9]{12,64}$/.test(containerId))
    throw new Error("Product fixture SQL requires its verified PostgreSQL container ID");
  return ["exec", "--interactive=false", "-e",
    `PGOPTIONS=-c statement_timeout=${timeoutMs} -c lock_timeout=${timeoutMs}`,
    containerId, "psql", "-X", "-w", "-h", "/var/run/postgresql", "-U", "postgres", "-d", "tempo",
    "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose", "-At", "-c", sql];
}

export function isPermanentDeletionYield(error: unknown): boolean {
  if (!error || typeof error !== "object" || Reflect.get(error, "killed") || Reflect.get(error, "signal"))
    return false;
  const exitCode = Reflect.get(error, "code");
  if (typeof exitCode !== "number" || exitCode === 0) return false;
  const stderr = Reflect.get(error, "stderr");
  if (typeof stderr !== "string") return false;
  const primaryErrors = stderr.split(/\r?\n/).filter(line => /^ERROR:/.test(line));
  return primaryErrors.length === 1 &&
    primaryErrors[0] === `ERROR:  55P03: ${permanentDeletionYieldMessage}`;
}

export async function verifyPostgresFixtureTarget(
  target: PostgresFixtureTarget,
  runtimeOverrides: Partial<FixtureResetRuntime> = {},
): Promise<string> {
  assertDisposableTarget(target.health);
  if (Reflect.get(target.health as object, "storage") !== "postgresql")
    throw new Error("Product fixture reset requires disposable PostgreSQL health");
  postgresFixtureComposeArguments(target.project);
  const runtime = { ...defaultRuntime, ...runtimeOverrides };
  if (!Number.isInteger(runtime.retryBudgetMs) || runtime.retryBudgetMs <= 0 || runtime.retryBudgetMs > 10_000)
    throw new Error("Product fixture retry budget must be between 1 and 10000 milliseconds");
  const apiAddress = new URL(target.apiUrl);
  if (apiAddress.protocol !== "http:" || apiAddress.hostname !== "127.0.0.1" ||
      apiAddress.pathname !== "/api" || apiAddress.username || apiAddress.password ||
      apiAddress.search || apiAddress.hash)
    throw new Error("Product fixture API must be its owning runner's loopback /api endpoint");
  // Inspect only non-secret metadata. The disposable runner uses Compose's
  // single-replica service names; labels and the published port must also agree.
  const metadata = await runtime.runDocker(["inspect", "--format",
    '{"id":{{json .Id}},"labels":{{json .Config.Labels}},"ports":{{json .NetworkSettings.Ports}}}',
    `${target.project}-web-1`, `${target.project}-postgres-1`], 5_000);
  const containers = metadata.trim().split(/\r?\n/).map(line => JSON.parse(line));
  if (containers.length !== 2 || containers.some((container, index) =>
    !/^[a-f0-9]{12,64}$/.test(container?.id ?? "") ||
    container?.labels?.["com.docker.compose.project"] !== target.project ||
    container?.labels?.["com.docker.compose.service"] !== ["web", "postgres"][index]))
    throw new Error("Product fixture containers do not belong to the owning disposable project");
  const bindings = containers[0].ports?.["80/tcp"];
  if (!Array.isArray(bindings) || bindings.length !== 1 ||
      bindings[0].HostIp !== "127.0.0.1" || bindings[0].HostPort !== apiAddress.port)
    throw new Error(`Product fixture API does not belong to disposable project ${target.project}`);
  return containers[1].id;
}

export async function resetPostgresDeletedCards(
  target: PostgresFixtureTarget,
  runtimeOverrides: Partial<FixtureResetRuntime> = {},
): Promise<void> {
  const containerId = await verifyPostgresFixtureTarget(target, runtimeOverrides);
  const runtime = { ...defaultRuntime, ...runtimeOverrides };
  const startedAt = runtime.now();
  const deadline = startedAt + runtime.retryBudgetMs;
  let attempts = 0;
  let lastYield: unknown;
  let minimumAttemptBudgetMs = 1;
  while (runtime.now() < deadline) {
    const remainingMs = Math.max(1, Math.floor(deadline - runtime.now()));
    if (lastYield && remainingMs < minimumAttemptBudgetMs) {
      // Leave headroom for Docker's observed command cost rather than starting
      // an already-too-short final attempt and misreporting it as an outage.
      await runtime.wait(deadline - runtime.now());
      break;
    }
    const attemptStartedAt = runtime.now();
    attempts += 1;
    try {
      // Each command owns one short transaction and exits before any wait.
      // The production trigger acquires the exclusive reservation on success.
      await runtime.runDocker(postgresFixtureSqlArguments(containerId, "DELETE FROM deleted_cards", remainingMs), remainingMs);
      return;
    } catch (error) {
      if (!isPermanentDeletionYield(error)) throw error;
      lastYield = error;
      minimumAttemptBudgetMs = Math.max(minimumAttemptBudgetMs, Math.ceil((runtime.now() - attemptStartedAt) * 2));
    }
    const remainingWaitMs = deadline - runtime.now();
    if (remainingWaitMs > 0) await runtime.wait(Math.min(50, remainingWaitMs));
  }
  let diagnostics: string;
  try {
    diagnostics = await runtime.runDocker(postgresFixtureSqlArguments(containerId, reservationDiagnosticsSql, 1_000), 1_000);
  } catch (error) {
    diagnostics = `unavailable: ${error instanceof Error ? error.message : String(error)}`;
  }
  throw new Error(
    `Product fixture reset exhausted permanent-deletion coordination for ${target.project} after ${attempts} attempts in ${Math.round(runtime.now() - startedAt)}ms (budget ${runtime.retryBudgetMs}ms). ` +
    `${permanentDeletionYieldMessage}\nReservation holders: ${diagnostics.trim()}`,
    { cause: lastYield },
  );
}
