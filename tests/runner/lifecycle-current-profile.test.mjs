import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { assertCurrent63Guard, validateLifecycleProfile } from "../../scripts/check-tempo-cli.mjs";

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
