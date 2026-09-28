// Disposable PostgreSQL product and container-recreation gate.

import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const supported = new Set(["--list", "--skip-browser"]);
for (const argument of process.argv.slice(2)) {
  if (!supported.has(argument)) throw new Error(`Unknown PostgreSQL test option: ${argument}`);
}
const skipBrowser = process.argv.includes("--skip-browser");
if (process.argv.includes("--list")) {
  console.log(JSON.stringify({ stages: ["compose_config", "maintenance_cli", "startup", "command_receipt",
    "container_recreation", "backup_restore", ...(skipBrowser ? [] : ["browser"]),
    "cleanup"] }, null, 2));
  process.exit(0);
}

// Docker Desktop only shares the project checkout, so the short-lived secret
// files must be created there and removed in the cleanup block.
const secretsDirectory = mkdtempSync(join(process.cwd(), ".tempo-pg-test-secrets-"));
chmodSync(secretsDirectory, 0o700);
const administratorPassword = randomBytes(24).toString("hex");
const readerPassword = randomBytes(24).toString("hex");
const writerPassword = randomBytes(24).toString("hex");
const secretFiles = {
  admin_password: `${administratorPassword}\n`,
  admin_pgpass: `postgres:5432:*:postgres:${administratorPassword}\n`,
  reader_password: `${readerPassword}\n`,
  writer_password: `${writerPassword}\n`,
  reader_pgpass: `postgres:5432:tempo:tempo_reader:${readerPassword}\n`,
  writer_pgpass: `postgres:5432:tempo:tempo_writer:${writerPassword}\n`,
};
for (const [name, value] of Object.entries(secretFiles)) {
  writeFileSync(join(secretsDirectory, name), value, { mode: 0o600 });
}
const testPort = await new Promise((resolve, reject) => {
  const server = createServer();
  server.once("error", reject);
  server.listen(0, "127.0.0.1", () => {
    const address = server.address();
    if (!address || typeof address === "string") return reject(new Error("Could not reserve a port"));
    server.close(() => resolve(address.port));
  });
});
const project = `tempo-pg-regressions-${process.pid}`;
const maintenanceImage = `${project}-maintenance`;
const compose = ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml"];
const environment = { ...process.env, TEMPO_PG_TEST_SECRETS: secretsDirectory,
  TEMPO_PG_TEST_PORT: String(testPort),
  TEMPO_POSTGRES_ADMIN_PASSWORD_FILE: join(secretsDirectory, "admin_password"),
  TEMPO_POSTGRES_READER_PGPASS_FILE: join(secretsDirectory, "reader_pgpass"),
  TEMPO_POSTGRES_WRITER_PGPASS_FILE: join(secretsDirectory, "writer_pgpass"),
  TEMPO_POSTGRES_ADMIN_PGPASS_FILE: join(secretsDirectory, "admin_pgpass") };
const origin = `http://127.0.0.1:${testPort}`;

function run(command, argumentsList, options = {}) {
  const result = spawnSync(command, argumentsList, { stdio: "inherit", env: environment, ...options });
  if (result.error || result.status !== 0)
    throw new Error(`${command} ${argumentsList.join(" ")} failed`);
}

async function waitForReady() {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    try {
      const response = await fetch(`${origin}/api/health`, { signal: AbortSignal.timeout(3_000) });
      if (response.ok) {
        const health = await response.json();
        assert.equal(health.storage, "postgresql");
        assert.equal(health.test_instance, true);
        return;
      }
    } catch { /* stack startup */ }
    await new Promise(resolve => setTimeout(resolve, 1_000));
  }
  throw new Error("Disposable PostgreSQL Tempo did not become ready");
}

async function get(path) {
  const response = await fetch(`${origin}/api/${path}`, { signal: AbortSignal.timeout(15_000) });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status} ${await response.text()}`);
  return response.json();
}

async function confirm(response) {
  if (response.status !== 202) {
    if (!response.ok) throw new Error(`command HTTP ${response.status} ${await response.text()}`);
    return response.json();
  }
  const pending = await response.json();
  assert(pending.operation_id, "202 command must return an operation ID");
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const receipt = await get(`operations/${encodeURIComponent(pending.operation_id)}`);
    if (receipt.state === "complete") return receipt.response;
    if (receipt.state === "failed") throw new Error(`PostgreSQL command failed: ${JSON.stringify(receipt.error)}`);
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error(`PostgreSQL operation ${pending.operation_id} remains pending`);
}

let failed = false;
try {
  const config = spawnSync("docker", [...compose, "config", "--format", "json"],
    { encoding: "utf8", env: environment });
  assert.equal(config.status, 0, config.stderr);
  const stack = JSON.parse(config.stdout);
  assert.equal(stack.services.api.environment.TEMPO_DATABASE_WRITE_URL, undefined);
  assert.equal(stack.services.api.environment.TEMPO_DB_PATH, undefined);
  assert.equal(stack.volumes["postgres-test-data"].external, undefined);
  assert.equal(stack.volumes["redis-test-data"].external, undefined);
  assert(!JSON.stringify(stack.services.api.volumes ?? []).includes("tempo-data"));
  console.log("PASS PostgreSQL API has reader credentials and no SQLite mount");
  const defaultConfig = spawnSync("docker", ["compose", "-f", "docker-compose.yml",
    "config", "--format", "json"], { encoding: "utf8", env: environment });
  assert.equal(defaultConfig.status, 0, defaultConfig.stderr);
  const defaultStack = JSON.parse(defaultConfig.stdout);
  assert(defaultStack.services.postgres && defaultStack.services["foreground-worker"]);
  assert.equal(defaultStack.services.api.environment.TEMPO_DATABASE_WRITE_URL, undefined);
  assert.equal(defaultStack.services.api.environment.TEMPO_DB_PATH, undefined);
  assert(!JSON.stringify(defaultStack.services.api.volumes ?? []).includes("tempo-data"));
  const backupCommand = defaultStack.services["postgres-backup"].command;
  assert.equal(backupCommand.length, 1, "backup loop must be one shell argument");
  const backupSyntax = spawnSync("sh", ["-n", "-c", backupCommand[0].replaceAll("$$", "$")],
    { encoding: "utf8" });
  assert.equal(backupSyntax.status, 0, backupSyntax.stderr);
  console.log("PASS recurring PostgreSQL backup loop has valid shell syntax");
  console.log("PASS default Compose selects PostgreSQL and keeps SQLite isolated");
  run("docker", ["build", "-f", "Dockerfile.postgres-maintenance", "-t", maintenanceImage, "."]);
  for (const script of ["apply_postgres_migrations.py", "migrate_sqlite_to_postgres.py"]) {
    run("docker", ["run", "--rm", maintenanceImage, `scripts/${script}`, "--help"]);
  }
  console.log("PASS PostgreSQL maintenance image starts migration and import commands");
  run("docker", [...compose, "up", "--build", "-d"]);
  await waitForReady();
  const runningContainers = spawnSync("docker", [...compose, "ps", "--format", "json"],
    { encoding: "utf8", env: environment });
  assert.equal(runningContainers.status, 0, runningContainers.stderr);
  const runningServices = new Set(runningContainers.stdout.trim().split("\n")
    .filter(Boolean).map(line => JSON.parse(line))
    .filter(container => container.State === "running")
    .map(container => container.Service));
  for (const service of ["api", "foreground-worker", "background-worker",
    "background-scheduler", "defense-engine", "maia-worker", "web"])
    assert(runningServices.has(service), `${service} exited during PostgreSQL startup`);
  const settings = await get("settings");
  const before = await get("queue/today");
  const operationId = `pg-durability-${process.pid}`;
  const updatedSettings = { ...settings, new_cards_per_day: settings.new_cards_per_day + 1 };
  const sendSettings = () => fetch(`${origin}/api/settings`, {
    method: "PUT", headers: { "Content-Type": "application/json", "Idempotency-Key": operationId },
    body: JSON.stringify(updatedSettings), signal: AbortSignal.timeout(15_000),
  });
  await confirm(await sendSettings());
  await confirm(await sendSettings());
  const savedSettings = await get("settings");
  assert.equal(savedSettings.new_cards_per_day, updatedSettings.new_cards_per_day);
  console.log("PASS PostgreSQL command receipt replays one settings change");
  run("docker", [...compose, "down"]);
  run("docker", [...compose, "up", "-d"]);
  await waitForReady();
  assert.equal((await get("settings")).new_cards_per_day, updatedSettings.new_cards_per_day);
  const receipt = await get(`operations/${operationId}`);
  assert.equal(receipt.state, "complete");
  const after = await get("queue/today");
  assert.deepEqual(after.cards.map(card => card.queue_entry_id),
    before.cards.map(card => card.queue_entry_id));
  console.log("PASS PostgreSQL settings, receipt, and queue order survive container recreation");
  run("docker", [...compose, "stop", "api", "foreground-worker", "background-worker",
    "background-scheduler", "defense-engine", "maia-worker", "web"]);
  run("docker", [...compose, "exec", "-T", "postgres", "sh", "-ec",
    "pg_dump -U postgres -d tempo -Fc -f /tmp/tempo-test.dump && " +
    "pg_restore -l /tmp/tempo-test.dump >/dev/null && " +
    "createdb -U postgres tempo_restore_check && " +
    "pg_restore -U postgres -d tempo_restore_check /tmp/tempo-test.dump"]);
  run("docker", [...compose, "run", "--rm", "--no-deps", "schema", "python",
    "/source/scripts/verify_postgres_backup.py",
    "postgresql://postgres@postgres:5432/tempo",
    "postgresql://postgres@postgres:5432/tempo_restore_check"]);
  run("docker", [...compose, "exec", "-T", "postgres", "sh", "-ec",
    "dropdb -U postgres tempo_restore_check && rm /tmp/tempo-test.dump"]);
  console.log("PASS every PostgreSQL table matches after backup restoration");
  run("docker", [...compose, "up", "-d"]);
  await waitForReady();
  if (!skipBrowser) {
    const browserArguments = ["playwright", "test"];
    if (process.env.TEMPO_PG_BROWSER_GREP)
      browserArguments.push("--grep", process.env.TEMPO_PG_BROWSER_GREP);
    run("npx", browserArguments, { env: { ...environment,
      TEMPO_DOCKER_URL: origin,
      TEMPO_TEST_OUTPUT_DIR: join(process.cwd(), "test-results", `browser-postgres-${process.pid}`),
    } });
  }
} catch (error) {
  failed = true;
  console.error(error);
  spawnSync("docker", [...compose, "logs", "--tail=80"], { stdio: "inherit", env: environment });
} finally {
  const stopped = spawnSync("docker", [...compose, "down", "-v"],
    { stdio: "inherit", env: environment });
  if (stopped.status !== 0) failed = true;
  rmSync(secretsDirectory, { recursive: true, force: true });
}
process.exit(failed ? 1 : 0);
