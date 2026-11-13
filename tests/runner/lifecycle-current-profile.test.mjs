import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { assertCurrent63Guard, assertLifecycleOperationIdentity, classifyLifecycleOperation, pollLifecycleOperation, readLifecycleQueue, validateLifecycleProfile, verifyTempoCliLifecycle, verifyCurrent65Transition } from "../../scripts/check-tempo-cli.mjs";

test("current65 polling binds acknowledgment and every receipt to the original operation", () => {
  assert.equal(assertLifecycleOperationIdentity({ operation_id: "original" }, "original"), "original");
  for (const value of [{}, { operation_id: "" }, { operation_id: "foreign" }]) assert.throws(() => assertLifecycleOperationIdentity(value, "original"), /identity/);
  for (const state of ["unknown", "pending", "queued", "executing", "retrying"]) assert.equal(classifyLifecycleOperation({ operation_id: "original", state }, "original"), "pending");
  assert.equal(classifyLifecycleOperation({ operation_id: "original", state: "complete" }, "original"), "complete");
  for (const state of ["failed", "blocked", "unrecognized"]) assert.throws(() => classifyLifecycleOperation({ operation_id: "original", state }, "original"));
  assert.throws(() => classifyLifecycleOperation({ operation_id: "foreign", state: "complete" }, "original"), /identity/);
});
test("current65 polling waits for matching unknown but terminates at its bound", async () => {
  const responses = [{ operation_id: "original", state: "unknown" }, { operation_id: "original", state: "complete", response: { review_id: 7 } }];
  assert.deepEqual(await pollLifecycleOperation("original", async () => responses.shift(), async () => {}, 2), { review_id: 7 });
  let reads = 0;
  await assert.rejects(pollLifecycleOperation("original", async () => { reads++; return { operation_id: "original", state: "unknown" }; }, async () => {}, 2), /bounded poll limit/);
  assert.equal(reads, 2);
});
test("current65 queue rereads authority after a 202 issue receipt", async () => {
  let reads = 0;
  const queue = await readLifecycleQueue(async () => {
    reads++;
    if (reads === 1) {
      const response = await pollLifecycleOperation("issue-original", async () => ({ operation_id: "issue-original", state: "complete", response: { issued: true } }), async () => {}, 1);
      return { pending: true, value: response };
    }
    return { pending: false, value: { cards: [{ id: "playable" }] } };
  }, async () => {}, 10, () => 0);
  assert.equal(reads, 2); assert.equal(queue.cards[0].id, "playable");
  for (const receipt of [{ operation_id: "foreign", state: "complete" }, { operation_id: "issue-original", state: "failed" }])
    await assert.rejects(readLifecycleQueue(async () => ({ pending: true, value: await pollLifecycleOperation("issue-original", async () => receipt, async () => {}, 1) }), async () => {}, 10, () => 0));
});

test("current65 lifecycle preserves original guard and review through refusal and retry", async () => {
  const versions65 = Array.from({ length: 65 }, (_, i) => i + 1), versions66 = [...versions65, 66];
  const original = { review: { review_id: 7, queue_entry_id: 9 }, receipt: { state: "complete", operation_id: "original" } };
  const pending = { state: "pending", target: "owned", starting_schema: 65, intended_schema: 66, starting_versions: versions65,
    attempt_id: "attempt", backup: { verified: true, restore_removed: true, filename: "original.dump" } };
  let bytes = Buffer.from(JSON.stringify(pending)), versions = versions65, saved = { schema: 66 }, events = [];
  const readGuard = () => JSON.parse(bytes.toString());
  const makeCandidate = options => ({ ...options, inspectTarget: async () => {}, preflightSchema: async () => {} });
  const lifecycle = async (label, _plan, runtime) => {
    events.push(label);
    if (label === "current65_before_write") return runtime.preflightSchema();
    if (label === "current65_interrupted") {
      versions = versions66;
      return runtime.intercept(async () => ({}), "docker", ["database/tooling/apply_postgres_migrations.py"], {});
    }
    if (label === "current65_unresolved_refusal") throw new Error("pending migration requires retry");
    if (label === "current65_mismatched_guard") { assert.equal(readGuard().target, "foreign-target"); throw new Error("foreign guard"); }
    if (label === "current65_retry_original") bytes = Buffer.from(JSON.stringify({ ...pending, state: "verified" }));
  };
  const result = await verifyCurrent65Transition({ makeCandidate, lifecycle, snapshot: async () => original,
    readGuard, readGuardBytes: () => bytes, writeGuardBytes: value => { bytes = value; }, readDeployment: () => saved,
    readVersions: async () => versions, commandLog: [] });
  assert.equal(result.settled.attempt_id, pending.attempt_id);
  assert.deepEqual(result.settled.backup, pending.backup);
  assert.deepEqual(events, ["current65_before_write", "current65_interrupted", "current65_unresolved_refusal", "current65_mismatched_guard", "current65_retry_original", "current65_repeat", "current65_recreate"]);
  await assert.rejects(verifyCurrent65Transition({ makeCandidate, lifecycle, snapshot: async () => original,
    originalBeforeHandoff: { ...original, receipt: { state: "complete", operation_id: "different" } },
    readGuard, readGuardBytes: () => bytes, writeGuardBytes: value => { bytes = value; }, readDeployment: () => saved,
    readVersions: async () => versions, commandLog: [] }), /handoff must preserve/);
});

test("current65 root handoff preserves volumes and starts only owned dependencies before candidate inspection", () => {
  const source = readFileSync(new URL("../../scripts/check-tempo-cli.mjs", import.meta.url), "utf8");
  const start = source.indexOf("      const originalBeforeHandoff = await snapshot();");
  const end = source.indexOf("      await verifyCurrent65Transition(", start);
  const handoff = source.slice(start, end);
  assert(start >= 0 && end > start);
  assert(handoff.indexOf("handoffOwnedSchema62(") < handoff.indexOf('"postgres", "redis"], { echo: true }'));
  assert.match(handoff, /compose\(\["--profile", "maintenance", "down"\]\)/);
  assert.match(handoff, /snapshotVolumes\(\), originalVolumes/);
  assert.match(handoff, /Application writers must remain stopped/);
  assert(!handoff.includes('"down", "-v"'));
});

test("current lifecycle profile rejects unknown, wrong-schema and schema59 selections before work", () => {
  assert.doesNotThrow(() => validateLifecycleProfile("full", 62, {}));
  assert.doesNotThrow(() => validateLifecycleProfile("current63", 63, {}));
  for (const [profile, schema, environment] of [
    ["unknown", 63, {}], ["current63", 62, {}],
    ["current63", 63, { TEMPO_LIFECYCLE_P0_59_ONLY: "1" }],
    ["current63", 63, { TEMPO_PG_STALE_DELIVERY_ONLY: "1" }],
  ]) assert.throws(() => validateLifecycleProfile(profile, schema, environment));
});

test("current lifecycle profile runs the real predecessor and witnessed cut before its current tail", async () => {
  const source = readFileSync(new URL("../../scripts/check-tempo-cli.mjs", import.meta.url), "utf8");
  const start = source.indexOf('\n    if (profile === "current63") {\n      // This explicitly selected short path');
  const end = source.indexOf("    if (schema59Only) {", start);
  assert(start >= 0 && end > start);
  const branch = source.slice(start, end);
  const run = async (missingWitness = false, badHistoryGuard = false) => {
    let phase = 0; const events = [], expected = { history: "original" };
    const context = {
      profile: "current63", assert, join, stateDirectory: "/owned/state", schema61Revision: "historical61", revision: "intermediate62", images: {}, schema61Images: {}, config: {}, target: {}, run: () => {},
      childProject: "owned-cli", evidence: {}, expected, compose: {}, configurationFingerprint: () => "fingerprint",
      makeRuntime: (_target, options) => ({ options, inspectTarget: async () => events.push(`inspect:${options.revision}`) }),
      lifecycle: async (label, plan, runtime) => { events.push(`${label}:${runtime.options.revision}:${plan.recreate}`); phase++; },
      readFileSync: path => JSON.stringify(path.endsWith("migration-guard.json")
        ? phase === 1 ? { state: "verified", starting_schema: badHistoryGuard ? 15 : 16, intended_schema: 61, backup: { verified: true } } : { state: "verified", starting_schema: 61, intended_schema: 62, backup: { verified: true }, cut62_identity: { witness_id: "original" }, cut62_witness: "cut62-id.json" }
        : { revision: phase === 1 ? "historical61" : "intermediate62", schema: phase === 1 ? 61 : 62, backup: { verified: true } }),
      readLatestMigration: async () => ({ stdout: String(phase === 1 ? 61 : 62) }), readHistory: async () => expected,
      existsSync: () => !missingWitness, finishCurrentSchema: async () => events.push("current63-tail"),
    };
    await runInNewContext(`(async () => { ${branch} })()`, context);
    return events;
  };
  assert.deepEqual(await run(), ["inspect:historical61", "current63_schema16_to_61:historical61:true", "inspect:intermediate62", "current63_schema61_to_62:intermediate62:true", "current63-tail"]);
  await assert.rejects(run(true), /retains its original witness/);
  await assert.rejects(run(false, true));
});

test("current lifecycle source preflight rejects unsupported selection before source acquisition", async t => {
  const { mkdtempSync, mkdirSync, writeFileSync, rmSync } = await import("node:fs");
  const { tmpdir } = await import("node:os");
  const { checkLifecycleSource } = await import("../../scripts/lifecycle-source-preflight.mjs");
  const root = mkdtempSync(join(tmpdir(), "tempo-current-profile-")); t.after(() => rmSync(root, { recursive: true, force: true }));
  const ddl = join(root, "database/tempo_legacy_database/ddl"); mkdirSync(ddl, { recursive: true });
  const version = join(ddl, "schema_version.py"); writeFileSync(version, "POSTGRES_SCHEMA_VERSION = 62\n");
  assert.throws(() => checkLifecycleSource({ root, profile: "current63", environment: {} }), /requires schema 63/);
  writeFileSync(version, "POSTGRES_SCHEMA_VERSION = 63\n");
  assert.throws(() => checkLifecycleSource({ root, profile: "current63", environment: { TEMPO_LIFECYCLE_P0_59_ONLY: "1" } }), /schema-61 predecessor/);
  assert.throws(() => checkLifecycleSource({ root, profile: "current63", environment: { TEMPO_PG_STALE_DELIVERY_ONLY: "1" } }), /schema-61 predecessor/);
  assert.throws(() => checkLifecycleSource({ root, profile: "unknown", environment: {} }), /Unknown lifecycle source profile/);
});


test("current lifecycle profile requires the original verified schema63 guard and backup", () => {
  const guard = { state: "verified", starting_schema: 62, intended_schema: 63, backup: { verified: true } };
  assert.doesNotThrow(() => assertCurrent63Guard(guard));
  for (const mutation of [{ state: "pending" }, { starting_schema: 61 }, { intended_schema: 62 }, { backup: { verified: false } }])
    assert.throws(() => assertCurrent63Guard({ ...guard, ...mutation }));
});


test("schema64 cannot reuse historical lifecycle qualification through direct profile calls", () => {
  for (const profile of ["full", "current63"])
    assert.throws(() => validateLifecycleProfile(profile, 64, {}), /Schema-64.*not qualified/);
});

test("direct lifecycle verifier refuses native66 historical profile before resources or timing stages", async () => {
  let stages = 0;
  for (const profile of ["full", "current63"])
    await assert.rejects(verifyTempoCliLifecycle({
      project: "must-not-acquire", revision: "a".repeat(40), composeFiles: [], profile,
      environment: { TEMPO_LIFECYCLE_SCHEMA61_IMAGES: "/must-not-read", DOCKER_HOST: "must-not-contact" },
      measure: () => { stages++; throw new Error("Resources must not be acquired"); },
    }), /Schema-66.*not qualified/);
  assert.equal(stages, 0);
});

test("schema65 cannot reuse historical lifecycle qualification", () => {
  for (const profile of ["full", "current63"])
    assert.throws(() => validateLifecycleProfile(profile, 65, {}), /Schema-65.*not qualified/);
});
