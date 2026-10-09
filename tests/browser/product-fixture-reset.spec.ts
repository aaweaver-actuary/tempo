import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { randomUUID } from "node:crypto";
import { test, expect } from "./observability";
import {
  isPermanentDeletionYield, postgresFixtureSqlArguments, verifyPostgresFixtureTarget,
  resetPostgresDeletedCards, type PostgresFixtureTarget,
} from "./product-fixture-reset";

const executeFile = promisify(execFile);
const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
const apiUrl = `${process.env.TEMPO_DOCKER_URL}/api`;
let postgresContainerId: string;
const reservation = "hashtextextended('tempo:prefix-transition:reservations',0)";

async function runDocker(argumentsList: string[], timeoutMs: number): Promise<string> {
  try {
    return (await executeFile("docker", argumentsList, {
      encoding: "utf8", timeout: timeoutMs,
    })).stdout;
  } catch (error) {
    if (error && typeof error === "object" && !isPermanentDeletionYield(error))
      console.error("Issue136 command failure", JSON.stringify({
        code: Reflect.get(error, "code"), killed: Reflect.get(error, "killed"),
        signal: Reflect.get(error, "signal"), stdout: Reflect.get(error, "stdout"),
        stderr: Reflect.get(error, "stderr"), timeoutMs,
      }));
    throw error;
  }
}

async function sql(statement: string): Promise<string> {
  return runDocker(postgresFixtureSqlArguments(postgresContainerId, statement, 5_000), 5_000);
}

async function bounded<T>(promise: Promise<T>, label: string): Promise<T> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([promise, new Promise<never>((_, reject) => {
      timeout = setTimeout(() => reject(new Error(`${label} did not settle within 5 seconds`)), 5_000);
    })]);
  } finally { clearTimeout(timeout); }
}

async function holdWriter() {
  const ownerName = `issue136-${randomUUID()}`;
  const child = spawn("docker", ["exec", "-i",
    "-e", `PGAPPNAME=${ownerName}`, postgresContainerId, "psql", "-X", "-w", "-h", "/var/run/postgresql",
    "-U", "postgres", "-d", "tempo", "-qAt", "-v", "ON_ERROR_STOP=1"], { stdio: "pipe" });
  let output = "";
  let diagnostics = "";
  let markReady!: () => void;
  let rejectReady!: (error: Error) => void;
  const ready = new Promise<void>((resolve, reject) => { markReady = resolve; rejectReady = reject; });
  // Attach rejection handling immediately; teardown may follow readiness failure.
  void ready.catch(() => {});
  const closed = new Promise<void>((resolve, reject) => {
    child.once("error", error => { rejectReady(error); reject(error); });
    child.once("close", (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`Held writer exited ${code}/${signal}: ${diagnostics}`));
      rejectReady(new Error(`Held writer closed before readiness: ${diagnostics}`));
    });
  });
  void closed.catch(() => {});
  child.stdout.on("data", chunk => {
    output += chunk.toString();
    if (output.includes("ISSUE136_READY\n")) markReady();
  });
  child.stderr.on("data", chunk => { diagnostics += chunk.toString(); });
  child.stdin.on("error", error => rejectReady(error));
  child.stdin.write(`BEGIN;\nSELECT pg_advisory_xact_lock_shared(${reservation});\n\\echo ISSUE136_READY\n`);
  let released = false;
  async function release() {
    if (released) return;
    released = true;
    try {
      child.stdin.end("ROLLBACK;\n\\q\n");
      await bounded(closed, "held writer release");
    } catch (error) {
      // Terminate only this test's uniquely named backend before reaping Docker.
      try {
        await sql(`SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='${ownerName}'`);
      } finally {
        child.kill("SIGKILL");
        await bounded(closed.catch(() => {}), "held writer cleanup");
      }
      throw error;
    }
  }
  try { await bounded(ready, "held writer readiness"); }
  catch (error) { await release(); throw error; }
  return { release, ownerName };
}

async function seedExclusion(cardId: string) {
  await sql(`BEGIN; SELECT pg_advisory_xact_lock(${reservation});
    INSERT INTO deleted_cards(card_id,deleted_at) VALUES('${cardId}','2026-10-09'); COMMIT;`);
}

test("Issue136 fixture reset yields to a held writer and succeeds after release", async ({ request }) => {
  const healthResponse = await request.get(`${apiUrl}/health`);
  const target: PostgresFixtureTarget = { project, apiUrl, health: await healthResponse.json() };
  postgresContainerId = await verifyPostgresFixtureTarget(target);
  await resetPostgresDeletedCards(target);
  const cardId = `issue136-${randomUUID()}`;
  await seedExclusion(cardId);
  expect((await sql(`SELECT count(*) FROM deleted_cards WHERE card_id='${cardId}'`)).trim()).toBe("1");
  const writer = await holdWriter();
  let pendingReset: Promise<void> | undefined;
  try {
    // This is the original one-shot operation, with verbose error reporting.
    const originalError = await sql("DELETE FROM deleted_cards").catch(error => error);
    expect(isPermanentDeletionYield(originalError)).toBe(true);
    let sawYield!: () => void;
    const yielded = new Promise<void>(resolve => { sawYield = resolve; });
    pendingReset = resetPostgresDeletedCards(target, {
      async runDocker(argumentsList, timeoutMs) {
        try { return await runDocker(argumentsList, timeoutMs); }
        catch (error) {
          if (isPermanentDeletionYield(error)) sawYield();
          throw error;
        }
      },
    });
    void pendingReset.catch(() => {});
    await bounded(yielded, "reset's real production yield");
    await writer.release();
    await pendingReset;
    expect((await sql(`SELECT count(*) FROM deleted_cards WHERE card_id='${cardId}'`)).trim()).toBe("0");
    await resetPostgresDeletedCards(target);
    expect((await sql("SELECT count(*) FROM deleted_cards")).trim()).toBe("0");
    expect((await sql("SELECT count(*) FROM pg_trigger WHERE tgname='permanent_deletion_reservation' AND tgenabled='O'")).trim()).toBe("1");
  } finally {
    try { await writer.release(); }
    finally {
      await pendingReset?.catch(() => {});
      await resetPostgresDeletedCards(target);
    }
  }
});

test("Issue136 fixture reset stops at its contention deadline and retains exclusions", async ({ request }) => {
  const target: PostgresFixtureTarget = { project, apiUrl, health: await (await request.get(`${apiUrl}/health`)).json() };
  postgresContainerId = await verifyPostgresFixtureTarget(target);
  await resetPostgresDeletedCards(target);
  const cardId = `issue136-${randomUUID()}`;
  await seedExclusion(cardId);
  const writer = await holdWriter();
  try {
    const startedAt = performance.now();
    const error = await resetPostgresDeletedCards(target).catch(error => error);
    expect(error).toBeInstanceOf(Error);
    expect(error.message).toContain("exhausted permanent-deletion coordination");
    expect(error.message).toContain(project);
    expect(error.message).toContain(writer.ownerName);
    expect(isPermanentDeletionYield(error.cause)).toBe(true);
    // Includes the separate 5-second ownership check and 1-second diagnostics.
    expect(performance.now() - startedAt).toBeLessThan(17_000);
    expect((await sql(`SELECT count(*) FROM deleted_cards WHERE card_id='${cardId}'`)).trim()).toBe("1");
  } finally {
    try { await writer.release(); }
    finally { await resetPostgresDeletedCards(target); }
  }
});

test("Issue136 fixture reset rejects unexpected real database errors without retry", async ({ request }) => {
  const target: PostgresFixtureTarget = { project, apiUrl, health: await (await request.get(`${apiUrl}/health`)).json() };
  postgresContainerId = await verifyPostgresFixtureTarget(target);
  let deletionAttempts = 0;
  const error = await resetPostgresDeletedCards(target, {
    async runDocker(argumentsList, timeoutMs) {
      if (argumentsList.at(-1) === "DELETE FROM deleted_cards") {
        deletionAttempts += 1;
        return runDocker(postgresFixtureSqlArguments(postgresContainerId, "SELECT * FROM issue136_nonexistent_relation", timeoutMs), timeoutMs);
      }
      return runDocker(argumentsList, timeoutMs);
    },
  }).catch(error => error);
  expect(error.stderr).toContain("42P01");
  expect(deletionAttempts).toBe(1);
  expect(isPermanentDeletionYield(error)).toBe(false);
});
