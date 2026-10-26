import assert from "node:assert/strict";
import { test } from "node:test";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { main, parseArguments } from "../../scripts/tempo-cli.mjs";
import { targetKey } from "../../scripts/tempo-deployment.mjs";
import { createInstalledStudyLauncher, runRegisteredStudyCommand } from "../../scripts/tempo-study-launcher.mjs";

// All providers, observations and effect receipts in this file are injected
// fakes. Exercising the installed-provider protocol is not actual admission.
function fixture() {
  const images = Object.fromEntries(["learning", "study-web", "postgres"].map((name, index) => [name, `sha256:${String(index + 1).repeat(64)}`]));
  const volumes = { learning: { name: "fixture-new-learning", external: true } };
  const cutover = { mode: "graph-preserving-fresh-start", transitionId: "fixture-transition", phase: "quarantined",
    revision: "a".repeat(40), schema: `sha256:${"7".repeat(64)}`, images, volumes,
    composeFiles: ["/fixture/release/serving.yml"], database: { name: "fixture_learning", volume: "fixture-new-learning" },
    workspace: "fixture-fresh", ingress: { origin: "http://127.0.0.1:38001", hosts: ["127.0.0.1:38001"] },
    graphSourceReference: "fixture-source", graphReceipt: "fixture-graph", freshInitReceipt: "fixture-init" };
  const registration = { version: 2, root: "/fixture/release", context: "fixture-context", daemonId: "fixture-daemon",
    project: "fixture-study", envFile: "/fixture/private/runtime.env", postgresVolumeKey: "learning", volumes,
    ports: [{ service: "study-web", host: "127.0.0.1", port: "38001", target: 80 }],
    composeFiles: cutover.composeFiles, webUrl: cutover.ingress.origin, cutover };
  const receipt = { version: 2, revision: cutover.revision, schema: cutover.schema,
    images, composeFiles: cutover.composeFiles, cutover };
  const calls = [];
  const provider = {
    executionScope: "installed",
    async inspectRegisteredTarget(request) {
      calls.push(["inspect", request]);
      return { status: "verified", scope: "observed", evidenceId: "fixture-observation", binding: structuredClone(request), receipt: structuredClone(receipt),
        authority: { evidenceId: "fixture-admission", phase: "complete", targetIdentity: targetKey(registration),
          sourceIdentity: "fixture-original-lock", lockIdentity: "fixture-original-lock", originalWriterFenced: true } };
    },
    async withTargetLock(identity, callback) { calls.push(["lock", identity]); try { return await callback(); } finally { calls.push(["unlock"]); } },
  };
  for (const name of ["maintainAuthoritativeTarget", "backupAuthoritativeTarget", "stopAuthoritativeTarget"])
    provider[name] = async request => { calls.push([name, request]); return { status: "complete", scope: "observed", evidenceId: "fixture-completion", binding: structuredClone(request) }; };
  return { registration, receipt, provider, calls };
}
const options = (...args) => parseArguments([...args, "--config", "/fixture/private/registration.json"]);
const invoke = (f, args = ["start"]) => runRegisteredStudyCommand({ options: options(...args), registration: f.registration, log() {} }, f.provider);
const effects = f => f.calls.filter(([name]) => /AuthoritativeTarget$/.test(name));

test("normal CLI v2 provider absence blocks before legacy runtime or Docker", async () => {
  const f = fixture(); f.provider = undefined;
  await assert.rejects(invoke(f), { code: "study_provider_unavailable" });
  assert.equal(effects(f).length, 0);
});
test("normal CLI v2 synthetic provider cannot grant installed admission", async () => {
  const f = fixture(); f.provider.executionScope = "synthetic-fixture";
  await assert.rejects(invoke(f), { code: "study_provider_unavailable" });
  assert.deepEqual(f.calls, []);
});
for (const command of ["status", "doctor", "logs", "start", "restart", "stop", "backup", "migrate"])
  test(`normal CLI v2 ${command} selects only its public semantic port`, async () => {
    const f = fixture(); await invoke(f, [command]);
    const expected = ["status", "doctor", "logs"].includes(command) ? null
      : command === "backup" ? "backupAuthoritativeTarget" : command === "stop" ? "stopAuthoritativeTarget" : "maintainAuthoritativeTarget";
    assert.deepEqual(effects(f).map(([name]) => name), expected ? [expected] : []);
    assert.equal(f.calls.filter(([name]) => name === "inspect").length, expected ? 2 : 1);
    assert.equal(f.calls.some(([name]) => name === "lock"), Boolean(expected));
    if (expected) assert.deepEqual(f.calls.find(([name]) => name === "lock")[1], { lockIdentity: "fixture-original-lock" });
  });
test("normal CLI v2 plan inspects without acquiring lock or changing authority", async () => {
  const f = fixture(); await invoke(f, ["restart", "--plan"]);
  assert.deepEqual(f.calls.map(([name]) => name), ["inspect"]);
});
for (const flag of ["--no-ci", "--no-wait", "--retry"])
  test(`normal CLI v2 ${flag} cannot bypass installed authority`, async () => {
    const f = fixture(); await assert.rejects(invoke(f, ["start", flag]), { code: "study_option_unavailable" });
    assert.deepEqual(f.calls, []);
  });
for (const [name, alter, code] of [
  ["synthetic observation", o => { o.scope = "synthetic-fixture"; }, "study_authority_unverified"],
  ["partial request binding", o => { delete o.binding.configPath; }, "study_authority_unverified"],
  ["wrong registration binding", o => { o.binding.registration.project = "another-project"; }, "study_authority_unverified"],
  ["missing receipt", o => { delete o.receipt; }, "study_publication_mismatch"],
  ["schema count substitute", o => { o.receipt.schema = 4; }, "study_publication_mismatch"],
  ["wrong image", o => { o.receipt.images.learning = `sha256:${"9".repeat(64)}`; }, "study_publication_mismatch"],
  ["wrong graph receipt", o => { o.receipt.cutover.graphReceipt = "other-graph"; }, "study_publication_mismatch"],
  ["wrong target", o => { o.authority.targetIdentity = "other-target"; }, "study_admission_unverified"],
  ["changed original lock", o => { o.authority.lockIdentity = "new-target-lock"; }, "study_admission_unverified"],
  ["quarantined publication", o => { o.authority.phase = "published"; }, "study_admission_unverified"],
  ["original writer unfenced", o => { o.authority.originalWriterFenced = false; }, "study_admission_unverified"],
]) test(`normal CLI v2 refuses ${name} before any effect`, async () => {
  const f = fixture(), inspect = f.provider.inspectRegisteredTarget;
  f.provider.inspectRegisteredTarget = async request => { const o = await inspect(request); alter(o); return o; };
  await assert.rejects(invoke(f), { code }); assert.equal(effects(f).length, 0);
});
test("normal CLI v2 failed public inspection has no legacy fallback", async () => {
  const f = fixture(); f.provider.inspectRegisteredTarget = async () => { throw new Error("fixture-provider-outage"); };
  await assert.rejects(invoke(f), /fixture-provider-outage/); assert.equal(effects(f).length, 0);
});
test("normal CLI v2 changed publication under original lock blocks effect", async () => {
  const f = fixture(), inspect = f.provider.inspectRegisteredTarget;
  let count = 0;
  f.provider.inspectRegisteredTarget = async request => { const o = await inspect(request); if (++count === 2) o.receipt.verified_at = "changed"; return o; };
  await assert.rejects(invoke(f), { code: "study_authority_changed" }); assert.equal(effects(f).length, 0);
});
test("normal CLI v2 absent backup port blocks backup only", async () => {
  const f = fixture(); delete f.provider.backupAuthoritativeTarget;
  await assert.rejects(invoke(f, ["backup"]), { code: "study_command_unavailable" });
  await invoke(f, ["status"]); await invoke(f, ["start"]);
  assert.deepEqual(effects(f).map(([name]) => name), ["maintainAuthoritativeTarget"]);
});
for (const [name, value] of [["empty", ""], ["number", 0], ["whitespace", "   "]])
  test(`normal CLI v2 ${name} command evidence is not completion`, async () => {
    const f = fixture(); f.provider.maintainAuthoritativeTarget = async request => ({ status: "complete", scope: "observed", evidenceId: value, binding: request });
    await assert.rejects(invoke(f), { code: "study_command_unverified" });
  });
test("normal CLI v2 synthetic command completion is rejected", async () => {
  const f = fixture(); f.provider.maintainAuthoritativeTarget = async request => ({ status: "complete", scope: "synthetic-fixture", evidenceId: "fixture", binding: request });
  await assert.rejects(invoke(f), { code: "study_command_unverified" });
});
test("normal CLI v2 no-op lock cannot report successful command", async () => {
  const f = fixture(); f.provider.withTargetLock = async () => 0;
  await assert.rejects(invoke(f), { code: "study_lock_callback_invalid" }); assert.equal(effects(f).length, 0);
});
test("normal CLI v2 lock callback runs at most one command effect", async () => {
  const f = fixture(); f.provider.withTargetLock = async (_identity, callback) => { await callback(); return callback(); };
  await assert.rejects(invoke(f), { code: "study_lock_callback_invalid" }); assert.equal(effects(f).length, 1);
});
test("normal CLI v2 late callback cannot act after lock capability returned", async () => {
  const f = fixture(); let callback;
  f.provider.withTargetLock = async (_identity, value) => { callback = value; return 0; };
  await assert.rejects(invoke(f), { code: "study_lock_callback_invalid" });
  await assert.rejects(callback(), { code: "study_lock_callback_invalid" }); assert.equal(effects(f).length, 0);
});
test("normal CLI v2 premature lock return cannot continue an asynchronous command callback", async () => {
  const f = fixture(), inspect = f.provider.inspectRegisteredTarget;
  let inspectionCount = 0, resume, callbackResult;
  f.provider.inspectRegisteredTarget = async request => {
    const result = await inspect(request);
    if (++inspectionCount === 2) await new Promise(resolve => { resume = resolve; });
    return result;
  };
  f.provider.withTargetLock = async (_identity, callback) => { callbackResult = callback(); return 0; };
  await assert.rejects(invoke(f), { code: "study_lock_callback_invalid" });
  // Let the read-only inspection finish after its owning lock has returned.
  while (!resume) await new Promise(resolve => setImmediate(resolve));
  resume(); await assert.rejects(callbackResult, { code: "study_lock_callback_invalid" }); assert.equal(effects(f).length, 0);
});
test("normal CLI v2 request and input registration retain independent immutable ownership", async () => {
  const f = fixture(); const before = structuredClone(f.registration), inspect = f.provider.inspectRegisteredTarget;
  f.provider.inspectRegisteredTarget = async request => {
    assert(Object.isFrozen(request)); assert(Object.isFrozen(request.registration.cutover.images));
    assert.throws(() => { request.registration.cutover.images.learning = "changed"; }, TypeError);
    return inspect(request);
  };
  await invoke(f); assert.deepEqual(f.registration, before);
});

test("actual v2 main imports and diagnoses with legacy runtime and all database probes physically absent", t => {
  const root = mkdtempSync(join(tmpdir(), "tempo-study-routing-")); t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, "scripts"));
  for (const name of ["tempo-cli.mjs", "tempo-study-launcher.mjs", "tempo-deployment.mjs", "tempo-guidance.mjs"])
    copyFileSync(new URL(`../../scripts/${name}`, import.meta.url), join(root, "scripts", name));
  assert.equal(existsSync(join(root, "scripts", "tempo-runtime.mjs")), false);
  assert.equal(existsSync(join(root, "database")), false);
  const f = fixture(), config = join(root, "registration.json"); writeFileSync(config, JSON.stringify(f.registration));
  const harness = join(root, "harness.mjs");
  writeFileSync(harness, `import { main } from './scripts/tempo-cli.mjs';\nimport { targetKey } from './scripts/tempo-deployment.mjs';\nconst registration=${JSON.stringify(f.registration)};\nconst receipt=${JSON.stringify(f.receipt)};\nconst provider={executionScope:'installed',async inspectRegisteredTarget(request){return{status:'verified',scope:'observed',evidenceId:'injected-fake-only',binding:request,receipt,authority:{evidenceId:'injected-fake-only',phase:'complete',targetIdentity:targetKey(registration),sourceIdentity:'original',lockIdentity:'original',originalWriterFenced:true}}}};\nfor(const command of ['status','doctor'])await main([command,'--config',${JSON.stringify(config)}],console.log,{studyLauncher:provider});\ntry{await main(['start','--config',${JSON.stringify(config)}]);throw Error('missing provider passed')}catch(e){if(e.code!=='study_provider_unavailable')throw e}\n`);
  const result = spawnSync(process.execPath, [harness], { encoding: "utf8", env: { PATH: "", HOME: root, TEMPO_CLI_STATE_DIR: join(root, "never-created") } });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Study status: published authority verified/);
  assert.match(result.stdout, /Study doctor: published authority verified/);
  assert.equal(existsSync(join(root, "never-created")), false);
});

function installedFixture() {
  const f = fixture(), originalInspect = f.provider.inspectRegisteredTarget;
  const configuration = { name: f.registration.project, volumes: structuredClone(f.registration.volumes), services: {
    learning: { image: f.receipt.images.learning }, postgres: { image: f.receipt.images.postgres },
    "study-web": { image: f.receipt.images["study-web"], ports: [{ host_ip: "127.0.0.1", published: "38001", target: 80 }] },
  } };
  const dependencies = {
    async readAuthority(request) { return { ...await originalInspect(request), configuration: structuredClone(configuration) }; },
    state: { lockIdentity: "fixture-original-lock", async withLock(callback) { f.calls.push(["retained-original-lock"]); return callback(); } },
    async run(command, args, settings) {
      f.calls.push(["docker", command, [...args], settings]);
      return { code: 0, stdout: args.includes("config") ? JSON.stringify(configuration) : "", stderr: "" };
    },
    async verifyRuntimeOutcome({ request, desiredState }) {
      f.calls.push(["outcome", desiredState]);
      return { status: "complete", scope: "observed", evidenceId: "injected-fake-runtime-readback", binding: structuredClone(request) };
    },
  };
  const install = () => { f.provider = createInstalledStudyLauncher(dependencies); return f; };
  return { f, configuration, dependencies, install };
}
const dockerEffects = f => f.calls.filter(([name, , args]) => name === "docker" && ["up", "stop"].some(operation => args.includes(operation)));

test("installed factory cannot derive real admission from synthetic lifecycle journal or bootstrap receipt", async () => {
  const { f, dependencies, install } = installedFixture();
  delete dependencies.readAuthority;
  dependencies.state.readJournal = () => { throw new Error("private journal must never be read"); };
  install(); await assert.rejects(invoke(f), { code: "production-admission-contract-pending" });
  assert.deepEqual(f.calls, []);
});
test("installed factory starts only selected Study services with pinned no-build/no-pull/no-dependencies choreography", async () => {
  const { f, install } = installedFixture(); install(); await invoke(f);
  const effect = dockerEffects(f); assert.equal(effect.length, 1);
  assert.deepEqual(effect[0][2], ["--context", "fixture-context", "compose", "--project-directory", "/fixture/release",
    "--env-file", "/fixture/private/runtime.env", "-p", "fixture-study", "-f", "/fixture/release/serving.yml",
    "up", "-d", "--no-build", "--pull", "never", "--no-deps", "learning", "study-web"]);
  assert.deepEqual(f.calls.filter(([name]) => name === "outcome").map(([, state]) => state), ["running"]);
  assert.equal(f.calls.filter(([name]) => name === "retained-original-lock").length, 1);
});
test("installed factory restart recreates Study consumers without starting PostgreSQL or bootstrap", async () => {
  const { f, install } = installedFixture(); install(); await invoke(f, ["restart"]);
  const args = dockerEffects(f)[0][2]; assert(args.includes("--force-recreate"));
  assert.deepEqual(args.slice(-2), ["learning", "study-web"]); assert(!args.includes("postgres")); assert(!args.includes("bootstrap"));
});
test("installed factory stop retains database and source resources", async () => {
  const { f, install } = installedFixture(); install(); await invoke(f, ["stop"]);
  assert.deepEqual(dockerEffects(f)[0][2].slice(-3), ["stop", "study-web", "learning"]);
  assert.deepEqual(f.calls.filter(([name]) => name === "outcome").map(([, state]) => state), ["stopped"]);
});
test("installed factory missing read-only outcome capability prevents service effect", async () => {
  const { f, dependencies, install } = installedFixture(); delete dependencies.verifyRuntimeOutcome; install();
  await assert.rejects(invoke(f), { code: "study_runtime_outcome_contract_pending" }); assert.deepEqual(dockerEffects(f), []);
});
test("installed factory schema updates remain explicit unsupported source contract without migration effects", async () => {
  const { f, install } = installedFixture(); install();
  await assert.rejects(invoke(f, ["migrate"]), { code: "study_schema_update_contract_pending" }); assert.deepEqual(dockerEffects(f), []);
});
test("installed factory missing Learning-owned backup capability does not block status or ordinary start", async () => {
  const { f, install } = installedFixture(); install();
  await assert.rejects(invoke(f, ["backup"]), { code: "study_command_unavailable" });
  await invoke(f, ["doctor"]); await invoke(f); assert.equal(dockerEffects(f).length, 1);
});
test("installed factory backup receives only selected authoritative request under original lock", async () => {
  const { f, dependencies, install } = installedFixture();
  dependencies.backupTarget = async (request, observation) => {
    f.calls.push(["public-learning-backup", request, observation]);
    return { status: "complete", scope: "observed", evidenceId: "injected-fake-restore-verified-backup", binding: request };
  };
  install(); await invoke(f, ["backup"]);
  const call = f.calls.find(([name]) => name === "public-learning-backup");
  assert.equal(call[1].registration.project, "fixture-study"); assert.equal(call[2].authority.phase, "complete");
  assert.deepEqual(dockerEffects(f), []);
  assert(f.calls.findIndex(([name]) => name === "retained-original-lock") < f.calls.findIndex(([name]) => name === "public-learning-backup"));
});
test("installed factory diagnostics and logs use only read-only public configuration/log capabilities", async () => {
  const { f, install } = installedFixture(); install();
  await invoke(f, ["status"]); await invoke(f, ["doctor"]); await invoke(f, ["logs", "learning"]); await invoke(f, ["restart", "--plan"]);
  assert.deepEqual(dockerEffects(f), []); assert(!f.calls.some(([name]) => name === "retained-original-lock"));
  const logs = f.calls.filter(([name, , args]) => name === "docker" && args.includes("logs")); assert.equal(logs.length, 1);
  assert.deepEqual(logs[0][2].slice(-5), ["logs", "--no-color", "--tail", "100", "learning"]);
});
test("installed factory unknown legacy service cannot select a log or lifecycle path", async () => {
  const { f, install } = installedFixture(); install();
  await assert.rejects(invoke(f, ["logs", "redis"]), { code: "study_service_unknown" }); assert.deepEqual(dockerEffects(f), []);
});
for (const [name, alter] of [
  ["unapproved current configuration", c => { c.name = "other-project"; }],
  ["extra legacy service", c => { c.services.redis = {}; }],
  ["bootstrap service", c => { c.services.bootstrap = {}; }],
  ["mutable Learning image", c => { c.services.learning.image = "learning:latest"; }],
  ["different external volume", c => { c.volumes.learning.name = "source-volume"; }],
  ["managed data volume", c => { c.volumes.learning.external = false; }],
  ["Learning host port", c => { c.services.learning.ports = [{ published: "8001" }]; }],
  ["PostgreSQL host port", c => { c.services.postgres.ports = [{ published: "5432" }]; }],
  ["public ingress", c => { c.services["study-web"].ports[0].host_ip = "0.0.0.0"; }],
  ["different ingress port", c => { c.services["study-web"].ports[0].published = "3000"; }],
]) test(`installed factory rejects ${name} before effect`, async () => {
  const { f, configuration, install } = installedFixture(); alter(configuration); install();
  await assert.rejects(invoke(f), { code: "study_configuration_unverified" }); assert.deepEqual(dockerEffects(f), []);
});
test("installed factory original lock mismatch cannot acquire a replacement target lock", async () => {
  const { f, dependencies, install } = installedFixture(); dependencies.state.lockIdentity = "fresh-target-key"; install();
  await assert.rejects(invoke(f), { code: "study_lock_contract_pending" }); assert.deepEqual(dockerEffects(f), []);
});
test("installed factory matching mutable receipt and configuration images cannot qualify pinned source", async () => {
  const { f, configuration, install } = installedFixture();
  f.registration.cutover.images.learning = "learning:latest";
  configuration.services.learning.image = "learning:latest";
  install(); await assert.rejects(invoke(f), { code: "study_registration_incomplete" }); assert.deepEqual(f.calls, []);
});
test("installed factory missing rendered registered ingress is not an exact target match", async () => {
  const { f, install } = installedFixture();
  f.registration.ports.push({ service: "study-web", host: "127.0.0.1", port: "38002", target: 80 });
  install(); await assert.rejects(invoke(f), { code: "study_configuration_unverified" }); assert.deepEqual(dockerEffects(f), []);
});
test("installed factory failed Docker effect cannot be relabelled completed by outcome proof", async () => {
  const { f, dependencies, install } = installedFixture(), run = dependencies.run;
  dependencies.run = async (...args) => { const result = await run(...args); return args[1].includes("up") ? { ...result, code: 1 } : result; };
  install(); await assert.rejects(invoke(f), { code: "study_command_failed" }); assert.equal(dockerEffects(f).length, 1);
  assert(!f.calls.some(([name]) => name === "outcome"));
});
test("installed factory synthetic post-effect runtime proof remains uncertain and never triggers legacy fallback", async () => {
  const { f, dependencies, install } = installedFixture();
  dependencies.verifyRuntimeOutcome = async ({ request }) => ({ status: "complete", scope: "synthetic-fixture", evidenceId: "not-actual-proof", binding: request });
  install(); await assert.rejects(invoke(f), { code: "study_runtime_outcome_unverified" }); assert.equal(dockerEffects(f).length, 1);
});
