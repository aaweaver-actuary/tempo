import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { assertBridgePreparedBinding, assertBridgeTargetIdentity, checkLifecycleSource, readSchema62Bridge, schema62BridgeRevision, schema62BridgeTree, readSchema65Baseline, schema65BaselineRevision, schema65BaselineTree, renderInterruptedLifecycle } from "../../scripts/lifecycle-source-preflight.mjs";

test("schema65 baseline requires exact detached source and complete immutable image roles", t => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "tempo-schema65-source-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, "database/tempo_storage/ddl"), { recursive: true });
  writeFileSync(join(root, "database/tempo_storage/ddl/schema_version.py"), "POSTGRES_SCHEMA_VERSION = 65\n");
  const file = join(root, "images.json"), roles = ["schema", "api", "foreground-worker", "background-worker", "background-scheduler", "web", "migration"];
  const manifest = { version: 1, kind: "local-seed", revision: schema65BaselineRevision, tree: schema65BaselineTree, platform: "linux/arm64", images: Object.fromEntries(roles.map(role => [role, `sha256:${"a".repeat(64)}`])), provenance: { source_root: root, metadata: { backend: {}, web: {}, migration: {} } } };
  mkdirSync(join(root, "backend"));
  for (const file of ["backend/Dockerfile", "Dockerfile.web", "Dockerfile.postgres-maintenance"]) writeFileSync(join(root, file), "FROM example\n");
  for (const name of ["backend", "web", "migration"]) writeFileSync(join(root, `${name}-build.json`), "{}");
  const environment = { TEMPO_LIFECYCLE_SCHEMA65_ROOT: root, TEMPO_LIFECYCLE_SCHEMA65_IMAGES: file };
  const runGit = (_command, args) => ({ status: 0, stdout: args.includes("HEAD^{tree}") ? schema65BaselineTree : args.includes("HEAD") ? schema65BaselineRevision : args.includes("worktree") ? `worktree ${root}\nHEAD ${schema65BaselineRevision}\ndetached\n` : "" });
  writeFileSync(file, JSON.stringify(manifest));
  const options = { runGit, verifyBuildMaterials: () => ({}) };
  assert.equal(readSchema65Baseline(environment, options).root, root);
  for (const change of [{ revision: "bad" }, { tree: "bad" }, { images: { ...manifest.images, api: "sha256:" + "b".repeat(64) } }, { images: {} }]) {
    writeFileSync(file, JSON.stringify({ ...manifest, ...change }));
    assert.throws(() => readSchema65Baseline(environment, options), /manifest/);
  }
  writeFileSync(file, JSON.stringify(manifest));
  assert.throws(() => readSchema65Baseline(environment, { runGit, verifyBuildMaterials: () => ({ unexpected: "digest" }) }), /build materials differ/);
  rmSync(join(root, "backend-build.json"));
  assert.throws(() => readSchema65Baseline(environment, options), /build metadata is unavailable/);
  assert.throws(() => readSchema65Baseline({ ...environment, DOCKER_HOST: "override" }, { runGit }), /without overrides/);
  assert.throws(() => readSchema65Baseline(environment, { runGit: () => ({ status: 0, stdout: "wrong" }) }), /stale or dirty/);
});

test("current65 source refuses wrong schema and historical overrides before reading baseline", t => {
  const root = mkdtempSync(join(tmpdir(), "tempo-current65-preflight-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const ddl = join(root, "database/tempo_storage/ddl"); mkdirSync(ddl, { recursive: true });
  const path = join(ddl, "schema_version.py");
  for (const version of [63, 65, 67]) {
    writeFileSync(path, `POSTGRES_SCHEMA_VERSION = ${version}\n`);
    assert.throws(() => checkLifecycleSource({ root, profile: "current65", environment: {} }), /candidate schema 66/);
  }
  writeFileSync(path, "POSTGRES_SCHEMA_VERSION = 66\n");
  for (const name of ["TEMPO_LIFECYCLE_P0_59_ONLY", "TEMPO_PG_STALE_DELIVERY_ONLY", "TEMPO_LIFECYCLE_MANAGED_REHEARSAL", "TEMPO_LIFECYCLE_SHIPPING_REHEARSAL", "TEMPO_LIFECYCLE_SCHEMA62_ROOT", "TEMPO_LIFECYCLE_SCHEMA61_IMAGES"])
    assert.throws(() => checkLifecycleSource({ root, profile: "current65", environment: { [name]: "1" } }), /without historical/);
  assert.throws(() => checkLifecycleSource({ root, profile: "current65", environment: {} }), /absolute source and image/);
});

const revision = "4aa0ad1b731402365c1b866a6ba7bed484d2b6ac";
test("lifecycle preflight parses rendered interruption harness before resources", () => {
  assert.doesNotThrow(() => checkLifecycleSource({ root: "/temporary path", environment: {} }));
  assert.match(renderInterruptedLifecycle("/temporary path"), /interrupted|executeLifecycle/);
  assert.throws(() => checkLifecycleSource({ root: "/temporary path", environment: {}, render: () => "const broken = ;" }), /Invalid generated interruption harness/);
});
test("lifecycle preflight rejects missing, malformed, stale and incomplete explicit manifests", t => {
  const dir = mkdtempSync(join(tmpdir(), "tempo-preflight-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = join(dir, "images.json");
  const environment = { TEMPO_LIFECYCLE_SCHEMA61_IMAGES: file };
  assert.throws(() => checkLifecycleSource({ root: dir, environment }), /Cannot read historical image manifest/);
  writeFileSync(file, "{");
  assert.throws(() => checkLifecycleSource({ root: dir, environment }), /Cannot read historical image manifest/);
  writeFileSync(file, JSON.stringify({ revision: "stale", images: {} }));
  assert.throws(() => checkLifecycleSource({ root: dir, environment }), /Unexpected historical image revision/);
  writeFileSync(file, JSON.stringify({ revision, images: { api: "api", web: "web" } }));
  assert.throws(() => checkLifecycleSource({ root: dir, environment }), /Missing historical image for migration/);
  writeFileSync(file, JSON.stringify({ revision, images: { api: "api", web: "web", migration: "migration" } }));
  assert.doesNotThrow(() => checkLifecycleSource({ root: dir, environment }));
  assert.throws(() => checkLifecycleSource({ root: dir, environment: { TEMPO_LIFECYCLE_P0_59_ONLY: "1" } }), /schema-59 image manifest/);
});

test("timing labels distinguish inspection families without recording arguments", async () => {
  const { postgresCommandTiming } = await import("../../scripts/test-scenario-timings.mjs");
  for (const [args, label] of [
    [["image", "inspect", "secret-canary"], "image_inspection"],
    [["compose", "-p", "secret-canary", "config"], "compose_configuration"],
    [["volume", "ls", "secret-canary"], "volume_inspection"],
    [["context", "inspect", "secret-canary"], "daemon_context"],
    [["compose", "-p", "secret-canary", "ps"], "container_inventory"],
    [["inspect", "secret-canary"], "resource_inspection"],
  ]) assert.deepEqual(postgresCommandTiming(args), { label, category: "inspection" });
});


test("schema62 bridge refuses stale source and incomplete image mapping before mutation", t => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "tempo-bridge-"))); t.after(() => rmSync(root, { recursive: true, force: true }));
  const ddl = join(root, "database/tempo_legacy_database/ddl"); mkdirSync(ddl, { recursive: true });
  writeFileSync(join(ddl, "schema_version.py"), "POSTGRES_SCHEMA_VERSION = 62\n");
  const file = join(root, "images.json");
  const manifest = JSON.parse(readFileSync(new URL("../../config/lifecycle-images/schema62-linux-arm64.json", import.meta.url)));
  writeFileSync(file, JSON.stringify(manifest));
  const env = { TEMPO_LIFECYCLE_SCHEMA62_ROOT: root, TEMPO_LIFECYCLE_SCHEMA62_IMAGES: file };
  let dirty = false, detached = true;
  const runGit = (_cmd, args) => ({ status: 0, stdout: args.includes("worktree") ? `worktree ${root}\nHEAD ${schema62BridgeRevision}\n${detached ? "detached" : "branch refs/heads/main"}\n` : args.includes("status") ? (dirty ? "M unsafe" : "") : args.includes("HEAD^{tree}") ? schema62BridgeTree : schema62BridgeRevision });
  assert.equal(readSchema62Bridge(env, { runGit }).root, root);
  dirty = true; assert.throws(() => readSchema62Bridge(env, { runGit }), /stale or dirty/); dirty = false;
  detached = false; assert.throws(() => readSchema62Bridge(env, { runGit }), /registered detached/); detached = true;
  for (const [key, value] of [["version", 2], ["kind", "registry"]]) {
    const invalid = { ...manifest, [key]: value }; writeFileSync(file, JSON.stringify(invalid));
    assert.throws(() => readSchema62Bridge(env, { runGit }), /revision or roles/);
  }
  delete manifest.images.web; writeFileSync(file, JSON.stringify(manifest));
  assert.throws(() => readSchema62Bridge(env, { runGit }), /revision or roles/);
  manifest.images.web = "sha256:" + "2".repeat(64); manifest.images.foreign = "sha256:" + "3".repeat(64);
  writeFileSync(file, JSON.stringify(manifest)); assert.throws(() => readSchema62Bridge(env, { runGit }), /revision or roles/);
  assert.throws(() => readSchema62Bridge({}, { runGit }), /requires an absolute/);
});

test("schema62 bridge refuses changed project, daemon, volumes, ports, or source root", () => {
  const old = { root: "/old", project: "owned", context: "ctx", daemonId: "daemon", postgresVolumeKey: "pg", postgresMajor: 18,
    webUrl: "http://127.0.0.1:4000", volumes: { pg: { name: "owned_pg", external: false } }, ports: [{ host: "127.0.0.1", port: "4000" }] };
  const next = { ...old, root: "/new" }, roots = { previousRoot: "/old", currentRoot: "/new" };
  assert.doesNotThrow(() => assertBridgeTargetIdentity(old, next, roots));
  for (const change of [{ root: "/old" }, { root: "/foreign" }, { project: "other" }, { daemonId: "other" }, { context: "other" },
    { volumes: { pg: { name: "foreign" } } }, { ports: [] }])
    assert.throws(() => assertBridgeTargetIdentity(old, { ...next, ...change }, roots), /identity differs/);
});


test("schema63 bridge requires current candidate and fresh config binding", () => {
  const images = { api: "sha256:" + "1".repeat(64) }, fingerprint = "a".repeat(64), revision = "b".repeat(40);
  const prepared = { revision, images, configFingerprint: fingerprint };
  assert.doesNotThrow(() => assertBridgePreparedBinding(prepared, revision, images, fingerprint));
  assert.throws(() => assertBridgePreparedBinding({ ...prepared, configFingerprint: "c".repeat(64) }, revision, images, fingerprint), /binding differs/);
  assert.throws(() => assertBridgePreparedBinding({ ...prepared, images: { api: "wrong" } }, revision, images, fingerprint), /binding differs/);
  assert.throws(() => assertBridgePreparedBinding({ ...prepared, revision: "old" }, revision, images, fingerprint), /binding differs/);
});


test("schema63 managed schema59 rehearsal refuses before source or image acquisition", t => {
  const root = mkdtempSync(join(tmpdir(), "tempo-schema63-preflight-")); t.after(() => rmSync(root, { recursive: true, force: true }));
  const ddl = join(root, "database/tempo_legacy_database/ddl"); mkdirSync(ddl, { recursive: true });
  writeFileSync(join(ddl, "schema_version.py"), "POSTGRES_SCHEMA_VERSION = 63\n");
  for (const name of ["TEMPO_LIFECYCLE_MANAGED_REHEARSAL", "TEMPO_LIFECYCLE_SHIPPING_REHEARSAL"])
    assert.throws(() => checkLifecycleSource({ root, environment: { TEMPO_LIFECYCLE_P0_59_ONLY: "1", [name]: "1" } }), /not qualified/);
});


test("schema63 compatible repeat constructs a new runtime from the committed receipt", () => {
  const source = readFileSync(new URL("../../scripts/check-tempo-cli.mjs", import.meta.url), "utf8");
  const start = source.indexOf("      const repeat63 = currentCreateRuntime(");
  const end = source.indexOf('      console.log("PASS schema-62 to schema-63', start);
  assert(start >= 0 && end > start);
  const repeat = source.slice(start, end);
  assert.match(repeat, /previous: record/);
  assert.match(repeat, /preparedImages: currentPrepared/);
  assert.match(repeat, /await repeat63\.inspectTarget\(\)/);
  assert.match(repeat, /currentExecuteLifecycle\(\{ recreate: false \}, repeat63\)/);
  assert.match(repeat, /call\.args\.includes\("build"\)/);
  assert.match(repeat, /call\.args\.includes\("stop"\)/);
  assert.match(repeat, /!call\.args\.includes\("--check"\)/);
  assert.match(repeat, /operations\/schema63-bridge-review/);
});


test("schema62 bridge refuses Docker host or TLS overrides before Git", () => {
  for (const name of ["DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"]) {
    let called = false;
    assert.throws(() => readSchema62Bridge({ [name]: "override", TEMPO_LIFECYCLE_SCHEMA61_IMAGES: "/explicit" },
      { runGit: () => { called = true; throw new Error("Git must not run"); } }), /without host or TLS overrides/);
    assert.equal(called, false);
  }
});

test("schema62 owned handoff stops and removes containers but preserves volume and guard identities", async () => {
  const source = readFileSync(new URL("../../scripts/check-tempo-cli.mjs", import.meta.url), "utf8");
  const start = source.indexOf("export async function handoffOwnedSchema62(");
  const end = source.indexOf("\nexport function validateLifecycleProfile(", start);
  assert(start >= 0 && end > start);
  const handoff = runInNewContext(`(${source.slice(start, end).replace("export async function", "async function")})`, { assert });
  const run = async ({ changedVolume = false, changedGuard = false, remaining = false } = {}) => {
    const events = []; let reads = 0, volumeReads = 0;
    const options = { oldRuntime: { inspectTarget: async () => events.push("inspect"), stopApplications: async () => events.push("stop") },
      readEvidence: () => ({ deployment: "same", guard: changedGuard && reads++ ? "changed" : "same" }),
      snapshotVolumes: async () => { events.push("volumes"); return [{ Name: changedVolume && volumeReads++ ? "foreign" : "owned", Driver: "local" }]; },
      down: async () => events.push("down"), listContainers: async () => { events.push("containers"); return remaining ? ["owned-container"] : []; } };
    return { events, result: handoff(options) };
  };
  const passing = await run(); await passing.result;
  assert.deepEqual(passing.events, ["inspect", "volumes", "stop", "down", "containers", "volumes"]);
  for (const change of [{ changedVolume: true }, { changedGuard: true }, { remaining: true }]) {
    const failing = await run(change); await assert.rejects(failing.result);
  }
});


test("native schema64 refuses historical lifecycle profiles before rendering or acquiring images", t => {
  const root = mkdtempSync(join(tmpdir(), "tempo-native64-preflight-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const ddl = join(root, "database/tempo_storage/ddl"); mkdirSync(ddl, { recursive: true });
  writeFileSync(join(ddl, "schema_version.py"), "POSTGRES_SCHEMA_VERSION = 64\n");
  for (const profile of ["full", "current63"]) {
    let rendered = false;
    assert.throws(() => checkLifecycleSource({ root, profile,
      environment: { TEMPO_LIFECYCLE_SCHEMA61_IMAGES: "/must-not-read", TEMPO_LIFECYCLE_SCHEMA62_ROOT: "/must-not-check" },
      render: () => { rendered = true; throw new Error("renderer must not run"); },
    }), /Schema-64 lifecycle upgrade is not qualified/);
    assert.equal(rendered, false);
  }
});

test("native schema65 refuses the historical lifecycle profile before acquiring resources", t => {
  const root = mkdtempSync(join(tmpdir(), "tempo-native65-preflight-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const ddl = join(root, "database/tempo_storage/ddl"); mkdirSync(ddl, { recursive: true });
  writeFileSync(join(ddl, "schema_version.py"), "POSTGRES_SCHEMA_VERSION = 65\n");
  let rendered = false;
  assert.throws(() => checkLifecycleSource({root, profile: "full", environment: {},
    render: () => { rendered = true; }}), /Schema-65 lifecycle upgrade is not qualified/);
  assert.equal(rendered, false);
});
