// @vitest-environment node
import { expect, it, vi } from "vitest";
import {
  isPermanentDeletionYield, permanentDeletionYieldMessage, resetPostgresDeletedCards,
  type FixtureResetRuntime, type PostgresFixtureTarget,
} from "../browser/product-fixture-reset";

const target: PostgresFixtureTarget = {
  health: { test_instance: true, storage: "postgresql" },
  apiUrl: "http://127.0.0.1:48124/api", project: "tempo-pg-regressions-123-abcdef01",
};
const containerId = "a".repeat(64);
function containerMetadata(port = "48124", postgresProject = target.project) {
  return ["web", "postgres"].map(service => JSON.stringify({
    id: service === "postgres" ? containerId : "b".repeat(64),
    labels: { "com.docker.compose.project": service === "postgres" ? postgresProject : target.project,
      "com.docker.compose.service": service },
    ports: service === "web" ? { "80/tcp": [{ HostIp: "127.0.0.1", HostPort: port }] } : {},
  })).join("\n");
}
const yieldError = () => Object.assign(new Error(permanentDeletionYieldMessage), {
  code: 1, killed: false, signal: null,
  stderr: `ERROR:  55P03: ${permanentDeletionYieldMessage}\nCONTEXT:  PL/pgSQL function reserve_permanent_deletion_write() line 4 at RAISE\nLOCATION:  exec_stmt_raise, pl_exec.c:3911\n`,
});

function controlledRuntime() {
  let monotonicTime = 0;
  const runDocker = vi.fn(async (argumentsList: string[], timeoutMs: number): Promise<string> => {
    expect(timeoutMs).toBeGreaterThan(0);
    monotonicTime += 5;
    return argumentsList.includes("inspect") ? containerMetadata() : "DELETE 1\n";
  });
  const wait = vi.fn(async (durationMs: number) => { monotonicTime += durationMs; });
  const runtime: FixtureResetRuntime = {
    runDocker, wait, now: () => monotonicTime, retryBudgetMs: 120,
  };
  return { runtime, runDocker, wait };
}

it("Issue136 fixture reset yields to a held writer and succeeds after release", async () => {
  const { runtime, runDocker, wait } = controlledRuntime();
  runDocker.mockResolvedValueOnce(containerMetadata())
    .mockRejectedValueOnce(yieldError()).mockRejectedValueOnce(yieldError())
    .mockResolvedValueOnce("DELETE 1\n");
  await resetPostgresDeletedCards(target, runtime);
  expect(runDocker).toHaveBeenCalledTimes(4);
  expect(wait.mock.calls).toEqual([[50], [50]]);
  const deletionCalls = runDocker.mock.calls.slice(1);
  for (const [argumentsList, timeoutMs] of deletionCalls) {
    expect(argumentsList).toContain("DELETE FROM deleted_cards");
    expect(argumentsList).toContain("ON_ERROR_STOP=1");
    expect(argumentsList).toContain("VERBOSITY=verbose");
    expect(argumentsList).toContain("-X");
    expect(argumentsList).toContain("--interactive=false");
    expect(argumentsList).toContain(`/var/run/postgresql`);
    expect(argumentsList).toContain(`PGOPTIONS=-c statement_timeout=${timeoutMs} -c lock_timeout=${timeoutMs}`);
  }
  expect(deletionCalls.map(([, timeoutMs]) => timeoutMs)).toEqual([120, 70, 20]);
});

it("Issue136 fixture reset waits out a deadline too short for another observed command", async () => {
  let monotonicTime = 0;
  let deletionAttempts = 0;
  const wait = vi.fn(async (durationMs: number) => { monotonicTime += durationMs; });
  const error = await resetPostgresDeletedCards(target, {
    retryBudgetMs: 1_000, now: () => monotonicTime, wait,
    async runDocker(argumentsList) {
      if (argumentsList.includes("inspect")) return containerMetadata();
      if (argumentsList.includes("DELETE FROM deleted_cards")) {
        deletionAttempts += 1;
        monotonicTime += 450;
        throw yieldError();
      }
      return "[]\n";
    },
  }).catch(error => error);
  expect(error.message).toContain("1 attempts");
  expect(deletionAttempts).toBe(1);
  expect(wait.mock.calls).toEqual([[50], [500]]);
});

it("Issue136 fixture reset stops at its contention deadline and reports holders", async () => {
  const { runtime, runDocker, wait } = controlledRuntime();
  const originalError = yieldError();
  runDocker.mockImplementation(async (argumentsList) => {
    if (argumentsList.includes("inspect")) return containerMetadata();
    if (argumentsList.includes("DELETE FROM deleted_cards")) throw originalError;
    return '[{"pid":987,"mode":"ShareLock","granted":true}]\n';
  });
  const error = await resetPostgresDeletedCards(target, runtime).catch(error => error);
  expect(error.message).toContain(target.project);
  expect(error.message).toContain("120ms");
  expect(error.message).toContain("3 attempts");
  expect(error.message).toContain("987");
  expect(error.message).toContain(permanentDeletionYieldMessage);
  expect(error.cause).toBe(originalError);
  expect(wait.mock.calls).toEqual([[50], [50], [20]]);
  expect(runDocker.mock.calls.at(-1)?.[1]).toBe(1_000);
  expect(runDocker.mock.calls.at(-1)?.[0].at(-1)).toContain("pg_locks");
});

it("Issue136 fixture reset retains contention diagnostics when holder inspection fails", async () => {
  const { runtime, runDocker } = controlledRuntime();
  const originalError = yieldError();
  runDocker.mockResolvedValueOnce(containerMetadata());
  runDocker.mockImplementation(async (argumentsList) => {
    if (argumentsList.includes("DELETE FROM deleted_cards")) throw originalError;
    throw new Error("diagnostic connection unavailable");
  });
  const error = await resetPostgresDeletedCards(target, runtime).catch(error => error);
  expect(error.cause).toBe(originalError);
  expect(error.message).toContain("diagnostic connection unavailable");
  expect(error.message).toContain(permanentDeletionYieldMessage);
});

it.each([
  { code: 1, stderr: "ERROR:  55P03: canceling statement due to lock timeout\n" },
  { code: 1, stderr: "ERROR:  P0080: Prefix transition is in progress\n" },
  { code: 1, stderr: "ERROR:  42P01: relation deleted_cards does not exist\n" },
  { code: 1, stderr: `ERROR:  23514: ${permanentDeletionYieldMessage}\n` },
  { code: "ECONNRESET", stderr: yieldError().stderr },
  { code: 1, killed: true, stderr: yieldError().stderr },
  { code: 1, signal: "SIGTERM", stderr: yieldError().stderr },
  { code: 1, stderr: `${yieldError().stderr}ERROR:  42P01: another failure\n` },
])("Issue136 fixture reset rejects unexpected database errors without retry: %j", async (unexpectedError) => {
  const { runtime, runDocker, wait } = controlledRuntime();
  runDocker.mockResolvedValueOnce(containerMetadata()).mockRejectedValueOnce(unexpectedError);
  await expect(resetPostgresDeletedCards(target, runtime)).rejects.toBe(unexpectedError);
  expect(runDocker).toHaveBeenCalledTimes(2);
  expect(wait).not.toHaveBeenCalled();
  expect(isPermanentDeletionYield(unexpectedError)).toBe(false);
});

it.each([
  { health: null }, { health: { test_instance: false, storage: "postgresql" } },
  { health: { test_instance: "true", storage: "postgresql" } },
  { health: { test_instance: true, storage: "sqlite" } },
  { project: undefined }, { project: "tempo" }, { project: "tempo-regressions-123-abcdef01" },
  { apiUrl: "http://remote:48124/api" }, { apiUrl: "http://127.0.0.1:48124/api?database=live" },
  { apiUrl: "http://user:password@127.0.0.1:48124/api" },
])("Issue136 fixture reset refuses unmarked or mismatched targets: %j", async (targetOverride) => {
  const { runtime, runDocker } = controlledRuntime();
  await expect(resetPostgresDeletedCards({ ...target, ...targetOverride }, runtime)).rejects.toThrow();
  expect(runDocker).not.toHaveBeenCalled();
});

it("Issue136 fixture reset refuses another project's published API before SQL", async () => {
  const { runtime, runDocker } = controlledRuntime();
  runDocker.mockResolvedValueOnce(containerMetadata("49999"));
  await expect(resetPostgresDeletedCards(target, runtime)).rejects.toThrow("does not belong");
  expect(runDocker).toHaveBeenCalledTimes(1);
  expect(runDocker.mock.calls[0][0]).toContain("inspect");
});

it.each(["wrong project", "wrong service", "invalid ID", "missing service", "public bind", "multiple binds", "null container"])(
  "Issue136 fixture reset refuses invalid container ownership without SQL: %s", async (invalidMetadata) => {
    const { runtime, runDocker } = controlledRuntime();
    const containers = containerMetadata().split("\n").map(line => JSON.parse(line));
    if (invalidMetadata === "wrong project") containers[1].labels["com.docker.compose.project"] = "tempo";
    if (invalidMetadata === "wrong service") containers[1].labels["com.docker.compose.service"] = "api";
    if (invalidMetadata === "invalid ID") containers[1].id = "tempo-postgres-1";
    if (invalidMetadata === "missing service") containers.pop();
    if (invalidMetadata === "public bind") containers[0].ports["80/tcp"][0].HostIp = "0.0.0.0";
    if (invalidMetadata === "multiple binds") containers[0].ports["80/tcp"].push({ HostIp: "127.0.0.1", HostPort: "49999" });
    if (invalidMetadata === "null container") containers[1] = null;
    runDocker.mockResolvedValueOnce(containers.map(container => JSON.stringify(container)).join("\n"));
    await expect(resetPostgresDeletedCards(target, runtime)).rejects.toThrow("Product fixture");
    expect(runDocker).toHaveBeenCalledTimes(1);
    expect(runDocker.mock.calls[0][0]).toContain("inspect");
  },
);

it("Issue136 fixture reset propagates ownership inspection failure without SQL", async () => {
  const { runtime, runDocker } = controlledRuntime();
  const inspectionError = new Error("Docker unavailable");
  runDocker.mockRejectedValueOnce(inspectionError);
  await expect(resetPostgresDeletedCards(target, runtime)).rejects.toBe(inspectionError);
  expect(runDocker).toHaveBeenCalledTimes(1);
});

it("Issue136 fixture reset succeeds immediately and repeated resets remain idempotent", async () => {
  const { runtime, runDocker, wait } = controlledRuntime();
  await resetPostgresDeletedCards(target, runtime);
  await resetPostgresDeletedCards(target, runtime);
  expect(runDocker).toHaveBeenCalledTimes(4);
  expect(wait).not.toHaveBeenCalled();
});
