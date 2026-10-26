import assert from "node:assert/strict";
import { chmodSync, existsSync, linkSync, mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { main } from "../../scripts/tempo-cli.mjs";
import { targetKey } from "../../scripts/tempo-deployment.mjs";
import { planLauncherFreshStart } from "../../scripts/tempo-cutover-plan.mjs";
import { createDefaultStudyLauncher, installedStudySettings } from "../../scripts/tempo-study-assembly.mjs";
import { runRegisteredStudyCommand } from "../../scripts/tempo-study-launcher.mjs";

// Protocol fixtures model observed responses through injected capabilities.
// They never inspect a daemon, run Docker, or qualify installed admission.
function fixture(command = "start") {
  const input = JSON.parse(readFileSync(new URL("../../scripts/tests/fixtures/tempo-cutover-plan/publication-input.json", import.meta.url), "utf8"));
  const registration = input.proposed.registration, receipt = input.proposed.receipt;
  const lockIdentity = targetKey(input.current.registration);
  registration.installedStudy = { version: 1, originalRegistrationPath: "/fixture/private/original.json",
    stateDirectory: "/fixture/private/original-state", lockIdentity, receiptPath: "/fixture/private/deployment.json",
    admissionPath: "/fixture/private/admission.json", persistentIntentPath: "/fixture/private/intent.json",
    persistentReceiptPath: "/fixture/private/persistent.json", runtime: { version: 1,
      postgres: { volumeKey: registration.postgresVolumeKey, volumeName: registration.volumes[registration.postgresVolumeKey].name,
        mountDestination: "/var/lib/postgresql", dataDirectory: "/var/lib/postgresql/18/docker", major: 18, systemIdentifier: "fixture-system" },
      credentialDigests: { studyTokenSha256: "a".repeat(64), runtimePassfileSha256: "b".repeat(64) } } };
  const options = { command, configPath: "/fixture/private/config.json", flags: new Set(), services: [] };
  const configuration = { name: registration.project,
    services: { learning: { image: receipt.images.learning }, postgres: { image: receipt.images.postgres },
      "study-web": { image: receipt.images["study-web"], ports: [{ host_ip: "127.0.0.1", published: "3000", target: 80 }] } },
    volumes: structuredClone(registration.volumes) };
  const records = new Map([[options.configPath, registration], [registration.installedStudy.receiptPath, receipt],
    [registration.installedStudy.originalRegistrationPath, input.current.registration],
    [registration.installedStudy.admissionPath, { plan: "fixture-public-plan", evidence: {}, facts: {} }],
    [registration.installedStudy.persistentIntentPath, { kind: "fixture-existing-persistent-intent" }],
    [registration.installedStudy.persistentReceiptPath, { kind: "fixture-existing-persistent-receipt" }]]);
  const events = [], messages = [], controls = {};
  let lockHeld = false;
  const dependencies = {
    readPrivateJson(path) { events.push(["read", path]); return structuredClone(records.get(path)); },
    async run(commandName, args) {
      events.push(["run", commandName, [...args], lockHeld]);
      if (args.includes("config")) return { code: 0, stdout: JSON.stringify(configuration), stderr: "" };
      return { code: 0, stdout: "", stderr: "" };
    },
    async withLock(identity, callback) {
      events.push(["lock", identity]); assert.equal(identity.lockIdentity, lockIdentity);
      assert.equal(identity.stateDirectory, registration.installedStudy.stateDirectory);
      lockHeld = true;
      try { return await callback(); } finally { lockHeld = false; events.push(["unlock"]); }
    },
    components: {
      validateAdmission(packet) { events.push(["validate-admission", packet]); return { fixtureOnly: true }; },
      async inspectConfiguration(request, selected) {
        events.push(["configuration", selected]);
        assert.equal(selected.persistentIntent.kind, "fixture-existing-persistent-intent");
        assert.equal(selected.persistentReceipt.kind, "fixture-existing-persistent-receipt");
        return structuredClone(configuration);
      },
      createRuntime(selected) {
        events.push(["runtime-factory", selected]);
        assert.equal(typeof selected.inspectConfiguration, "function");
        assert.equal(selected.persistentReceipt.kind, "fixture-existing-persistent-receipt");
        return {
          async inspectRuntime(request) { events.push(["runtime-inspection", lockHeld]); return { status: "verified", scope: "observed", binding: request }; },
          async prepareDatabase(request) {
            events.push(["prepare-database", lockHeld]); assert.equal(lockHeld, true);
            return { status: "verified", scope: "observed", verified: true, evidenceId: "fixture-database-ready",
              binding: request, clusterIdentityVerified: true, schemaVerified: true, ...(controls.preparation ?? {}) };
          },
          async verifyRuntimeOutcome({ request, desiredState }) {
            events.push(["outcome", desiredState, lockHeld]);
            return { status: "complete", scope: "observed", evidenceId: "fixture-runtime-outcome", binding: request };
          },
        };
      },
      createAuthorityReader(selected) {
        return async request => {
          selected.validateAdmission(selected.admission);
          const publication = selected.readPublication(request);
          assert.deepEqual(publication.registration, request.registration);
          const actual = await selected.inspectConfiguration(request);
          await selected.inspectRuntime(request, { configuration: actual });
          return { status: "verified", scope: "observed", evidenceId: "fixture-authority", binding: request,
            receipt: publication.receipt, configuration: actual, authority: { evidenceId: "fixture-completed-admission",
              targetIdentity: targetKey(request.registration), sourceIdentity: lockIdentity, lockIdentity,
              phase: "complete", originalWriterFenced: true },
            runtimeInspection: { status: "verified", scope: "observed", binding: request, state: controls.runtimeState ?? "cold",
              schemaVerified: controls.runtimeState === "running", healthVerified: controls.runtimeState === "running",
              services: Object.fromEntries(["postgres", "learning", "study-web"].map(name => [name, { running: controls.runtimeState === "running" }])) } };
        };
      },
    },
  };
  const provider = () => createDefaultStudyLauncher({ registration, configPath: options.configPath, log: message => messages.push(message) }, dependencies);
  const execute = () => runRegisteredStudyCommand({ options, registration, log() {} }, provider());
  return { input, registration, receipt, options, configuration, records, events, messages, controls, dependencies, provider, execute };
}
const serviceEffects = events => events.filter(event => event[0] === "run" && ["up", "stop"].some(name => event[2].includes(name)));

test("default assembly carrier is selected before public planning and pinned unchanged", () => {
  const f = fixture(), result = planLauncherFreshStart(f.input);
  assert.equal(result.status, "planned", JSON.stringify(result.blockers));
  assert.deepEqual(result.plan.registration.installedStudy, f.registration.installedStudy);
  assert.equal(Object.isFrozen(result.plan.registration.installedStudy.runtime), true);
  assert.equal(Object.hasOwn(result.plan.registration.installedStudy.runtime, "credentials"), false);
});

for (const command of ["start", "restart"]) test(`default assembly ${command} prepares the existing database under the original lock before app services`, async () => {
  const f = fixture(command);
  assert.equal(await f.execute(), 0);
  const prepare = f.events.findIndex(event => event[0] === "prepare-database");
  const up = f.events.findIndex(event => event[0] === "run" && event[2].includes("up"));
  assert.ok(prepare > f.events.findIndex(event => event[0] === "lock"));
  assert.ok(up > prepare);
  assert.equal(serviceEffects(f.events).length, 1);
  assert.equal(serviceEffects(f.events)[0][3], true);
  assert.deepEqual(serviceEffects(f.events)[0][2].slice(-2), ["learning", "study-web"]);
  assert.ok(f.events.some(event => event[0] === "outcome" && event[1] === "running"));
});

for (const command of ["status", "doctor", "stop"]) test(`default assembly ${command} never prepares or starts PostgreSQL`, async () => {
  const f = fixture(command);
  assert.equal(await f.execute(), 0);
  assert.equal(f.events.some(event => event[0] === "prepare-database"), false);
  assert.equal(serviceEffects(f.events).some(event => event[2].includes("up")), false);
  assert.equal(serviceEffects(f.events).length, command === "stop" ? 1 : 0);
});

test("default status distinguishes stopped cold metadata from verified live health", async () => {
  const cold = fixture("status"); await cold.execute();
  assert.ok(cold.messages.some(message => message.includes("runtime: cold") && message.includes("PostgreSQL stopped")
    && message.includes("schema unverified") && message.includes("health unverified")));
  const running = fixture("status"); running.controls.runtimeState = "running"; await running.execute();
  assert.ok(running.messages.some(message => message.includes("runtime: running") && message.includes("PostgreSQL running")
    && message.includes("schema verified") && message.includes("health verified")));
  assert.deepEqual(serviceEffects(cold.events), []); assert.deepEqual(serviceEffects(running.events), []);
});

test("default assembly read-only plan does not take a lock or prepare services", async () => {
  const f = fixture(); f.options.flags.add("--plan");
  assert.equal(await f.execute(), 0);
  assert.equal(f.events.some(event => ["lock", "prepare-database"].includes(event[0])), false);
  assert.deepEqual(serviceEffects(f.events), []);
});

test("default assembly rereads selected private inputs and publication under the retained lock", async () => {
  const f = fixture(); await f.execute();
  for (const path of [f.options.configPath, ...["originalRegistrationPath", "receiptPath", "admissionPath", "persistentIntentPath", "persistentReceiptPath"]
    .map(name => f.registration.installedStudy[name])])
    assert.ok(f.events.filter(event => event[0] === "read" && event[1] === path).length >= 2, path);
  assert.deepEqual(f.records.get(f.options.configPath), f.registration);
});

for (const name of ["createAuthorityReader", "inspectConfiguration", "createRuntime", "validateAdmission"])
  test(`default assembly missing ${name} blocks before database or app effects`, async () => {
    const f = fixture(); delete f.dependencies.components[name];
    await assert.rejects(f.execute(), error => error.code === "study_provider_unavailable");
    assert.equal(f.events.some(event => event[0] === "prepare-database"), false);
    assert.deepEqual(serviceEffects(f.events), []);
  });

for (const name of ["inspectRuntime", "prepareDatabase", "verifyRuntimeOutcome"])
  test(`default assembly missing runtime ${name} blocks before PostgreSQL preparation`, async () => {
    const f = fixture(), create = f.dependencies.components.createRuntime;
    f.dependencies.components.createRuntime = (...args) => { const runtime = create(...args); delete runtime[name]; return runtime; };
    await assert.rejects(f.execute(), error => error.code === "study_runtime_contract_pending");
    assert.equal(f.events.some(event => event[0] === "prepare-database"), false);
    assert.deepEqual(serviceEffects(f.events), []);
  });

for (const [name, changes] of [["synthetic", { scope: "synthetic-fixture" }], ["unverified", { verified: false }],
  ["missing evidence", { evidenceId: " " }], ["wrong binding", { binding: {} }],
  ["unproven physical cluster", { clusterIdentityVerified: false }], ["unproven live schema", { schemaVerified: false }]])
  test(`default assembly ${name} database preparation blocks app startup`, async () => {
    const f = fixture(); f.controls.preparation = changes;
    await assert.rejects(f.execute(), error => error.code === "study_database_preparation_unverified");
    assert.deepEqual(serviceEffects(f.events), []);
  });

test("default assembly backup and schema update remain explicitly unsupported command-local paths", async () => {
  for (const command of ["backup", "migrate"]) {
    const f = fixture(command);
    await assert.rejects(f.execute(), error => ["study_command_unavailable", "study_schema_update_contract_pending"].includes(error.code));
    assert.equal(f.events.some(event => event[0] === "prepare-database"), false);
    assert.deepEqual(serviceEffects(f.events), []);
  }
});

test("default failed inspection cannot leave usable lock or command capabilities", async () => {
  const f = fixture(), request = { registration: f.registration, command: f.options.command,
    configPath: f.options.configPath, flags: [], services: [] };
  f.dependencies.components.createAuthorityReader = () => async () => { throw new Error("fixture reader failed"); };
  const provider = f.provider();
  await assert.rejects(provider.inspectRegisteredTarget(request), /fixture reader failed/);
  await assert.rejects(provider.withTargetLock({ lockIdentity: f.registration.installedStudy.lockIdentity }, () => assert.fail("lock callback")),
    error => error.code === "study_installation_not_inspected");
  await assert.rejects(provider.maintainAuthoritativeTarget(request, null), error => error.code === "study_installation_not_inspected");
  assert.equal(f.events.some(event => ["lock", "prepare-database"].includes(event[0])), false);
  assert.deepEqual(serviceEffects(f.events), []);
});

for (const [name, change] of [["missing carrier", f => { delete f.registration.installedStudy; }],
  ["unsupported carrier version", f => { f.registration.installedStudy.version = 2; }],
  ["relative selected path", f => { f.registration.installedStudy.receiptPath = "relative.json"; }],
  ["aliased publication paths", f => { f.registration.installedStudy.receiptPath = f.options.configPath; }],
  ["executable module selection", f => { f.registration.installedStudy.module = "/arbitrary/provider.mjs"; }]])
  test(`default assembly ${name} fails before public probes`, async () => {
    const f = fixture(); change(f);
    await assert.rejects(f.execute(), error => error.code === "study_installation_inputs_pending");
    assert.deepEqual(f.events, []);
  });

for (const name of ["DOCKER_HOST", "DOCKER_CONTEXT", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"])
  test(`default assembly ${name} cannot silently override its registered target`, async () => {
    const f = fixture(); f.dependencies.environment = { [name]: "fixture-override" };
    await assert.rejects(f.execute(), error => error.code === "study_environment_override");
    assert.equal(f.events.some(event => ["runtime-factory", "run", "prepare-database"].includes(event[0])), false);
    assert.deepEqual(serviceEffects(f.events), []);
  });

test("default actual CLI v2 path selects assembly without an installed-provider override", async t => {
  const f = fixture("status"), directory = mkdtempSync(join(tmpdir(), "tempo-study-assembly-cli-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const configPath = join(directory, "config.json");
  writeFileSync(configPath, JSON.stringify(f.registration), { mode: 0o600 });
  f.records.set(configPath, f.registration);
  const messages = [];
  assert.equal(await main(["status", "--config", configPath], message => messages.push(message), { studyAssemblyDependencies: f.dependencies }), 0);
  assert.ok(messages.some(message => message.startsWith("Study status:")));
  assert.ok(f.events.some(event => event[0] === "validate-admission"));
  assert.deepEqual(serviceEffects(f.events), []);
});

test("default actual CLI missing private carrier is actionable without loading legacy runtime", async t => {
  const f = fixture("status"), directory = mkdtempSync(join(tmpdir(), "tempo-study-assembly-missing-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  delete f.registration.installedStudy;
  const configPath = join(directory, "config.json"); writeFileSync(configPath, JSON.stringify(f.registration), { mode: 0o600 });
  await assert.rejects(main(["status", "--config", configPath], () => {}), error => error.code === "study_installation_inputs_pending");
});

test("default private input reader refuses group-readable metadata before public probes", async t => {
  const f = fixture("status"), directory = mkdtempSync(join(tmpdir(), "tempo-study-private-inputs-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const source = join(directory, "source"); mkdirSync(source);
  f.registration.root = source;
  f.registration.installedStudy.originalRegistrationPath = join(directory, "original.json");
  writeFileSync(f.registration.installedStudy.originalRegistrationPath, "{}", { mode: 0o600 });
  chmodSync(f.registration.installedStudy.originalRegistrationPath, 0o640);
  delete f.dependencies.readPrivateJson;
  await assert.rejects(f.execute(), error => error.code === "study_installation_input_unsafe");
  assert.deepEqual(f.events, []);
});

for (const kind of ["missing", "invalid JSON", "inside checkout", "symbolic link"])
  test(`default private input reader refuses ${kind} metadata before public probes`, async t => {
    const f = fixture("status"), directory = mkdtempSync(join(tmpdir(), "tempo-study-private-case-"));
    t.after(() => rmSync(directory, { recursive: true, force: true }));
    const source = join(directory, "source"); mkdirSync(source);
    f.registration.root = source;
    const original = join(kind === "inside checkout" ? source : directory, "original.json");
    f.registration.installedStudy.originalRegistrationPath = original;
    if (kind === "symbolic link") {
      const target = join(directory, "target.json"); writeFileSync(target, "{}", { mode: 0o600 }); symlinkSync(target, original);
    } else if (kind !== "missing") writeFileSync(original, kind === "invalid JSON" ? "{" : "{}", { mode: 0o600 });
    delete f.dependencies.readPrivateJson;
    await assert.rejects(f.execute(), error => error.code === (kind === "missing" ? "study_installation_input_missing"
      : kind === "invalid JSON" ? "study_installation_input_invalid" : "study_installation_input_unsafe"));
    assert.deepEqual(f.events, []);
  });

test("default assembly missing original state directory never creates a replacement lock", async t => {
  const f = fixture(), directory = mkdtempSync(join(tmpdir(), "tempo-study-original-state-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const source = join(directory, "source"); mkdirSync(source); f.registration.root = source;
  const records = {};
  for (const name of ["originalRegistrationPath", "receiptPath", "admissionPath", "persistentIntentPath", "persistentReceiptPath"]) {
    const value = f.records.get(f.registration.installedStudy[name]);
    f.registration.installedStudy[name] = join(directory, `${name}.json`); records[name] = value;
  }
  f.options.configPath = join(directory, "config.json");
  f.registration.installedStudy.stateDirectory = join(directory, "missing-original-state");
  writeFileSync(f.options.configPath, JSON.stringify(f.registration), { mode: 0o600 });
  for (const [name, value] of Object.entries(records)) writeFileSync(f.registration.installedStudy[name], JSON.stringify(value), { mode: 0o600 });
  delete f.dependencies.readPrivateJson; delete f.dependencies.withLock;
  await assert.rejects(f.execute(), error => error.code === "study_installation_input_missing");
  assert.equal(existsSync(f.registration.installedStudy.stateDirectory), false);
  assert.equal(f.events.some(event => event[0] === "prepare-database"), false);
  assert.deepEqual(serviceEffects(f.events), []);
});

for (const alias of ["ancestor symlink", "hard link"])
  test(`default private input ${alias} cannot alias original registration and deployment receipt`, async t => {
    const f = fixture(), directory = mkdtempSync(join(tmpdir(), "tempo-study-private-alias-"));
    t.after(() => rmSync(directory, { recursive: true, force: true }));
    const source = join(directory, "source"); mkdirSync(source); f.registration.root = source;
    const records = {};
    for (const name of ["originalRegistrationPath", "receiptPath", "admissionPath", "persistentIntentPath", "persistentReceiptPath"]) {
      records[name] = f.records.get(f.registration.installedStudy[name]);
      f.registration.installedStudy[name] = join(directory, `${name}.json`);
      writeFileSync(f.registration.installedStudy[name], JSON.stringify(records[name]), { mode: 0o600 });
    }
    unlinkSync(f.registration.installedStudy.receiptPath);
    if (alias === "hard link") linkSync(f.registration.installedStudy.originalRegistrationPath, f.registration.installedStudy.receiptPath);
    else {
      const link = join(directory, "alias"); symlinkSync(directory, link, "dir");
      f.registration.installedStudy.receiptPath = join(link, "originalRegistrationPath.json");
    }
    f.options.configPath = join(directory, "config.json");
    writeFileSync(f.options.configPath, JSON.stringify(f.registration), { mode: 0o600 });
    delete f.dependencies.readPrivateJson;
    await assert.rejects(f.execute(), error => error.code === "study_installation_input_unsafe");
    assert.equal(f.events.some(event => ["runtime-factory", "run", "prepare-database"].includes(event[0])), false);
  });

test("default settings remain immutable independently of caller mutations", () => {
  const f = fixture(), selected = installedStudySettings(f.registration, f.options.configPath);
  f.registration.installedStudy.runtime.postgres.major = 999;
  assert.equal(selected.runtime.postgres.major, 18);
  assert.equal(Object.isFrozen(selected.runtime.credentialDigests), true);
});
