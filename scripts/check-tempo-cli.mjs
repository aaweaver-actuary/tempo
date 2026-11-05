// Real CLI maintenance on its own populated PostgreSQL volumes. All images are
// reused from this test invocation; no live product target is adopted.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createHash, createHmac } from "node:crypto";
import { closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, mkdtempSync, openSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { isAbsolute, join } from "node:path";
import { acquireTargetLock, atomicJson, commandExecutor, portsFromConfig, productVolumes, redact, schemaVersionFromSource, targetKey, validateTarget } from "./tempo-deployment.mjs";
import { applicationServices, configurationFingerprint, createRuntime, executeLifecycle, releaseComposeFiles } from "./tempo-runtime.mjs";
import { postgresCommandTiming } from "./test-scenario-timings.mjs";
import { studyDurabilityPgn } from "../database/tests/postgres-test-fixture.mjs";
import { Chess } from "chess.js";
import { verifyPersistedRedisReadiness } from "../database/tests/redis/check-redis-readiness.mjs";
import { forgetMigrationSuffix, markHistoryIncorrect, markHistoryCorrect, readLatestMigration } from "../database/tests/cli/lifecycle-storage.mjs";

async function exerciseIsolatedDefense({ target, run, compose, docker, makeRuntime, lifecycle, stateDirectory,
  revision, evidence, preparedImages, selectionPath, componentPython, environment, childEnvironment, images }) {
  const release = acquireTargetLock(stateDirectory);
  const syncDirectory = path => { const fd = openSync(path, "r"); try { fsyncSync(fd); } finally { closeSync(fd); } };
  const writeOnce = (path, value) => {
    const fd = openSync(path, "wx", 0o600);
    try { writeFileSync(fd, value); fsyncSync(fd); } finally { closeSync(fd); }
  };
  const maintenance = (script, args = [], mount = null) => compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T",
    ...(mount ? ["--volume", `${mount}:/cut62`] : []), "migration", script, ...args]);
  try {
    const runtime = makeRuntime(target, { run, stateDirectory, revision, evidence, preparedImages, defenseSelection: selectionPath });
    await runtime.inspectTarget(); await runtime.ensureImages();
    await assert.rejects(runtime.preflightSchema(), /Nonempty Defense cleanup is unavailable/,
      "The shipped launcher must still reject nonempty Defense cleanup.");
    await runtime.stopApplications(); await runtime.ensureDatabase({ allowRecreation: false });
    const status = await runtime.checkSchema();
    assert.equal(status.applied_versions.at(-1), 59); assert.equal(status.expected_version, 62);
    const backup = await runtime.backup();
    assert.equal(backup.verified, true); assert.equal(backup.restore_removed, true);
    const operation = JSON.parse(readFileSync(join(stateDirectory, "operation.json"), "utf8"));
    const attempt = operation.id;
    assert.match(attempt, /^[0-9a-f-]{36}$/i);
    const guardPath = join(stateDirectory, "migration-guard.json");
    assert(!existsSync(guardPath), "An original migration guard must never be replaced.");
    const witnessName = `cut62-${attempt}.json`, preName = `pre62-${attempt}.json`;
    const witnessDirectory = join(stateDirectory, witnessName.slice(0, -5));
    const preDirectory = join(stateDirectory, preName.slice(0, -5));
    if (/[:,\n\r]/.test(stateDirectory)) throw new Error("Disposable private mount path is unsafe.");
    const study = JSON.parse((await maintenance("database/tooling/verify_postgres_cli_state.py")).stdout);
    let guard = { version: 1, target: targetKey(target), database: { name: "tempo", volume: target.volumes[target.postgresVolumeKey].name },
      attempt_id: attempt, origin_revision: revision, starting_schema: 59, starting_versions: status.applied_versions,
      intended_schema: 62, study_invariants: study, backup, state: "pending", created_at: new Date().toISOString(),
      cut62_witness: witnessName, pre62_witness: preName };
    atomicJson(guardPath, guard);
    mkdirSync(witnessDirectory, { mode: 0o700 }); mkdirSync(preDirectory, { mode: 0o700 });
    const selection = readFileSync(selectionPath);
    const digestResult = await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "--entrypoint", "sh",
      "postgres-backup", "-ec", 'cd /backups; sha256sum -c "$1.sha256" >/dev/null; sha256sum "$1" | cut -d " " -f1', "sh", backup.filename]);
    const backupDigest = digestResult.stdout.trim(); assert.match(backupDigest, /^[0-9a-f]{64}$/);
    const binding = { version: 1, target: targetKey(target), attempt_id: attempt, backup_filename: backup.filename,
      backup_sha256: backupDigest, selection_sha256: createHash("sha256").update(selection).digest("hex") };
    writeOnce(join(witnessDirectory, "defense-selection.json"), selection);
    writeOnce(join(witnessDirectory, "binding.json"), JSON.stringify(binding));
    syncDirectory(witnessDirectory); syncDirectory(stateDirectory);
    guard.defense_binding = binding; atomicJson(guardPath, guard);
    const captured59 = JSON.parse((await maintenance("database/tooling/verify_postgres_cli_state.py", ["--pre62-capture59", `/cut62/${preName}`], preDirectory)).stdout);
    syncDirectory(preDirectory); syncDirectory(stateDirectory);
    guard.pre62_witness_id = captured59.witness_id; atomicJson(guardPath, guard);
    const check59 = async version => {
      const checked = JSON.parse((await maintenance("database/tooling/verify_postgres_cli_state.py", ["--pre62-verify59", `/cut62/${preName}`], preDirectory)).stdout);
      assert.equal(checked.witness_id, guard.pre62_witness_id); assert.equal(checked.observed_schema, version);
    };
    await check59(59);
    await maintenance("database/tooling/apply_postgres_migrations.py", ["--target-version", "61"]);
    await check59(61);
    await maintenance("database/tooling/verify_postgres_cli_state.py", ["--expected", JSON.stringify(study)]);
    guard.cut62_intermediate_verified = { schema: 61, pre62_witness: preName, pre62_witness_id: guard.pre62_witness_id,
      attempt_id: attempt, backup_filename: backup.filename, verified_at: new Date().toISOString() };
    guard.cut62_capture_started = true; atomicJson(guardPath, guard);
    const defenseArgs = ["--defense-selection", "/cut62/defense-selection.json", "--cut62-binding", "/cut62/binding.json"];
    const captured = JSON.parse((await maintenance("database/tooling/verify_postgres_cli_state.py",
      ["--cut62-capture", `/cut62/${witnessName}`, ...defenseArgs], witnessDirectory)).stdout);
    syncDirectory(witnessDirectory); syncDirectory(stateDirectory);
    guard.cut62_identity = { witness_id: captured.witness_id, attempt_id: attempt, backup_filename: backup.filename };
    atomicJson(guardPath, guard);
    const witnessPath = join(witnessDirectory, witnessName), prePath = join(preDirectory, preName);
    const originalWitness = readFileSync(witnessPath), originalPre = readFileSync(prePath);
    const lock = JSON.parse(readFileSync(join(stateDirectory, "maintenance.lock"), "utf8"));
    assert.equal(lock.pid, process.pid); assert(lock.token);
    const config = { target, expected_image: images.postgres, maintenance_image: images.migration,
      maintenance_revision: revision, approved_revision: revision, pgpass_path: join(environment.TEMPO_PG_TEST_SECRETS, "admin_pgpass"),
      root: stateDirectory, guard_path: guardPath, selection_path: join(witnessDirectory, "defense-selection.json"),
      binding_path: join(witnessDirectory, "binding.json"), witness_path: witnessPath, pre62_path: prePath,
      lock_pid: process.pid, lock_token: lock.token, trusted_daemon: true };
    const supervisorDiagnostics = [];
    const supervise = crash => new Promise((resolve, reject) => {
      const child = spawn(componentPython, [join(target.root, "database/tests/cli_isolation_supervisor.py")],
        { cwd: target.root, env: { ...process.env, ...childEnvironment, PYTHONPATH: `${target.root}:${join(target.root, "database")}`, TEMPO_TEST_INSTANCE: "disposable" },
          stdio: ["pipe", "pipe", "pipe"] });
      let stdout = "", stderr = "";
      child.stdout.on("data", data => { stdout += data; }); child.stderr.on("data", data => { stderr += data; });
      child.once("error", reject); child.once("close", code => {
        const errorLine = stderr.trim().split(/\r?\n/).findLast(line => /^(?:RuntimeError|AssertionError|FileNotFoundError|PermissionError|TimeoutExpired):/.test(line)) ?? "";
        const match = errorLine.match(/^(RuntimeError|AssertionError|FileNotFoundError|PermissionError|TimeoutExpired):\s*([A-Za-z0-9 .;,'-]{1,180})$/);
        const summary = match ? `${match[1]}: ${match[2]}` : errorLine.split(":", 1)[0] || "No safe supervisor diagnostic";
        supervisorDiagnostics.push({ fault: crash, code, summary });
        atomicJson(join(target.root, "test-results", "tempo-cli", target.project, "isolation-diagnostics.json"), supervisorDiagnostics);
        resolve({ code, stdout, stderr, summary });
      });
      child.stdin.end(JSON.stringify({ ...config, crash }));
    });
    if (environment.TEMPO_LIFECYCLE_ISOLATION_LATE_WRITER === "1") {
      const databaseId = (await compose(["ps", "-q", "postgres"])).stdout.trim();
      assert(databaseId, "The owned late-writer database must exist.");
      const insert = "INSERT INTO public.operation_receipts(operation_id,command_name,request_hash,state) VALUES ('cli-isolation-late-writer','synthetic','synthetic','pending')";
      await docker(["exec", "--user", "postgres", databaseId, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "tempo", "-c", insert]);
      const denied = await supervise(null);
      assert.notEqual(denied.code, 0); assert.match(denied.stderr, /changed|differs|witness/i);
      const heldGuard = JSON.parse(readFileSync(guardPath, "utf8"));
      assert.equal(heldGuard.state, "pending"); assert.equal(heldGuard.isolation.phase, "detached");
      assert.equal(heldGuard.attempt_id, attempt); assert.deepEqual(heldGuard.defense_binding, binding);
      const lateHandoffs = readdirSync(stateDirectory).filter(name => name.startsWith("handoff-"));
      assert.equal(lateHandoffs.length, 1);
      const peer = JSON.parse(readFileSync(join(stateDirectory, lateHandoffs[0], "peer.json"), "utf8"));
      assert.equal(peer.attempt_id, attempt); assert.equal(peer.network_id, heldGuard.isolation.identity.networkId);
      assert.equal(peer.admission_open, true); assert.equal(peer.blocked, true);
      assert.notEqual((await docker(["inspect", peer.peer_id], { allowFailure: true })).code, 0);
      assert.deepEqual(readFileSync(witnessPath), originalWitness); assert.deepEqual(readFileSync(prePath), originalPre);
      const detached = JSON.parse((await docker(["inspect", heldGuard.isolation.identity.containerId])).stdout)[0];
      assert.equal(Object.keys(detached.NetworkSettings.Networks).length, 0);
      const control = async statement => docker(["exec", "--user", "postgres", databaseId, "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres", "-c", statement]);
      const state = JSON.parse((await control("SELECT json_build_object('allowed',(SELECT datallowconn FROM pg_database WHERE datname='tempo'),'sessions',(SELECT count(*) FROM pg_stat_activity WHERE datname='tempo'),'prepared',(SELECT count(*) FROM pg_prepared_xacts WHERE database='tempo'))")).stdout);
      assert.deepEqual(state, { allowed: false, sessions: 0, prepared: 0 });
      await control("ALTER DATABASE tempo ALLOW_CONNECTIONS true");
      try {
        const retained = JSON.parse((await docker(["exec", "--user", "postgres", databaseId, "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "tempo", "-c",
          "SELECT json_build_object('schema',(SELECT max(version) FROM public.tempo_schema_migrations),'accepted',EXISTS(SELECT 1 FROM public.operation_receipts WHERE operation_id='cli-isolation-late-writer'))"])).stdout);
        assert.deepEqual(retained, { schema: 61, accepted: true }, "Late accepted work must survive and 062 must not run.");
      } finally { await control("ALTER DATABASE tempo ALLOW_CONNECTIONS false"); }
      assert.equal(JSON.parse((await control("SELECT to_json(datallowconn) FROM pg_database WHERE datname='tempo'")).stdout), false);
      console.log("PASS disposable late writer is preserved and blocks schema 62 before mutation");
      return { lateWriterHold: true };
    }
    const receiptPath = join(witnessDirectory, "verified-receipt.json"), completionPath = join(witnessDirectory, "verified-completion.json");
    let originalReceipt, receiptInode;
    for (const [fault, expectedCode] of [["after-commit", 1], ["after-receipt", 1], ["before-completion", 79],
      ["after-completion", 80], ["after-reattach", 81], ["after-guard", 82]]) {
      const priorHandoffs = new Set(readdirSync(stateDirectory).filter(name => name.startsWith("handoff-")));
      const result = await supervise(fault);
      assert.equal(result.code, expectedCode, `Disposable ${fault} must stop at its intended boundary.`);
      assert.deepEqual(readFileSync(witnessPath), originalWitness); assert.deepEqual(readFileSync(prePath), originalPre);
      const observedGuard = JSON.parse(readFileSync(guardPath, "utf8"));
      assert(observedGuard.isolation, `Disposable ${fault} failed before isolation: ${result.summary}`);
      assert.equal(observedGuard.attempt_id, attempt); assert.deepEqual(observedGuard.defense_binding, binding);
      assert.equal(observedGuard.cut62_identity.witness_id, captured.witness_id);
      assert.equal(observedGuard.pre62_witness_id, captured59.witness_id);
      const expectedPhase = ["after-commit", "after-receipt", "before-completion"].includes(fault) ? "detached"
        : fault === "after-completion" ? "verified" : "reattached";
      assert.equal(observedGuard.isolation.phase, expectedPhase);
      assert.equal(observedGuard.state, fault === "after-guard" ? "verified" : "pending");
      const handoffs = readdirSync(stateDirectory).filter(name => name.startsWith("handoff-") && !priorHandoffs.has(name));
      if (fault !== "after-guard") {
        assert.equal(handoffs.length, 1, `Disposable ${fault} needs one fresh pinned handoff.`);
        const handoff = join(stateDirectory, handoffs[0]);
        const ready = JSON.parse(readFileSync(join(handoff, "ready.json"), "utf8"));
        assert(existsSync(join(handoff, "committed.json")), `Disposable ${fault} failed before schema62: ${result.summary}`);
        const committed = JSON.parse(readFileSync(join(handoff, "committed.json"), "utf8"));
        const peer = JSON.parse(readFileSync(join(handoff, "peer.json"), "utf8"));
        assert.equal(peer.version, 1); assert.equal(peer.attempt_id, attempt);
        assert.equal(peer.network_id, observedGuard.isolation.identity.networkId); assert.equal(peer.image_id, images.postgres);
        assert.equal(peer.admission_open, true); assert.equal(peer.blocked, true);
        assert.match(peer.peer_id, /^[0-9a-f]{64}$/);
        assert.notEqual((await docker(["inspect", peer.peer_id], { allowFailure: true })).code, 0, "Owned peer must be removed.");
        assert.deepEqual(committed, { version: 1, nonce: ready.nonce, pid: ready.pid, attempt_id: attempt,
          cut62_witness_id: captured.witness_id, schema: 62 }, `Disposable ${fault} must reach schema 62 on the fenced connection.`);
      } else assert.equal(handoffs.length, 0);
      const hasReceipt = existsSync(receiptPath), hasCompletion = existsSync(completionPath);
      assert.equal(hasReceipt, fault !== "after-commit");
      assert.equal(hasCompletion, ["after-completion", "after-reattach", "after-guard"].includes(fault));
      if (hasReceipt && !originalReceipt) { originalReceipt = readFileSync(receiptPath); receiptInode = lstatSync(receiptPath).ino; }
      if (hasReceipt) { assert.deepEqual(readFileSync(receiptPath), originalReceipt); assert.equal(lstatSync(receiptPath).ino, receiptInode); }
      const identity = observedGuard.isolation.identity;
      const database = JSON.parse((await docker(["inspect", identity.containerId])).stdout)[0];
      const network = JSON.parse((await docker(["network", "inspect", identity.networkId])).stdout)[0];
      const attached = ["after-reattach", "after-guard"].includes(fault);
      assert.equal(Object.keys(database.NetworkSettings.Networks).length, attached ? 1 : 0);
      assert.equal(Boolean(network.Containers[identity.containerId]), attached);
      const query = "SELECT json_build_object('allowed',(SELECT datallowconn FROM pg_database WHERE datname='tempo'),'sessions',(SELECT count(*) FROM pg_stat_activity WHERE datname='tempo'),'prepared',(SELECT count(*) FROM pg_prepared_xacts WHERE database='tempo'))";
      const control = JSON.parse((await docker(["exec", "--user", "postgres", identity.containerId, "psql", "-X", "-A", "-t", "-U", "postgres", "-d", "postgres", "-c", query])).stdout);
      assert.deepEqual(control, { allowed: attached, sessions: 0, prepared: 0 });
      if (attached) {
        const version = (await docker(["exec", "--user", "postgres", identity.containerId, "psql", "-X", "-A", "-t", "-U", "postgres", "-d", "tempo", "-c", "SELECT max(version) FROM public.tempo_schema_migrations"])).stdout.trim();
        assert.equal(version, "62");
      }
      const checksum = await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "--entrypoint", "sh",
        "postgres-backup", "-ec", 'cd /backups; sha256sum -c "$1.sha256" >/dev/null; sha256sum "$1" | cut -d " " -f1', "sh", backup.filename]);
      assert.equal(checksum.stdout.trim(), backupDigest);
      if (fault === "after-completion") {
        const originalCompletion = readFileSync(completionPath), originalInode = lstatSync(completionPath).ino;
        for (const kind of ["tampered", "valid-substitution"]) {
          const held = `${completionPath}.held`;
          renameSync(completionPath, held);
          try {
            const changed = JSON.parse(originalCompletion);
            if (kind === "tampered") changed.signature = "0".repeat(64);
            else {
              changed.payload.attempt_id = "00000000-0000-0000-0000-000000000000";
              const secret = Buffer.from(JSON.parse(originalWitness).secret, "hex");
              changed.signature = createHmac("sha256", secret).update("tempo-disposable-completion-v1\0")
                .update(JSON.stringify(changed.payload)).digest("hex");
              const validate = spawnSync(componentPython, ["-c", "import hashlib,hmac,json,sys; x=json.load(sys.stdin); c=json.dumps(x['completion']['payload'],sort_keys=True,separators=(',',':')).encode(); assert hmac.compare_digest(x['completion']['signature'],hmac.new(bytes.fromhex(x['secret']),b'tempo-disposable-completion-v1\\0'+c,hashlib.sha256).hexdigest())"],
                { input: JSON.stringify({ secret: secret.toString("hex"), completion: changed }), encoding: "utf8" });
              assert.equal(validate.status, 0, "Substituted completion must have a valid independent HMAC.");
            }
            writeFileSync(completionPath, JSON.stringify(changed), { mode: 0o600, flag: "wx" });
            const denied = await supervise(null);
            assert.notEqual(denied.code, 0);
            assert.match(denied.stderr, /Original disposable completion differs/);
            assert.deepEqual(JSON.parse(readFileSync(guardPath, "utf8")), observedGuard);
            const stillDetached = JSON.parse((await docker(["inspect", identity.containerId])).stdout)[0];
            assert.equal(Object.keys(stillDetached.NetworkSettings.Networks).length, 0);
          } finally {
            rmSync(completionPath, { force: true }); renameSync(held, completionPath);
          }
          assert.deepEqual(readFileSync(completionPath), originalCompletion); assert.equal(lstatSync(completionPath).ino, originalInode);
        }
      }
    }
    const resumed = await supervise(null);
    assert.equal(resumed.code, 0, "Disposable original attempt must resume without recapture.");
    guard = JSON.parse(readFileSync(guardPath, "utf8"));
    assert.equal(guard.state, "verified"); assert.equal(guard.attempt_id, attempt);
    assert.deepEqual(readFileSync(witnessPath), originalWitness); assert.deepEqual(readFileSync(prePath), originalPre);
  } finally { release(); }
  const final = makeRuntime(target, { run, stateDirectory, revision, evidence, preparedImages });
  await final.inspectTarget(); await lifecycle("isolated_completed_start", { recreate: true }, final);
  return final;
}

export async function verifyTempoCliLifecycle({ project, environment, composeFiles, revision, measure = null }) {
  const root = process.cwd();
  const isolatedDefense = environment.TEMPO_LIFECYCLE_ISOLATED_DEFENSE === "1";
  const componentPython = environment.TEMPO_COMPONENT_PYTHON;
  if (environment.TEMPO_LIFECYCLE_ISOLATION_LATE_WRITER === "1" && !isolatedDefense)
    throw new Error("Late-writer proof requires the separate disposable Defense scenario.");
  if (isolatedDefense) {
    if (environment.TEMPO_LIFECYCLE_P0_59_ONLY !== "1" || !componentPython || !isAbsolute(componentPython))
      throw new Error("Disposable Defense proof requires schema59 mode and an explicit absolute TEMPO_COMPONENT_PYTHON.");
    const probe = spawnSync(componentPython, ["-c", "import psycopg; import database.tooling.verify_postgres_cli_state; import database.tests.check_postgres_cli_lifecycle; assert int(psycopg.__version__.split('.')[0]) == 3"],
      { cwd: root, env: { ...process.env, PYTHONPATH: `${root}:${join(root, 'database')}` }, encoding: "utf8" });
    if (probe.status !== 0) throw new Error("Disposable Defense proof requires the supplied Python with psycopg 3 and the fixture dependencies.");
  }
  composeFiles = releaseComposeFiles({ root, composeFiles });
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
  const measured = (label, category, action) => measure ? measure.detail(label, category, action) : action();
  const run = (command, args, options) => {
    commandLog.push({ command, args });
    const { label, category } = postgresCommandTiming(args);
    return measured(label, category, async () => {
      const result = await execute(command, args, options);
      measure?.processResult(result);
      return result;
    });
  };
  const makeRuntime = (runtimeTarget, options) => {
    const runtime = createRuntime(runtimeTarget, { ...options,
      redisReadinessOptions: measure ? { wait: measure.wait, poll: measure.poll } : {},
      readinessOptions: measure ? { wait: measure.wait, poll: measure.poll } : {} });
    const categories = { inspectTarget: "inspection", ensureImages: "build", stopApplications: "restart",
      ensureDatabase: "readiness", checkSchema: "migration", backup: "backup", migrate: "migration",
      startServices: "startup", verifyReady: "readiness", commitDeployment: "inspection" };
    for (const [method, category] of Object.entries(categories)) {
      const original = runtime[method];
      const label = method.replace(/[A-Z]/g, character => `_${character.toLowerCase()}`);
      runtime[method] = (...args) => measured(label, category, () => original(...args));
    }
    return runtime;
  };
  const lifecycle = (label, plan, runtime) => measured(label, "scenario", async () => {
    const release = acquireTargetLock(stateDirectory);
    try { return await executeLifecycle(plan, runtime); }
    finally { release(); }
  });
  const childProcess = (label, ...args) => {
    const action = () => {
      const result = spawnSync(...args);
      measure?.processResult(result);
      return result;
    };
    return measure ? measure.syncDetail(label, "scenario", action) : action();
  };
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
    assert.equal(productConfig.services["defense-engine"], undefined);
    assert.equal(productConfig.services["maia-worker"], undefined, "Retired Maia worker must not ship");
    console.log("PASS current product Compose persistent-volume and API/worker PostgreSQL, passfile/secret, Redis contracts (read-only configuration)");
  } finally { rmSync(productSecrets, { recursive: true, force: true }); }
  const composeArguments = ["compose", "--project-directory", root, "-p", childProject,
    ...composeFiles.flatMap(file => ["-f", file])];
  const compose = (args, options) => docker([...composeArguments, ...args], options);
  const config = JSON.parse((await compose(["--profile", "maintenance", "--profile", "games-feedback", "config", "--format", "json"])).stdout);
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
    if (name === "game-feedback-worker") {
      // The inactive optional service has the same build as the already-built
      // API. Reuse that real image only after proving build/source equality.
      const builds = [config.services.api.build, service.build].map(build => structuredClone(build));
      for (const build of builds) {
        if (build.labels?.["org.opencontainers.image.revision"] !== undefined) {
          assert.equal(build.labels["org.opencontainers.image.revision"], revision);
          delete build.labels["org.opencontainers.image.revision"];
          if (!Object.keys(build.labels).length) delete build.labels;
        }
      }
      assert.deepEqual(builds[0], builds[1], "Optional receipt image must have the exact API build");
      assert(images.api, "The actual API image must be inspected first");
      const apiImage = JSON.parse((await docker(["image", "inspect", images.api])).stdout)[0];
      assert.equal(apiImage.Config?.Labels?.["org.opencontainers.image.revision"], revision);
      images[name] = apiImage.Id;
      continue;
    }
    const tag = service.image ?? `${project}-${name}`;
    images[name] = JSON.parse((await docker(["image", "inspect", tag])).stdout)[0].Id;
  }
  // A distinct immutable schema-61 set is mandatory: a schema-62 maintenance
  // image cannot safely jump the populated historical fixture across this cut.
  const schema59Only = environment.TEMPO_LIFECYCLE_P0_59_ONLY === "1";
  const schema61Revision = schema59Only ? "5d879ffce599af7a2eaf9627e9e1c7ea82e9af5a" : "4aa0ad1b731402365c1b866a6ba7bed484d2b6ac";
  const imageFile = schema59Only ? environment.TEMPO_LIFECYCLE_SCHEMA59_IMAGES : environment.TEMPO_LIFECYCLE_SCHEMA61_IMAGES;
  if (!imageFile) throw new Error("Provide the task-owned historical image manifest before lifecycle mutation.");
  const schema61 = JSON.parse(readFileSync(imageFile, "utf8"));
  if (schema61.revision !== schema61Revision) throw new Error("Unexpected historical image revision.");
  const schema61Images = { ...images };
  for (const [name, service] of Object.entries(config.services)) {
    if (!service.build && name !== "game-feedback-worker" && name !== "migration") continue;
    const file = service.build?.dockerfile;
    const kind = name === "migration" || file === "Dockerfile.postgres-maintenance" ? "migration"
      : name === "game-feedback-worker" || file === "backend/Dockerfile" ? "api"
        : file === "Dockerfile.web" ? "web" : null;
    if (!kind || !schema61.images?.[kind]) throw new Error(`Missing schema-61 image for ${name}`);
    const inspected = JSON.parse((await docker(["image", "inspect", schema61.images[kind]])).stdout)[0];
    if (inspected.Config?.Labels?.["org.opencontainers.image.revision"] !== schema61Revision)
      throw new Error(`Schema-61 image revision mismatch for ${name}`);
    schema61Images[name] = inspected.Id;
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
    await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "migration", "database/tests/check_postgres_cli_lifecycle.py", ...(schema59Only ? ["--seed59", ...(isolatedDefense ? ["--isolation-defense"] : [])] : [])], { echo: true });
    const readHistory = async () => JSON.parse((await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "migration",
      "database/tests/check_postgres_cli_lifecycle.py", "--history"])).stdout);
    const expected = await readHistory();
    if (schema59Only) {
      const syntheticReference = JSON.parse((await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "migration",
        "database/tests/check_postgres_cli_lifecycle.py", "--defense-reference", ...(isolatedDefense ? ["--isolation-defense"] : [])])).stdout);
      assert.equal(syntheticReference.count, isolatedDefense ? 1 : 0);
      mkdirSync(stateDirectory, { recursive: true, mode: 0o700 });
      const selectionPath = join(stateDirectory, "synthetic-approved-selection.json");
      atomicJson(selectionPath, { version: 1, target: targetKey(target), cohort: "defense-no-activity",
        keys: isolatedDefense ? [["cli-isolation-card", "cli-isolation-candidate"]] : [], expected: syntheticReference });
      const oldOverlay = join(directory, "schema59-images.json");
      atomicJson(oldOverlay, { services: Object.fromEntries(Object.entries(schema61Images).map(([name, image]) => [name, { image }])) });
      await compose(["-f", oldOverlay, "up", "-d", "--no-build", "--no-deps", "--wait",
        "foreground-worker", "background-worker", "background-scheduler", "api", "web", "postgres-backup"], { echo: true });
      const preparedImages = { revision, images, configFingerprint: configurationFingerprint(config) };
      let final;
      if (isolatedDefense) {
        final = await exerciseIsolatedDefense({ target, run, compose, docker, makeRuntime, lifecycle, stateDirectory,
          revision, evidence, preparedImages, selectionPath, componentPython, environment, childEnvironment, images });
        if (final?.lateWriterHold) return;
      } else {
      let interrupted60 = false, interrupted61 = false, interruptedCapture = false, interrupted62 = false, captures = 0;
      const inject = async (command, args, options) => {
        if (args.includes("--cut62-verify") && !interrupted62) {
          interrupted62 = true; throw new Error("injected after schema62");
        }
        if (args.includes("--target-version") && !interrupted60) {
          interrupted60 = true;
          // Execute the real migrator with a fault at the start of 061. Migration
          // 060 commits normally; the 061 transaction rolls back.
          const script = `import os
from database.tooling import apply_postgres_migrations as m
original = m.migration_source
def source(path):
    sql = original(path)
    return "SELECT 1/0;\\n" + sql if path.name.startswith("061_") else sql
m.migration_source = source
m.apply_migrations(os.environ["TEMPO_POSTGRES_ADMIN_URL"], target_version=61)`;
          const scriptIndex = args.indexOf("database/tooling/apply_postgres_migrations.py");
          await assert.rejects(run(command, [...args.slice(0, scriptIndex), "-c", script], options), /DivisionByZero|22012/i);
          throw new Error("injected at schema60");
        }
        if (args.includes("--cut62-capture")) captures += 1;
        const result = await run(command, args, options);
        if (args.includes("--target-version") && !interrupted61) { interrupted61 = true; throw new Error("injected after schema61"); }
        if (args.includes("--cut62-check-captured") && !interruptedCapture) { interruptedCapture = true; throw new Error("injected after cut62 capture"); }
        return result;
      };
      const initial = makeRuntime(target, { run: inject, stateDirectory, revision, evidence, preparedImages, defenseSelection: selectionPath });
      await initial.inspectTarget();
      await assert.rejects(lifecycle("p0_interrupted_60", { recreate: true }, initial), /injected at schema60/);
      const originalGuard = JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8"));
      assert.equal(originalGuard.starting_schema, 59); assert.equal(originalGuard.intended_schema, 62);
      assert.equal(originalGuard.backup.verified, true);
      assert.equal(Number((await readLatestMigration(compose)).stdout.trim()), 60);
      const preWitness = join(stateDirectory, originalGuard.pre62_witness.replace(/\.json$/, ""), originalGuard.pre62_witness);
      const preBytes = readFileSync(preWitness);
      renameSync(preWitness, preWitness + ".held");
      try {
        const missing59 = makeRuntime(target, { run, stateDirectory, revision, evidence, preparedImages, retry: true });
        await missing59.inspectTarget();
        await assert.rejects(lifecycle("p0_missing_59_witness", { recreate: true }, missing59), /witness is missing/);
      } finally { renameSync(preWitness + ".held", preWitness); }
      writeFileSync(preWitness, Buffer.from("tampered"), { mode: 0o600 });
      try {
        const tampered59 = makeRuntime(target, { run, stateDirectory, revision, evidence, preparedImages, retry: true });
        await tampered59.inspectTarget();
        await assert.rejects(lifecycle("p0_tampered_59_witness", { recreate: true }, tampered59));
      } finally { writeFileSync(preWitness, preBytes, { mode: 0o600 }); }
      assert.equal(Number((await readLatestMigration(compose)).stdout.trim()), 60);
      const retry60 = makeRuntime(target, { run: inject, stateDirectory, revision, evidence, preparedImages, retry: true });
      await retry60.inspectTarget();
      await assert.rejects(lifecycle("p0_resume_60_interrupted_61", { recreate: true }, retry60), /injected after schema61/);
      const after60 = JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8"));
      assert.equal(after60.attempt_id, originalGuard.attempt_id);
      assert.deepEqual(after60.backup, originalGuard.backup);
      assert.deepEqual(readFileSync(preWitness), preBytes);
      assert.equal(Number((await readLatestMigration(compose)).stdout.trim()), 61);
      assert.equal(captures, 0);
      const resumed = makeRuntime(target, { run: inject, stateDirectory, revision, evidence, preparedImages, retry: true });
      await resumed.inspectTarget();
      await assert.rejects(lifecycle("p0_interrupted_capture", { recreate: true }, resumed), /injected after cut62 capture/);
      const captured = JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8"));
      assert.equal(captured.attempt_id, originalGuard.attempt_id);
      assert.deepEqual(captured.backup, originalGuard.backup);
      assert.equal(captures, 1);
      assert.equal(captured.cut62_intermediate_verified.schema, 61);
      const witness = join(stateDirectory, captured.cut62_witness.replace(/\.json$/, ""), captured.cut62_witness);
      const witnessBytes = readFileSync(witness);
      // Capture a second valid witness with the same retained state and a
      // different retired engine row; restore that row before any retry.
      const witnessDirectory = join(stateDirectory, captured.cut62_witness.replace(/\.json$/, ""));
      const alternate = join(witnessDirectory, "alternate.json");
      const updateRetired = async expression => compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "migration", "-c",
        `import os, psycopg\nwith psycopg.connect(os.environ['TEMPO_POSTGRES_ADMIN_URL']) as db:\n db.execute("UPDATE engine_scheduling_state SET automated_streak=${expression} WHERE id=1")`]);
      await updateRetired("(automated_streak+1)%4");
      let alternateId;
      try {
        alternateId = JSON.parse((await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "--volume", `${witnessDirectory}:/cut62`,
          "migration", "database/tooling/verify_postgres_cli_state.py", "--cut62-capture", "/cut62/alternate.json",
          "--defense-selection", "/cut62/defense-selection.json", "--cut62-binding", "/cut62/binding.json"])).stdout).witness_id;
      } finally { await updateRetired("(automated_streak+3)%4"); }
      assert(alternateId && alternateId !== captured.cut62_identity.witness_id);
      const alternateBytes = readFileSync(alternate);
      writeFileSync(witness, alternateBytes, { mode: 0o600 });
      try {
        const swapped61 = makeRuntime(target, { run, stateDirectory, revision, evidence, preparedImages, retry: true });
        await swapped61.inspectTarget();
        await assert.rejects(lifecycle("p0_alternate_witness_61", { recreate: true }, swapped61));
        assert.equal(Number((await readLatestMigration(compose)).stdout.trim()), 61);
      } finally { writeFileSync(witness, witnessBytes, { mode: 0o600 }); }
      renameSync(witness, witness + ".held");
      try {
        const missing = makeRuntime(target, { run, stateDirectory, revision, evidence, preparedImages, retry: true });
        await missing.inspectTarget();
        await assert.rejects(lifecycle("p0_missing_witness", { recreate: true }, missing), /witness is missing/);
      } finally { renameSync(witness + ".held", witness); }
      const retryStart = commandLog.length;
      const interrupt62 = makeRuntime(target, { run: inject, stateDirectory, revision, evidence, preparedImages, retry: true });
      await interrupt62.inspectTarget();
      await assert.rejects(lifecycle("p0_interrupted_62", { recreate: true }, interrupt62), /injected after schema62/);
      assert.equal(Number((await readLatestMigration(compose)).stdout.trim()), 62);
      writeFileSync(witness, alternateBytes, { mode: 0o600 });
      try {
        const swapped62 = makeRuntime(target, { run, stateDirectory, revision, evidence, preparedImages, retry: true });
        await swapped62.inspectTarget();
        await assert.rejects(lifecycle("p0_alternate_witness_62", { recreate: true }, swapped62), /witness identity differs/);
        assert.equal(JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8")).state, "pending");
      } finally { writeFileSync(witness, witnessBytes, { mode: 0o600 }); }
      final = makeRuntime(target, { run, stateDirectory, revision, evidence, preparedImages, retry: true });
      await final.inspectTarget(); await lifecycle("p0_resume_original", { recreate: true }, final);
      assert(!commandLog.slice(retryStart).some(call => call.args.includes("--cut62-capture")));
      assert.deepEqual(readFileSync(witness), witnessBytes);
      }
      assert.deepEqual(await readHistory(), expected, "schema59 original review, queue and receipt survive cutover");
      assert.equal(JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8")).state, "verified");
      assert.equal(Number((await readLatestMigration(compose)).stdout.trim()), 62);
      const request = async (path, options = {}) => {
        const response = await fetch(`${target.webUrl}/api/${path}`, options);
        if (response.status !== 202) { if (!response.ok) throw new Error(`${path}: HTTP ${response.status} ${await response.text()}`); return response.json(); }
        const pending = await response.json();
        for (let attempt = 0; attempt < 120; attempt++) {
          await new Promise(resolve => setTimeout(resolve, 250));
          const receipt = await fetch(`${target.webUrl}/api/operations/${encodeURIComponent(pending.operation_id)}`).then(r => r.json());
          if (receipt.state === "complete") return receipt.response;
          if (receipt.state === "failed") throw new Error(`operation failed: ${JSON.stringify(receipt.error)}`);
        }
        throw new Error("Study operation did not settle");
      };
      const settings = await request("settings");
      await request("settings", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...settings, new_cards_per_day: 100, study_new_per_day: 100 }) });
      const form = new FormData(); form.set("file", new Blob([studyDurabilityPgn], { type: "application/x-chess-pgn" }), "p0-synthetic.pgn");
      form.set("trained_color", "white"); form.set("initial_depth", "6");
      const imported = await request("imports/pgn", { method: "POST", body: form });
      let card;
      for (let attempt = 0; attempt < 120; attempt++) {
        try { const queue = await request("queue/today"); card = queue.cards.find(item => item.repertoire_id === imported.repertoire_id); if (card) break; } catch {}
        await new Promise(resolve => setTimeout(resolve, 500));
      }
      assert(card && card.queue_entry_id && card.moves?.length, "A real imported Study card must be issued");
      assert(new Chess(card.start_fen).move(card.moves[0]), "Issued first move must be legal");
      const post = (path, payload, id) => request(path, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": id }, body: JSON.stringify(payload) });
      await post(`cards/${card.id}/teaching`, { revision: card.revision, ply: 0 }, "p0-synthetic-teaching");
      const saved = await post(`cards/${card.id}/review`, { outcome: "correct", queue_entry_id: card.queue_entry_id,
        expected_revision: card.revision, attempt_id: "p0-synthetic-review-attempt" }, "p0-synthetic-review");
      assert(saved, "Review must commit");
      let next;
      for (let attempt = 0; attempt < 120; attempt++) {
        try { const queue = await request("queue/today"); next = queue.cards.find(item => item.queue_entry_id !== card.queue_entry_id); if (next) break; } catch {}
        await new Promise(resolve => setTimeout(resolve, 500));
      }
      assert(next?.moves?.length && new Chess(next.start_fen).move(next.moves[0]), "Saved review must lead to another legal playable card");
      const receiptBeforeStop = await request("operations/p0-synthetic-review");
      assert.equal(receiptBeforeStop.state, "complete");
      const cli = (label, command, ...args) => childProcess(label, process.execPath,
        ["scripts/tempo-cli.mjs", command, ...args, "--config", registration],
        { encoding: "utf8", env: { ...childEnvironment, TEMPO_CLI_STATE_DIR: stateRoot }, timeout: 180_000 });
      const stopped = cli("p0_cli_stop", "stop");
      assert.equal(stopped.status, 0, stopped.stdout + stopped.stderr);
      assert(!(await final.runningServices()).some(name => ["postgres", "redis", "api", "foreground-worker", "background-worker"].includes(name)));
      const started = cli("p0_cli_cold_start", "start", "--no-open", "--no-ci");
      assert.equal(started.status, 0, started.stdout + started.stderr);
      assert.deepEqual(await request("operations/p0-synthetic-review"), receiptBeforeStop, "cold start preserves the committed review receipt");
      const afterStart = await request("queue/today");
      const playable = afterStart.cards.find(item => item.moves?.length && item.start_fen);
      assert(playable && new Chess(playable.start_fen).move(playable.moves[0]), "cold start returns an authoritative legal playable card");
      assert.equal(Number((await readLatestMigration(compose)).stdout.trim()), 62);
      console.log("PASS schema59 guarded cutover saved a review, reached a legal next card, and survived CLI stop/cold start");
      return;
    }
    const readNormalization = async () => JSON.parse((await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "migration",
      "database/tests/check_postgres_cli_lifecycle.py", "--normalization"])).stdout);
    const legacyTacticalRow = await readNormalization();
    assert.equal(legacyTacticalRow.card_bucket, "tactics");
    assert.equal(legacyTacticalRow.content_type, "tactics");
    assert.equal(legacyTacticalRow.repertoire_id, "__game_tactics__", "fixture satisfies migration 025's predicate");
    const preparedImages = { revision, images, configFingerprint: configurationFingerprint(config) };
    const prepared61 = { revision: schema61Revision, images: schema61Images, configFingerprint: configurationFingerprint(config) };
    // Commit the real schema-16 upgrade, then inject an unexpected historical
    // mutation before the genuine verifier reads it. The current ledger must
    // not let the next process skip its original H0 obligation.
    const mutatingRun = async (command, args, options) => {
      const result = await run(command, args, options);
      if (args.includes("database/tooling/apply_postgres_migrations.py") && !args.includes("--check"))
        await markHistoryIncorrect(compose);
      return result;
    };
    const runtime = makeRuntime(target, { run: mutatingRun, stateDirectory, revision: schema61Revision,
      evidence: { commit: schema61Revision, disposable_runner: childProject }, preparedImages: prepared61 });
    await runtime.inspectTarget();
    await assert.rejects(lifecycle("history_mutation_rejected", { recreate: true }, runtime), /Original migration history verification failed/);
    const originalGuard = JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8"));
    assert.equal(originalGuard.state, "pending");
    const failedReceipt = await readLatestMigration(compose);
    assert.equal(Number(failedReceipt.stdout.trim()), 61);
    const retryRuntime = () => makeRuntime(target, { run, stateDirectory, revision: schema61Revision,
      evidence: { commit: schema61Revision, disposable_runner: childProject }, preparedImages: prepared61, retry: true });
    const failedRetry = retryRuntime(); await failedRetry.inspectTarget();
    const retryStart = commandLog.length;
    await assert.rejects(lifecycle("history_retry_rejected", { recreate: false }, failedRetry), /Original migration history verification failed/);
    assert(!commandLog.slice(retryStart).some(call => call.args.includes("database/tooling/verify_postgres_cli_state.py") && !call.args.includes("--expected")));
    assert(!commandLog.slice(retryStart).some(call => call.args.includes("database/tooling/apply_postgres_migrations.py") && !call.args.includes("--check")));
    assert.deepEqual(JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8")).study_invariants, originalGuard.study_invariants);
    assert(!(await failedRetry.runningServices()).includes("api"));
    await markHistoryCorrect(compose);
    const repairedRetry = retryRuntime(); await repairedRetry.inspectTarget();
    await lifecycle("history_repaired_retry", { recreate: false }, repairedRetry);
    assert.equal(JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8")).state, "verified");
    let historicalRecord = JSON.parse(readFileSync(join(stateDirectory, "deployment.json"), "utf8"));
    assert.equal(historicalRecord.schema, 61);
    assert.equal(historicalRecord.backup.verified, true);
    assert.equal(JSON.parse(readFileSync(join(stateDirectory, "operation.json"), "utf8")).phase, "ready");
    assert.deepEqual(await readNormalization(), { ...legacyTacticalRow, card_bucket: "tactic", content_type: "tactic" },
      "migration 025 normalizes the same card/queue row and preserves its historical identity");
    assert.deepEqual(await readHistory(), expected, "upgrade preserves original study history while workers may add unrelated receipts");

    // Preserve the duplicate-DDL failure proof on the schema-61 image. The
    // schema-62 guard intentionally rejects a direct 39-to-62 crossing.
    await repairedRetry.stopApplications();
    await compose(["exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo", "-c",
      "CREATE TABLE cli_lifecycle_ledger_backup (LIKE tempo_schema_migrations INCLUDING ALL); INSERT INTO cli_lifecycle_ledger_backup SELECT * FROM tempo_schema_migrations WHERE version >= 40"]);
    await forgetMigrationSuffix(repairedRetry);
    const rejected61 = makeRuntime(target, { run, stateDirectory, revision: schema61Revision,
      evidence: { commit: schema61Revision, disposable_runner: childProject },
      preparedImages: prepared61, previous: historicalRecord });
    await rejected61.inspectTarget();
    await assert.rejects(lifecycle("historical_migration_rejected", { recreate: true }, rejected61), /already exists/);
    assert(!(await rejected61.runningServices()).some(name => ["api", "foreground-worker", "background-worker", "web"].includes(name)));
    assert.deepEqual(await readHistory(), expected, "rejected historical DDL preserves original study history");
    // The only mutation used to create this fault was the disposable ledger
    // suffix. Restore its exact known versions, then verify the original guard.
    await compose(["exec", "-T", "postgres", "psql", "-U", "postgres", "-d", "tempo", "-c",
      "INSERT INTO tempo_schema_migrations SELECT * FROM cli_lifecycle_ledger_backup; DROP TABLE cli_lifecycle_ledger_backup"]);
    const recovered61 = retryRuntime(); await recovered61.inspectTarget();
    await lifecycle("historical_ledger_recovered", { recreate: false }, recovered61);
    historicalRecord = JSON.parse(readFileSync(join(stateDirectory, "deployment.json"), "utf8"));
    assert.equal(historicalRecord.schema, 61);
    assert.deepEqual(await readHistory(), expected);

    // Cross the destructive cut with an explicit empty disposable selection.
    const emptyReference = JSON.parse((await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "migration",
      "database/tests/check_postgres_cli_lifecycle.py", "--defense-reference"])).stdout);
    assert.equal(emptyReference.count, 0);
    const emptySelectionPath = join(stateDirectory, "synthetic-approved-selection.json");
    atomicJson(emptySelectionPath, { version: 1, target: targetKey(target), cohort: "defense-no-activity", keys: [], expected: emptyReference });
    // Interrupt after capture, test a missing witness, then resume once.
    let interruptedCapture = false;
    const cutRun = async (command, args, options) => {
      const result = await run(command, args, options);
      if (args.includes("--cut62-check-captured") && !interruptedCapture) {
        interruptedCapture = true; throw new Error("injected post-capture interruption");
      }
      return result;
    };
    const cutRuntime = makeRuntime(target, { run: cutRun, stateDirectory, revision, evidence, preparedImages, previous: historicalRecord, defenseSelection: emptySelectionPath });
    await cutRuntime.inspectTarget();
    await assert.rejects(lifecycle("cut62_interrupted_capture", { recreate: true }, cutRuntime), /injected post-capture interruption/);
    const cutGuard = JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8"));
    assert.equal(cutGuard.state, "pending");
    assert.equal(cutGuard.starting_schema, 61); assert.equal(cutGuard.intended_schema, 62);
    assert.equal(cutGuard.backup.verified, true);
    const witness = join(stateDirectory, cutGuard.cut62_witness.replace(/\.json$/, ""), cutGuard.cut62_witness);
    assert(existsSync(witness));
    const witnessBytes = readFileSync(witness);
    renameSync(witness, witness + ".held");
    try {
      const missing = makeRuntime(target, { run, stateDirectory, revision, evidence, preparedImages, previous: historicalRecord, retry: true });
      await missing.inspectTarget();
      await assert.rejects(lifecycle("cut62_missing_witness", { recreate: true }, missing), /witness is missing/);
    } finally { renameSync(witness + ".held", witness); }
    const cutRetryStart = commandLog.length;
    const cutRetry = makeRuntime(target, { run, stateDirectory, revision, evidence, preparedImages, previous: historicalRecord, retry: true });
    await cutRetry.inspectTarget();
    await lifecycle("cut62_resume_original_witness", { recreate: true }, cutRetry);
    assert(!commandLog.slice(cutRetryStart).some(call => call.args.includes("--cut62-capture")));
    assert.deepEqual(readFileSync(witness), witnessBytes);
    assert.equal(JSON.parse(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8")).state, "verified");
    const record = JSON.parse(readFileSync(join(stateDirectory, "deployment.json"), "utf8"));
    assert.equal(record.schema, 62); assert.equal(record.backup.verified, true);
    assert.equal(target.webUrl, `http://127.0.0.1:${webPort}`);
    assert.deepEqual(await readHistory(), expected, "cutover retains the historical Study origin");

    // Exercise the registered Node operator only after the real upgrade/ready
    // boundary. The fixed orphan exists solely in this already owned lifetime;
    // its retained origin and receipt are left for the normal whole-stack drop.
    const inspectAbandonment = async (mode = "--abandon-inspection") => JSON.parse((await compose([
      "--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "migration",
      "database/tests/check_postgres_cli_lifecycle.py", mode,
    ])).stdout);
    const abandonBefore = await inspectAbandonment("--seed-abandon-orphan");
    const origin = JSON.parse(abandonBefore.origin_bytes);
    assert.notEqual(origin.queue_entry_id, 13624);
    assert.equal(origin.card_id, "cli-synthetic-abandon-orphan");
    assert.deepEqual({ ...origin, queue_entry_id: undefined, start_fen: undefined, moves_json: undefined }, {
      queue_entry_id: undefined, card_id: "cli-synthetic-abandon-orphan", revision: 1,
      queue_date: "2000-01-02", cycle: 0, admission_kind: null, admission_repertoire_id: null,
      attempt_failed: 1, last_status: "buried", review_result_json: null, legacy: 0,
      start_fen: undefined, moves_json: undefined, trained_color: null, content_type: "opening",
      issued_as_head: 1, issued_at: null, issuance_sequence: null,
    });
    assert.equal(origin.start_fen, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1");
    assert.deepEqual(JSON.parse(origin.moves_json), ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "d2d3", "f8c5", "c2c3"]);
    assert(Object.values(abandonBefore.orphan_links).every(count => count === 0), "synthetic origin has no current card, membership, queue or accepted attempt/snapshot");
    assert.equal(abandonBefore.abandonment_receipt_count, 0);
    assert.deepEqual(abandonBefore.abandonments, []);
    assert.deepEqual(abandonBefore.grades, []);
    const abandonRequest = { entry: String(origin.queue_entry_id), card: origin.card_id, revision: String(origin.revision),
      "operation-id": "cli-synthetic-abandon-operation", actor: "fixture-user",
      reason: "fixture user explicitly discards this unfinished exercise; no unsaved work is known" };
    const abandonCli = (label, request, { apply = false, configPath = registration } = {}) => childProcess(label,
      process.execPath, ["scripts/position-study/abandon-queue-attempt.mjs", "--config", configPath,
        ...Object.entries(request).flatMap(([key, value]) => [`--${key}`, value]), ...(apply ? ["--apply"] : [])], {
        encoding: "utf8", env: { ...childEnvironment, TEMPO_CLI_STATE_DIR: stateRoot }, timeout: 180_000,
      });
    const abandonSuccess = result => {
      assert.ifError(result.error);
      assert.equal(result.signal, null);
      assert.equal(result.status, 0, result.stdout + result.stderr);
      return JSON.parse(result.stdout);
    };
    const abandonRefusal = (result, code) => {
      assert.ifError(result.error);
      assert.equal(result.signal, null);
      assert.equal(result.status, 1, result.stdout + result.stderr);
      assert.match(result.stderr, code);
    };
    const abandonPlan = abandonSuccess(abandonCli("cli_abandon_orphan_plan", abandonRequest));
    assert.deepEqual(abandonPlan, { plan: true, ...abandonBefore.identity, origin_sha256: abandonPlan.origin_sha256 });
    assert.match(abandonPlan.origin_sha256, /^[0-9a-f]{64}$/);
    assert.deepEqual(await inspectAbandonment(), abandonBefore, "default plan writes no receipt or Study state");
    const abandonApply = { ...abandonRequest, "expected-origin-sha256": abandonPlan.origin_sha256 };
    abandonRefusal(abandonCli("cli_abandon_orphan_stale_hash", {
      ...abandonApply, "expected-origin-sha256": abandonPlan.origin_sha256 === "0".repeat(64) ? "1".repeat(64) : "0".repeat(64),
    }, { apply: true }), /queue abandonment held: abandonment_origin_changed/);
    abandonRefusal(abandonCli("cli_abandon_orphan_wrong_revision", {
      ...abandonApply, revision: String(origin.revision + 1),
    }, { apply: true }), /queue abandonment held: abandonment_origin_missing_or_ambiguous/);
    // Same registered data identity/state directory, deliberately incorrect port
    // registration: target inspection must refuse before reaching the worker.
    const wrongRegistration = join(directory, "abandon-wrong-registration.json");
    assert(target.ports.length > 0);
    atomicJson(wrongRegistration, { ...target, ports: [] });
    abandonRefusal(abandonCli("cli_abandon_orphan_wrong_target", abandonApply, {
      apply: true, configPath: wrongRegistration,
    }), /Compose ports differ from the registered Tempo target/);
    assert.deepEqual(await inspectAbandonment(), abandonBefore, "all refused applications preserve every origin and Study history");
    assert.deepEqual(abandonSuccess(abandonCli("cli_abandon_orphan_apply", abandonApply, { apply: true })), {
      abandoned: true, idempotent: false, operation_id: abandonRequest["operation-id"], origin_sha256: abandonPlan.origin_sha256,
    });
    const abandonAfter = await inspectAbandonment();
    assert.equal(abandonAfter.abandonment_receipt_count, 1);
    assert.equal(abandonAfter.abandonments.length, 1);
    const abandonReceipt = JSON.parse(abandonAfter.abandonments[0]);
    assert.deepEqual({ ...abandonReceipt, request_sha256: undefined, abandoned_at: undefined }, {
      ...abandonBefore.identity, operation_id: abandonRequest["operation-id"], origin_sha256: abandonPlan.origin_sha256,
      actor: abandonRequest.actor, reason: abandonRequest.reason, request_sha256: undefined, abandoned_at: undefined,
    });
    assert.match(abandonReceipt.request_sha256, /^[0-9a-f]{64}$/);
    assert(Number.isFinite(Date.parse(abandonReceipt.abandoned_at)));
    assert.deepEqual({ ...abandonAfter, abandonments: [], abandonment_receipt_count: 0 }, abandonBefore,
      "one durable abandonment preserves raw origins, failed marker, FSRS, reviews, snapshots and zero grades");
    assert.deepEqual(abandonSuccess(abandonCli("cli_abandon_orphan_retry", abandonApply, { apply: true })), {
      abandoned: true, idempotent: true, operation_id: abandonRequest["operation-id"], origin_sha256: abandonPlan.origin_sha256,
    });
    abandonRefusal(abandonCli("cli_abandon_orphan_changed_payload", {
      ...abandonApply, reason: "different abandonment reason under the same operation identity",
    }, { apply: true }), /queue abandonment held: abandonment_receipt_conflict/);
    assert.deepEqual(await inspectAbandonment(), abandonAfter, "exact retry and changed-payload refusal preserve the receipt bytes and every original history row");
    assert.deepEqual(await readHistory(), expected, "registered abandonment does not fabricate a completed review");
    console.log("PASS test_tempo_cli_abandon_orphan_exact_plan_apply_retry_preserves_history");

    const repeat = makeRuntime(target, { run, stateDirectory, revision, evidence, previous: record });
    await repeat.inspectTarget();
    const repeatStart = commandLog.length;
    await lifecycle("compatible_repeat", { recreate: false }, repeat);
    assert(!commandLog.slice(repeatStart).some(call => call.args.includes("build") || call.args.includes("stop")
      || (call.args.includes("database/tooling/apply_postgres_migrations.py") && !call.args.includes("--check"))));
    assert(commandLog.slice(repeatStart).filter(call => call.args.includes("up") && call.args.includes("postgres"))
      .every(call => call.args.includes("--no-recreate")));

    await verifyPersistedRedisReadiness({ target, runtime: repeat, run, compose, docker, directory, revision,
      images, readHistory, expectedHistory: expected, expectedSchema: record.schema, commandLog, measure });
    await lifecycle("after_redis_repeat", { recreate: false }, repeat);

    // Exercise the actual installed-command entry point against PostgreSQL.
    // This checkout is a task branch, so start must explicitly retain the
    // already recorded disposable deployment rather than fetching product main.
    const fallbackReceiptBytes = readFileSync(join(stateDirectory, "deployment.json"), "utf8");
    const child = childProcess("cli_fallback", process.execPath, ["scripts/tempo-cli.mjs", "start", "--no-open", "--config", registration], {
      encoding: "utf8", env: { ...childEnvironment, TEMPO_CLI_STATE_DIR: stateRoot }, timeout: 180_000,
    });
    assert.equal(child.status, 0, child.stdout + child.stderr);
    assert(child.stdout.includes("previous verified version ready, update deferred"));
    assert(child.stdout.includes("The update has not been applied."));
    assert.equal(readFileSync(join(stateDirectory, "deployment.json"), "utf8"), fallbackReceiptBytes);
    assert.equal(JSON.parse(readFileSync(join(stateDirectory, "operation.json"), "utf8")).phase, "ready_previous_version");
    output.push(child.stdout);
    console.log("PASS test_tempo_cli_postgres_fallback_reports_previous_version_ready_without_applying_update");

    // Reconstruct the pre-commit recovery boundary on this already migrated
    // fixture: an old schema receipt, verified H0, unchanged dependency images,
    // and real candidate workers with different Compose configuration hashes.
    // Kill a separate lifecycle process after its first application up returns;
    // its catch handler cannot quiesce those workers or publish a new receipt.
    const receiptPath = join(stateDirectory, "deployment.json");
    const committedReceipt = readFileSync(receiptPath, "utf8");
    const historicalReceipt = { ...record, revision: "0".repeat(40), schema: record.schema - 1,
      evidence: { commit: "0".repeat(40), disposable_runner: childProject } };
    atomicJson(receiptPath, historicalReceipt);
    const historicalReceiptBytes = readFileSync(receiptPath, "utf8");
    const guardBytes = readFileSync(join(stateDirectory, "migration-guard.json"), "utf8");
    const candidateOverride = join(directory, "uncommitted-applications.json");
    atomicJson(candidateOverride, { services: Object.fromEntries(["api", "foreground-worker", "background-worker"]
      .map(name => [name, { environment: { TEMPO_UNCOMMITTED_CANDIDATE: "interrupted-rollout" } }])) });
    const candidateConfig = JSON.parse((await docker([...composeArguments, "-f", candidateOverride,
      "--profile", "maintenance", "--profile", "games-feedback", "config", "--format", "json"])).stdout);
    const preparedCandidate = join(directory, "uncommitted-prepared-images.json");
    atomicJson(preparedCandidate, { ...preparedImages, configFingerprint: configurationFingerprint(candidateConfig) });
    const interruptedLifecycle = join(directory, "interrupted-lifecycle.mjs");
    writeFileSync(interruptedLifecycle, `
import { readFileSync } from "node:fs";
import { commandExecutor } from ${JSON.stringify(join(root, "scripts/tempo-deployment.mjs"))};
import { createRuntime, executeLifecycle } from ${JSON.stringify(join(root, "scripts/tempo-runtime.mjs"))};
const [registration, stateDirectory, override, prepared] = process.argv.slice(2);
const target = JSON.parse(readFileSync(registration, "utf8"));
target.composeFiles.push(override);
const preparedImages = JSON.parse(readFileSync(prepared, "utf8"));
const execute = commandExecutor({ root: target.root, environment: process.env });
const run = async (command, args, options) => {
  const result = await execute(command, args, options);
  if (args.includes("up") && args.includes("foreground-worker")) process.kill(process.pid, "SIGKILL");
  return result;
};
const runtime = createRuntime(target, { run, stateDirectory, revision: preparedImages.revision,
  evidence: { commit: preparedImages.revision }, preparedImages });
await runtime.inspectTarget();
await executeLifecycle({ recreate: false }, runtime);
`, { mode: 0o600 });
    const interruptedChild = childProcess("interrupted_rollout", process.execPath, [interruptedLifecycle, registration, stateDirectory, candidateOverride, preparedCandidate], {
      encoding: "utf8", env: childEnvironment, timeout: 180_000,
    });
    output.push(redact(interruptedChild.stdout + interruptedChild.stderr, secretValues));
    assert.equal(interruptedChild.signal, "SIGKILL", redact(interruptedChild.stdout + interruptedChild.stderr, secretValues));
    assert.equal(JSON.parse(readFileSync(join(stateDirectory, "operation.json"), "utf8")).phase, "starting_services");
    const uncommittedRunning = await repeat.runningServices();
    assert(["api", "foreground-worker", "background-worker"].every(name => uncommittedRunning.includes(name)));
    assert(!uncommittedRunning.includes("web"), "interruption occurs before the second application startup group");
    const recovery = makeRuntime(target, { run, stateDirectory, revision: historicalReceipt.revision, previous: historicalReceipt, fallback: true });
    await recovery.inspectTarget();
    assert.deepEqual(await recovery.ensureImages(), { dependenciesMayChange: false });
    assert.equal(await recovery.recordedApplicationsMatch(), false, "actual candidate config hashes differ from the saved receipt");
    const failedFallback = childProcess("cli_fallback", process.execPath, ["scripts/tempo-cli.mjs", "start", "--no-open", "--config", registration], {
      encoding: "utf8", env: { ...childEnvironment, TEMPO_CLI_STATE_DIR: stateRoot }, timeout: 180_000,
    });
    output.push(redact(failedFallback.stdout + failedFallback.stderr, secretValues));
    assert.notEqual(failedFallback.status, 0);
    assert.match(failedFallback.stderr, /previous deployment is incompatible with the current database/);
    assert(failedFallback.stdout.includes("stopping application services")
      && failedFallback.stdout.indexOf("stopping application services") < failedFallback.stdout.indexOf("checking database"));
    assert(!failedFallback.stdout.includes("starting services"));
    const recoveredRunning = await recovery.runningServices();
    assert(![...applicationServices, "postgres-backup"].some(name => recoveredRunning.includes(name)));
    assert(["postgres", "redis"].every(name => recoveredRunning.includes(name)));
    assert.equal(readFileSync(receiptPath, "utf8"), historicalReceiptBytes);
    assert.equal(readFileSync(join(stateDirectory, "migration-guard.json"), "utf8"), guardBytes);
    const recoveryFailure = JSON.parse(readFileSync(join(stateDirectory, "operation.json"), "utf8"));
    assert.equal(recoveryFailure.phase, "failed");
    assert.match(recoveryFailure.failure, /incompatible/);
    assert(readdirSync(stateDirectory).includes(`failure-${recoveryFailure.id}.log`));
    assert.deepEqual(await readHistory(), expected);
    assert.equal(Number((await readLatestMigration(compose)).stdout.trim()), record.schema);
    console.log("PASS Tempo CLI interrupted uncommitted application rollout quiesces writers before incompatible fallback without database rollback");
    // Restore only the test's legitimate current receipt; fix forward restarts
    // the compatible application without touching PostgreSQL or migration H0.
    writeFileSync(receiptPath, committedReceipt, { mode: 0o600 });
    await lifecycle("fix_forward", { recreate: false }, repeat);

    // Simulate uncommitted candidate dependencies on these disposable volumes:
    // Redis has a different command/configuration, and PostgreSQL is absent.
    // The recorded deployment must restore its containers without data rollback.
    await repeat.stopApplications();
    const failedCandidateOverride = join(directory, "uncommitted-dependencies.json");
    atomicJson(failedCandidateOverride, { services: { redis: { command: ["redis-server", "--appendonly", "yes", "--appendfsync", "always"] } } });
    await docker([...composeArguments, "-f", failedCandidateOverride, "up", "-d", "--no-build", "--no-deps", "--force-recreate", "--wait", "redis"]);
    await compose(["rm", "-s", "-f", "postgres"]);
    const correctedFallback = makeRuntime(target, { run, stateDirectory, revision, previous: record, fallback: true });
    await correctedFallback.inspectTarget();
    const correctionStart = commandLog.length;
    await lifecycle("dependency_fallback", { recreate: false }, correctedFallback);
    const correctionCalls = commandLog.slice(correctionStart);
    const correctionShutdown = correctionCalls.findIndex(call => call.args.includes("stop") && call.args.includes("foreground-worker"));
    const correctionStartup = correctionCalls.findIndex(call => call.args.includes("up") && call.args.includes("postgres"));
    assert(correctionShutdown >= 0 && correctionStartup > correctionShutdown);
    assert(correctionCalls[correctionStartup].args.includes("--force-recreate"));
    assert.deepEqual((await correctedFallback.ensureImages()), { dependenciesMayChange: false }, "restored dependencies match saved immutable image/config identities");
    assert.deepEqual(await readHistory(), expected, "dependency correction preserves authoritative data");

    const beforeRestart = (await repeat.compose(["ps", "-q", "api"])).stdout.trim();
    const restartStart = commandLog.length;
    await lifecycle("explicit_restart", { recreate: true }, repeat);
    const restartCalls = commandLog.slice(restartStart);
    const writerShutdown = restartCalls.findIndex(call => call.args.includes("stop") && call.args.includes("foreground-worker"));
    const dependencyStartup = restartCalls.findIndex(call => call.args.includes("up") && call.args.includes("postgres"));
    assert(writerShutdown >= 0 && dependencyStartup > writerShutdown, "real restart stops writers before dependency changes");
    const afterRestart = (await repeat.compose(["ps", "-q", "api"])).stdout.trim();
    assert.notEqual(beforeRestart, afterRestart, "restart recreates the real API container");
    assert.deepEqual(await readHistory(), expected, "restart preserves original study history");
    console.log("PASS Tempo CLI historical and schema-62 guarded migration lifecycle preserves retained history and verified backups");

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
