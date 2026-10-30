import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { access, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";
import {
  createPostgresBackup, createBackupRestoreDatabase,
  restorePostgresBackup, removeBackupRestoreDatabase,
} from "../../tooling/cli/backup.mjs";

const repository = fileURLToPath(new URL("../../../", import.meta.url));
const helpers = fileURLToPath(new URL("./", import.meta.url));
const fixtureOwner = randomUUID();
const gracefulMilliseconds = 10_000;
let fixtureRoot;
let backupService;
let image;

async function exists(path) {
  try { await access(path); return true; }
  catch (error) { if (error.code === "ENOENT") return false; throw error; }
}

function docker(argumentsList, { env = process.env, timeout = 30_000 } = {}) {
  return new Promise((resolveResult, reject) => {
    const child = spawn("docker", argumentsList, { cwd: repository, env, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    child.stdout.setEncoding("utf8").on("data", value => { stdout += value; });
    child.stderr.setEncoding("utf8").on("data", value => { stderr += value; });
    const timer = timeout === null ? undefined : setTimeout(() => child.kill("SIGTERM"), timeout);
    child.once("error", error => { clearTimeout(timer); reject(error); });
    child.once("close", (code, signal) => {
      clearTimeout(timer);
      if (code === 0) resolveResult(stdout.trim());
      else reject(new Error(`docker ${argumentsList[0]} failed (${signal ?? code}): ${stderr || stdout}`));
    });
  });
}

async function waitFor(check, description, milliseconds = gracefulMilliseconds) {
  const deadline = performance.now() + milliseconds;
  while (performance.now() < deadline) {
    if (await check()) return;
    await new Promise(resolveDelay => setTimeout(resolveDelay, 25));
  }
  throw new Error(`Timed out waiting for ${description}`);
}

function assertOneShotOverrides() {
  const calls = [];
  const compose = (args, options) => { calls.push({ args, options }); };
  createPostgresBackup(compose, "fixture.dump", "fixture-user", "fixture-db");
  createBackupRestoreDatabase(compose, "fixture-user", "fixture-restore");
  restorePostgresBackup(compose, "fixture-user", "fixture-restore", "fixture.dump");
  removeBackupRestoreDatabase(compose, "fixture-user", "fixture-restore");
  assert.equal(calls.length, 4);
  for (const [{ args }, operation] of calls.map((call, index) => [call, ["pg_dump", "createdb", "pg_restore", "dropdb"][index]])) {
    const entrypoint = args.indexOf("--entrypoint");
    assert.deepEqual(args.slice(entrypoint, entrypoint + 4), ["--entrypoint", "sh", "postgres-backup", "-ec"]);
    assert.ok(args[entrypoint + 4].includes(operation));
    assert.ok(!args.includes(backupService.command[0]), "one-shot command must replace the daemon loop");
  }
}

before(async () => {
  assert.ok(process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN && process.env.TEMPO_REAL_DOCKER,
    "Run through the registered component suite to obtain managed Docker admission");
  // A checkout-local path is shared with Docker Desktop; host /tmp may not be.
  fixtureRoot = await mkdtemp(join(repository, ".tempo-backup-daemon-"));
  const envFile = join(fixtureRoot, "empty.env");
  const secretFile = join(fixtureRoot, "placeholder-secret");
  await writeFile(envFile, "", { mode: 0o600 });
  await writeFile(secretFile, "disposable-fixture-placeholder\n", { mode: 0o600 });
  const env = Object.fromEntries(Object.entries(process.env)
    .filter(([name]) => !/^(COMPOSE_|TEMPO_POSTGRES_|PG)/.test(name)));
  for (const name of ["ADMIN_PASSWORD", "READER_PGPASS", "WRITER_PGPASS", "ADMIN_PGPASS"])
    env[`TEMPO_POSTGRES_${name}_FILE`] = secretFile;
  const services = [];
  for (const composeFile of ["docker-compose.yml", "docker-compose.postgres.yml"]) {
    const configuration = JSON.parse(await docker([
      "compose", "--project-name", `backup-fixture-${fixtureOwner}`, "--env-file", envFile,
      "--file", join(repository, composeFile), "config", "--format", "json",
    ], { env }));
    services.push(configuration.services["postgres-backup"]);
  }
  backupService = services[0];
  for (const field of ["command", "entrypoint", "stop_signal", "image", "mem_limit"])
    assert.deepEqual(services[0][field], services[1][field], `Compose ${field} must agree`);
  assert.deepEqual(backupService.entrypoint, ["sh", "-ec"]);
  assert.equal(backupService.command.length, 1);
  assert.equal(backupService.stop_signal, "SIGTERM");
  assert.equal(backupService.cpus, undefined);
  assert.equal(Number(backupService.mem_limit), 96 * 1024 * 1024);
  // `compose config` escapes dollars for Compose round trips. Docker run takes
  // the final shell argument directly, so consume that escaping exactly once.
  backupService = { ...backupService, command: backupService.command.map(command => command.replaceAll("$$", "$")) };
  assert.ok(backupService.command[0].includes("child=$!"), "the shell must record the asynchronous child PID");
  assertOneShotOverrides();
  try { [image] = JSON.parse(await docker(["image", "inspect", backupService.image])); }
  catch (cause) { throw new Error(`Blocked native proof: selected PostgreSQL image ${backupService.image} is unavailable`, { cause }); }
  assert.match(image.Id, /^sha256:[a-f0-9]{64}$/);
  console.log(JSON.stringify({ image: image.Id, imageStopSignal: image.Config.StopSignal,
    configuredStopSignal: backupService.stop_signal, composeConfigurationsAgree: true, oneShotOverrides: 4 }));
});

after(async () => {
  if (fixtureRoot) {
    // Case cleanup must remove its own directories before shared secrets go away.
    assert.deepEqual((await readdir(fixtureRoot)).sort(), ["empty.env", "placeholder-secret"]);
    await rm(fixtureRoot, { recursive: true });
    assert.equal(await exists(fixtureRoot), false);
    console.log(JSON.stringify({ fixturePathsRemoved: true, fixtureOwner }));
  }
});

async function inspectOwned(name, expectedId) {
  let containers;
  try { containers = JSON.parse(await docker(["container", "inspect", name])); }
  catch (error) {
    if (error.message.includes(name) && /No such (?:object|container)/i.test(error.message)) return undefined;
    throw error;
  }
  const [container] = containers;
  assert.equal(container.Name, `/${name}`);
  if (expectedId) assert.equal(container.Id, expectedId);
  assert.equal(container.Config.Labels["tempo.resource-owner"], process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN);
  assert.equal(container.Config.Labels["tempo.resource-class"], "development");
  assert.equal(container.Config.Labels["tempo.backup-fixture"], fixtureOwner);
  return container;
}

const digest = bytes => createHash("sha256").update(bytes).digest("hex");

async function runCase(phase) {
  const name = `tempo-backup-${phase}-${fixtureOwner}`;
  const directory = await mkdtemp(join(fixtureRoot, `${phase}-`));
  const backups = join(directory, "backups"), state = join(directory, "state"), bin = join(directory, "bin");
  const seed = "tempo-20000101T000000Z.dump";
  const seedBytes = Buffer.from(`preserved archive for ${phase}\n`);
  const seedChecksum = Buffer.from(`${digest(seedBytes)}  /backups/${seed}\n`);
  let containerId, primaryError, completion, stopping;
  try {
    for (const path of [backups, state, bin]) await mkdir(path);
    await writeFile(join(backups, seed), seedBytes);
    await writeFile(join(backups, `${seed}.sha256`), seedChecksum);
    for (const commandName of ["pg_dump", "pg_restore", "sleep"])
      await writeFile(join(bin, commandName), `#!/bin/sh\nexec /bin/bash /helpers/fake-command.sh ${commandName} "$@"\n`, { mode: 0o755 });
    containerId = await docker([
      "run", "--detach", "--name", name, "--label", `tempo.backup-fixture=${fixtureOwner}`,
      // Deliberately constrained shutdown scenario; ordinary service CPU is unrestricted.
      "--cpus", "0.05", "--memory", "96m", "--network", "none", "--tmpfs", "/var/lib/postgresql",
      "--stop-signal", backupService.stop_signal, "--stop-timeout", "10",
      "--mount", `type=bind,src=${backups},dst=/backups`,
      "--mount", `type=bind,src=${state},dst=/state`,
      "--mount", `type=bind,src=${bin},dst=/fixture-bin,readonly`,
      "--mount", `type=bind,src=${helpers},dst=/helpers,readonly`,
      "--env", "PATH=/fixture-bin:/usr/bin:/bin", "--env", `BACKUP_FIXTURE_CASE=${phase}`,
      "--entrypoint", backupService.entrypoint[0], image.Id,
      ...backupService.entrypoint.slice(1), ...backupService.command,
    ], { timeout: null }); // The managed adapter owns admission and startup settlement.
    const initial = await inspectOwned(name, containerId);
    assert.equal(initial.Image, image.Id);
    assert.deepEqual(initial.Config.Entrypoint, [backupService.entrypoint[0]]);
    assert.deepEqual(initial.Config.Cmd, [...backupService.entrypoint.slice(1), ...backupService.command]);
    assert.equal(initial.Config.StopSignal, backupService.stop_signal);
    assert.equal(initial.HostConfig.NanoCpus, 50_000_000);
    assert.equal(initial.HostConfig.Memory, 96 * 1024 * 1024);
    assert.equal(initial.HostConfig.NetworkMode, "none");
    assert.ok(Object.hasOwn(initial.HostConfig.Tmpfs, "/var/lib/postgresql"));
    assert.ok(initial.Mounts.every(mount => mount.Type !== "volume"), "no anonymous or live volumes");
    const blockingCommand = phase === "sleep" ? "sleep" : "pg_dump";
    await waitFor(async () => {
      if (await exists(join(state, `${blockingCommand}.started`))) return true;
      const observed = await inspectOwned(name, containerId);
      assert.equal(observed.State.Running, true, `daemon exited before ${blockingCommand} startup: ${await docker(["logs", name])}`);
      return false;
    }, `${blockingCommand} readiness`, 30_000);

    if (phase === "sleep") await assertCompletedArchive(backups, seed);
    let completed = false;
    const started = performance.now();
    // Convert background failures to values immediately so cleanup can await them.
    completion = docker(["wait", name], { timeout: 20_000 })
      .then(value => { completed = true; return { value, milliseconds: performance.now() - started }; }, error => ({ error }));
    stopping = docker(phase === "sleep" ? ["stop", name] : ["kill", "--signal", "SIGINT", name], { timeout: 15_000 })
      .then(value => ({ value }), error => ({ error }));
    await waitFor(() => exists(join(state, `${blockingCommand}.settled`)), `${blockingCommand} settlement`);
    assert.equal(completed, false, "child settlement must be observed before container completion");
    assert.equal((await inspectOwned(name, containerId)).State.Running, true);
    await writeFile(join(state, `${blockingCommand}.observed`), "observed while parent running\n");
    const finished = await completion;
    const stopResult = await stopping;
    if (finished.error) throw finished.error;
    if (stopResult.error) throw stopResult.error;
    assert.equal(finished.value, "0");
    assert.ok(finished.milliseconds < gracefulMilliseconds, "graceful stop exceeded ten seconds");
    const final = await inspectOwned(name, containerId);
    assert.equal(final.State.ExitCode, 0);
    assert.equal(final.State.OOMKilled, false);
    assert.equal(final.State.Running, false);
    // Docker records `die` after State.FinishedAt; include that completed event.
    const eventsUntil = new Date(Date.parse(final.State.FinishedAt) + 2_000).toISOString();
    const events = (await docker(["events", "--since", initial.State.StartedAt, "--until", eventsUntil,
      "--filter", `container=${containerId}`, "--format", "{{json .}}"])).split("\n").filter(Boolean).map(line => JSON.parse(line));
    assert.ok(events.some(event => event.Action === "die"), "retain container completion event");
    assert.ok(!events.some(event => event.Action === "kill" && /^(9|SIGKILL)$/.test(String(event.Actor.Attributes.signal))), "no forced kill");
    const markers = (await readFile(join(state, "events"), "utf8")).trim().split("\n");
    assert.deepEqual(markers, phase === "sleep"
      ? ["pg_dump:invoked", "pg_restore:validated", "sleep:started", "sleep:signal:TERM", "sleep:settled"]
      : ["pg_dump:invoked", "pg_dump:started", "pg_dump:signal:TERM", "pg_dump:settled"]);
    assert.deepEqual(await readFile(join(backups, seed)), seedBytes);
    assert.deepEqual(await readFile(join(backups, `${seed}.sha256`)), seedChecksum);
    const newFiles = (await readdir(backups)).filter(file => file !== seed && file !== `${seed}.sha256`).sort();
    if (phase === "sleep") await assertCompletedArchive(backups, seed);
    else {
      assert.equal(newFiles.length, 1);
      assert.match(newFiles[0], /^tempo-\d{8}T\d{6}Z\.dump\.partial$/);
      assert.equal(await readFile(join(backups, newFiles[0]), "utf8"), "synthetic PostgreSQL archive\n");
    }
    console.log(JSON.stringify({ phase, image: image.Id, configuredStopSignal: final.Config.StopSignal,
      requestedSignal: phase === "sleep" ? "SIGTERM (docker stop)" : "SIGINT", exitCode: final.State.ExitCode,
      stopMilliseconds: finished.milliseconds, childSettledBeforeCompletion: true,
      seededPairUnchanged: true, archiveFiles: newFiles, markers,
      signalEvents: events.filter(event => event.Action === "kill").map(event => event.Actor.Attributes.signal) }));
  } catch (error) {
    primaryError = error;
    try {
      const container = await inspectOwned(name, containerId);
      console.log(JSON.stringify({ phase, originalError: error.message,
        container: container && { id: container.Id, state: container.State, command: container.Config.Cmd },
        logs: await docker(["logs", name]),
        archiveFiles: await readdir(backups), markers: await readFile(join(state, "events"), "utf8").catch(() => "") }));
    } catch (receiptError) { console.log(`Failure receipt: ${receiptError.message}; original failure: ${error.message}`); }
  } finally {
    const cleanupErrors = [];
    try {
      const owned = await inspectOwned(name, containerId);
      if (owned) await docker(["rm", ...(owned.State.Running ? ["--force"] : []), name]);
      assert.equal(await inspectOwned(name, containerId), undefined);
      for (const pending of [completion, stopping]) if (pending) await pending;
      await rm(directory, { recursive: true });
      assert.equal(await exists(directory), false);
      console.log(JSON.stringify({ phase, cleanup: "verified container and case paths absent", name }));
    } catch (error) { cleanupErrors.push(error); }
    if (cleanupErrors.length) throw new AggregateError([...(primaryError ? [primaryError] : []), ...cleanupErrors],
      `Backup fixture cleanup failed: ${cleanupErrors.map(error => error.message).join("; ")}; original: ${primaryError?.message ?? "none"}`);
  }
  if (primaryError) throw primaryError;
}

async function assertCompletedArchive(backups, seed) {
  const files = (await readdir(backups)).filter(file => file !== seed && file !== `${seed}.sha256`).sort();
  assert.equal(files.length, 2);
  const [archive, checksum] = files;
  assert.match(archive, /^tempo-\d{8}T\d{6}Z\.dump$/);
  assert.equal(checksum, `${archive}.sha256`);
  const bytes = await readFile(join(backups, archive));
  assert.equal(bytes.toString(), "synthetic PostgreSQL archive\n");
  assert.equal(await readFile(join(backups, checksum), "utf8"), `${digest(bytes)}  /backups/${archive}\n`);
}

test("backup daemon sleeping exits gracefully through configured stop signal", { concurrency: false }, () => runCase("sleep"));
test("backup daemon interrupted dump reaps child and retains only partial archive", { concurrency: false }, () => runCase("dump"));
