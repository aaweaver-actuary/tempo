// Real CLI maintenance on its own populated PostgreSQL volumes. All images are
// reused from this test invocation; no live product target is adopted.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { atomicJson, commandExecutor, portsFromConfig, productVolumes, redact, schemaVersionFromSource, targetKey, validateTarget } from "./tempo-deployment.mjs";
import { configurationFingerprint, createRuntime, executeLifecycle } from "./tempo-runtime.mjs";

export async function verifyTempoCliLifecycle({ project, environment, composeFiles, revision }) {
  const root = process.cwd();
  const childProject = `${project}-cli`;
  const directory = join(root, "test-results", "tempo-cli", childProject);
  mkdirSync(directory, { recursive: true });
  const webPort = await new Promise((resolvePort, reject) => {
    const server = createServer(); server.once("error", reject);
    server.listen(0, "127.0.0.1", () => { const port = server.address().port; server.close(() => resolvePort(port)); });
  });
  const childEnvironment = { ...environment, TEMPO_PG_TEST_PORT: String(webPort) };
  const secretValues = ["admin_password", "reader_password", "writer_password"]
    .map(name => readFileSync(join(environment.TEMPO_PG_TEST_SECRETS, name), "utf8").trim());
  const commandLog = [];
  const output = [];
  const execute = commandExecutor({ root, environment: childEnvironment, secretValues,
    output: message => { output.push(message); console.log(message); } });
  const run = (command, args, options) => { commandLog.push({ command, args }); return execute(command, args, options); };
  const context = (await run("docker", ["context", "show"])).stdout.trim();
  const docker = (args, options) => run("docker", ["--context", context, ...args], options);
  const productSecrets = mkdtempSync(join(tmpdir(), "tempo-compose-contract-"));
  const productSecretFiles = [
    ["ADMIN_PASSWORD", "admin_password"], ["READER_PGPASS", "reader_pgpass"], ["WRITER_PGPASS", "writer_pgpass"],
    ["ADMIN_PGPASS", "admin_pgpass"], ["READER_PASSWORD", "reader_password"], ["WRITER_PASSWORD", "writer_password"],
  ];
  try {
    const productEnvironment = { ...childEnvironment, ...Object.fromEntries(productSecretFiles.map(([name, file]) => {
      const destination = join(productSecrets, file);
      writeFileSync(destination, readFileSync(join(environment.TEMPO_PG_TEST_SECRETS, file)), { mode: 0o600 });
      return [`TEMPO_POSTGRES_${name}_FILE`, destination];
    })) };
    const productConfig = JSON.parse((await commandExecutor({ root, environment: productEnvironment, secretValues })("docker", [
      "--context", context, "compose", "--project-directory", root, "-p", "tempo", "-f", join(root, "docker-compose.yml"),
      "-f", join(root, "docker-compose.postgres-maintenance.yml"), "--profile", "maintenance", "config", "--format", "json",
    ])).stdout);
    validateTarget(productConfig, { root, project: "tempo", ports: portsFromConfig(productConfig),
      volumes: productVolumes, postgresVolumeKey: "tempo-postgres-data" });
    console.log("PASS current product Compose persistent-volume ownership contract (read-only configuration)");
  } finally { rmSync(productSecrets, { recursive: true, force: true }); }
  const composeArguments = ["compose", "--project-directory", root, "-p", childProject,
    ...composeFiles.flatMap(file => ["-f", file])];
  const compose = (args, options) => docker([...composeArguments, ...args], options);
  const config = JSON.parse((await compose(["--profile", "maintenance", "config", "--format", "json"])).stdout);
  const daemonId = (await docker(["info", "--format", "{{.ID}}"])).stdout.trim();
  const target = { version: 1, root, project: childProject, context, daemonId, composeFiles, envFile: null,
    disposable: true, volumes: Object.fromEntries(Object.entries(config.volumes).map(([key, volume]) =>
      [key, { name: volume.name, external: Boolean(volume.external) }])),
    ports: portsFromConfig(config), postgresVolumeKey: "postgres-test-data", postgresMajor: 18,
    postgresImage: config.services.postgres.image, webUrl: `http://127.0.0.1:${webPort}` };
  assert(Object.values(target.volumes).every(volume => volume.name.startsWith(`${childProject}_`)));
  assert.equal((await docker(["ps", "-aq", "--filter", `label=com.docker.compose.project=${childProject}`])).stdout.trim(), "");
  assert.equal((await docker(["volume", "ls", "-q", "--filter", `label=com.docker.compose.project=${childProject}`])).stdout.trim(), "");
  assert.equal((await docker(["network", "ls", "-q", "--filter", `label=com.docker.compose.project=${childProject}`])).stdout.trim(), "");
  for (const volume of Object.values(target.volumes))
    assert.equal((await docker(["volume", "ls", "-q", "--filter", `name=^${volume.name}$`])).stdout.trim(), "");
  const images = {};
  for (const [name, service] of Object.entries(config.services)) {
    const tag = service.image ?? `${project}-${name}`;
    images[name] = JSON.parse((await docker(["image", "inspect", tag])).stdout)[0].Id;
  }
  const fixtureImages = join(directory, "fixture-images.json");
  atomicJson(fixtureImages, { services: Object.fromEntries(Object.entries(images).map(([name, image]) => [name, { image }])) });
  composeArguments.push("-f", fixtureImages);
  const stateRoot = join(directory, "state");
  const stateDirectory = join(stateRoot, targetKey(target));
  const registration = join(directory, "registration.json"); atomicJson(registration, target);
  const evidence = { commit: revision, disposable_runner: childProject };
  let ownsResources = false;
  let failure;
  try {
    ownsResources = true;
    for (const [key, volume] of Object.entries(target.volumes)) {
      await docker(["volume", "create", "--label", `com.docker.compose.project=${childProject}`,
        "--label", `com.docker.compose.volume=${key}`, volume.name]);
    }
    await compose(["up", "-d", "--no-build", "--no-deps", "--wait", "postgres", "redis"], { echo: true });
    await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "migration", "scripts/check_postgres_cli_lifecycle.py"], { echo: true });
    const readHistory = async () => JSON.parse((await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "migration",
      "scripts/check_postgres_cli_lifecycle.py", "--history"])).stdout);
    const expected = await readHistory();
    const readNormalization = async () => JSON.parse((await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "migration",
      "scripts/check_postgres_cli_lifecycle.py", "--normalization"])).stdout);
    const legacyTacticalRow = await readNormalization();
    assert.equal(legacyTacticalRow.card_bucket, "tactics");
    assert.equal(legacyTacticalRow.content_type, "tactics");
    assert.equal(legacyTacticalRow.repertoire_id, "__game_tactics__", "fixture satisfies migration 025's predicate");
    const preparedImages = { revision, images, configFingerprint: configurationFingerprint(config) };
    // Commit the real schema-16 upgrade, then inject an unexpected historical
    // mutation before the genuine verifier reads it. The current ledger must
    // not let the next process skip its original H0 obligation.
    const mutatingRun = async (command, args, options) => {
      const result = await run(command, args, options);
      if (args.includes("scripts/apply_postgres_migrations.py") && !args.includes("--check"))
        await compose(["exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo", "-c",
          "UPDATE reviews SET rating='incorrect' WHERE card_id='cli-history'"]);
      return result;
    };
    const runtime = createRuntime(target, { run: mutatingRun, stateDirectory, revision, evidence, preparedImages });
    await runtime.inspectTarget();
    await assert.rejects(executeLifecycle({ recreate: true }, runtime), /Original migration history verification failed/);
    const originalGuard = JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8"));
    assert.equal(originalGuard.state, "pending");
    const failedReceipt = await compose(["exec", "-T", "postgres", "psql", "-tA", "-U", "postgres", "-d", "tempo", "-c", "SELECT MAX(version) FROM tempo_schema_migrations"]);
    assert.equal(Number(failedReceipt.stdout.trim()), schemaVersionFromSource(readFileSync("backend/app/schema_version.py", "utf8")));
    const retryRuntime = () => createRuntime(target, { run, stateDirectory, revision, evidence, preparedImages, retry: true });
    const failedRetry = retryRuntime(); await failedRetry.inspectTarget();
    const retryStart = commandLog.length;
    await assert.rejects(executeLifecycle({ recreate: false }, failedRetry), /Original migration history verification failed/);
    assert(!commandLog.slice(retryStart).some(call => call.args.includes("scripts/verify_postgres_cli_state.py") && !call.args.includes("--expected")));
    assert(!commandLog.slice(retryStart).some(call => call.args.includes("scripts/apply_postgres_migrations.py") && !call.args.includes("--check")));
    assert.deepEqual(JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8")).study_invariants, originalGuard.study_invariants);
    assert(!(await failedRetry.runningServices()).includes("api"));
    await compose(["exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo", "-c", "UPDATE reviews SET rating='correct' WHERE card_id='cli-history'"]);
    const repairedRetry = retryRuntime(); await repairedRetry.inspectTarget();
    await executeLifecycle({ recreate: false }, repairedRetry);
    assert.equal(JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8")).state, "verified");
    const record = JSON.parse(readFileSync(join(stateDirectory, "deployment.json"), "utf8"));
    assert.equal(record.schema, schemaVersionFromSource(readFileSync("backend/app/schema_version.py", "utf8")));
    assert.equal(record.backup.verified, true);
    assert.equal(JSON.parse(readFileSync(join(stateDirectory, "operation.json"), "utf8")).phase, "ready");
    assert.deepEqual(await readNormalization(), { ...legacyTacticalRow, card_bucket: "tactic", content_type: "tactic" },
      "migration 025 normalizes the same card/queue row and preserves its historical identity");
    assert.deepEqual(await readHistory(), expected, "upgrade preserves original study history while workers may add unrelated receipts");

    const repeat = createRuntime(target, { run, stateDirectory, revision, evidence, previous: record });
    await repeat.inspectTarget();
    const repeatStart = commandLog.length;
    await executeLifecycle({ recreate: false }, repeat);
    assert(!commandLog.slice(repeatStart).some(call => call.args.includes("build") || call.args.includes("stop")
      || (call.args.includes("scripts/apply_postgres_migrations.py") && !call.args.includes("--check"))));
    assert(commandLog.slice(repeatStart).filter(call => call.args.includes("up") && call.args.includes("postgres"))
      .every(call => call.args.includes("--no-recreate")));

    // Exercise the actual installed-command entry point against PostgreSQL.
    // This checkout is a task branch, so start must explicitly retain the
    // already recorded disposable deployment rather than fetching product main.
    const child = spawnSync(process.execPath, ["scripts/tempo-cli.mjs", "start", "--no-open", "--config", registration], {
      encoding: "utf8", env: { ...childEnvironment, TEMPO_CLI_STATE_DIR: stateRoot }, timeout: 180_000,
    });
    assert.equal(child.status, 0, child.stdout + child.stderr);
    assert(child.stdout.includes("update remains blocked")); output.push(child.stdout);

    // Simulate uncommitted candidate dependencies on these disposable volumes:
    // Redis has a different command/configuration, and PostgreSQL is absent.
    // The recorded deployment must restore its containers without data rollback.
    await repeat.stopApplications();
    const failedCandidateOverride = join(directory, "uncommitted-dependencies.json");
    atomicJson(failedCandidateOverride, { services: { redis: { command: ["redis-server", "--appendonly", "yes", "--appendfsync", "always"] } } });
    await docker([...composeArguments, "-f", failedCandidateOverride, "up", "-d", "--no-build", "--no-deps", "--force-recreate", "--wait", "redis"]);
    await compose(["rm", "-s", "-f", "postgres"]);
    const correctedFallback = createRuntime(target, { run, stateDirectory, revision, previous: record, fallback: true });
    await correctedFallback.inspectTarget();
    const correctionStart = commandLog.length;
    await executeLifecycle({ recreate: false }, correctedFallback);
    const correctionCalls = commandLog.slice(correctionStart);
    const correctionShutdown = correctionCalls.findIndex(call => call.args.includes("stop") && call.args.includes("foreground-worker"));
    const correctionStartup = correctionCalls.findIndex(call => call.args.includes("up") && call.args.includes("postgres"));
    assert(correctionShutdown >= 0 && correctionStartup > correctionShutdown);
    assert(correctionCalls[correctionStartup].args.includes("--force-recreate"));
    assert.deepEqual((await correctedFallback.ensureImages()), { dependenciesMayChange: false }, "restored dependencies match saved immutable image/config identities");
    assert.deepEqual(await readHistory(), expected, "dependency correction preserves authoritative data");

    const beforeRestart = (await repeat.compose(["ps", "-q", "api"])).stdout.trim();
    const restartStart = commandLog.length;
    await executeLifecycle({ recreate: true }, repeat);
    const restartCalls = commandLog.slice(restartStart);
    const writerShutdown = restartCalls.findIndex(call => call.args.includes("stop") && call.args.includes("foreground-worker"));
    const dependencyStartup = restartCalls.findIndex(call => call.args.includes("up") && call.args.includes("postgres"));
    assert(writerShutdown >= 0 && dependencyStartup > writerShutdown, "real restart stops writers before dependency changes");
    const afterRestart = (await repeat.compose(["ps", "-q", "api"])).stdout.trim();
    assert.notEqual(beforeRestart, afterRestart, "restart recreates the real API container");
    assert.deepEqual(await readHistory(), expected, "restart preserves original study history");

    // A genuinely rejected PostgreSQL migration, after a verified backup, must
    // leave all application consumers stopped. Keep the final column while
    // deleting its ledger row in this disposable fixture to force duplicate DDL.
    await repeat.stopApplications();
    await repeat.compose(["exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo", "-c",
      `DELETE FROM tempo_schema_migrations WHERE version=${record.schema}`]);
    const rejected = createRuntime(target, { run, stateDirectory, revision, evidence, previous: record });
    await rejected.inspectTarget();
    await assert.rejects(executeLifecycle({ recreate: true }, rejected), /already exists/);
    const running = await rejected.runningServices();
    assert(!running.some(name => ["api", "foreground-worker", "background-worker", "web", "defense-engine", "maia-worker"].includes(name)));
    assert.deepEqual(await readHistory(), expected, "rejected DDL preserves original study history");
    console.log("PASS Tempo CLI populated 16-to-current upgrade permits migration 025 normalization, retains original H0 through committed-schema failure/retry/repair, restores backup, quiesces before dependencies, corrects uncommitted/missing fallback dependencies, and preserves repeat/restart/rejected-migration history");
  } catch (error) { failure = error; }
  finally {
    try {
      const ids = (await docker(["ps", "-aq", "--filter", `label=com.docker.compose.project=${childProject}`])).stdout.trim().split(/\s+/).filter(Boolean);
      const ownership = ids.length ? JSON.parse((await docker(["inspect", ...ids])).stdout).map(container => ({ id: container.Id,
        name: container.Name, created: container.Created, started_at: container.State.StartedAt, image: container.Image,
        project: container.Config.Labels["com.docker.compose.project"], mounts: container.Mounts.map(mount => ({ name: mount.Name, destination: mount.Destination })) })) : [];
      atomicJson(join(directory, "ownership.json"), { checkout: root, revision, project: childProject, context, containers: ownership,
        volumes: target.volumes, images, teardown: ["docker", "--context", context, ...composeArguments, "--profile", "maintenance", "down", "-v"] });
      const logs = await compose(["--profile", "maintenance", "logs", "--no-color", "--tail", "80"], { allowFailure: true });
      output.push(logs.stdout, logs.stderr);
      writeFileSync(join(directory, "lifecycle.log"), redact(output.join("\n"), secretValues), { mode: 0o600 });
    } catch (error) { failure = failure ? new AggregateError([failure, error], "CLI lifecycle and diagnostics failed") : error; }
    if (ownsResources) {
      try {
        await compose(["--profile", "maintenance", "down", "-v"], { echo: true });
        for (const volume of Object.values(target.volumes))
          assert.notEqual((await docker(["volume", "inspect", volume.name], { allowFailure: true })).code, 0, "CLI fixture volume teardown must succeed");
        assert.equal((await docker(["ps", "-aq", "--filter", `label=com.docker.compose.project=${childProject}`])).stdout.trim(), "");
        atomicJson(join(directory, "cleanup.json"), { completed: true, project: childProject, at: new Date().toISOString() });
      } catch (error) { failure = failure ? new AggregateError([failure, error], "CLI lifecycle and cleanup failed") : error; }
    }
  }
  if (failure) throw failure;
}
