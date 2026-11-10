import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { lifecycleFixtureRevision as revision, prepareLifecycleImages, validateLifecycleImageLock } from "../../scripts/prepare-lifecycle-images.mjs";

const roles = ["api", "web", "migration"];
const id = n => `sha256:${String(n).repeat(64)}`;
const makeLock = (kind = "registry") => ({ version: 1, kind, revision, platform: "linux/arm64",
  provenance: { kind: "built", source_revision: revision, receipt: "https://github.com/owner/repo/issues/1" },
  images: Object.fromEntries(roles.map((role, i) => [role, { expected_local_image_id: id(i + 1),
    ...(kind === "registry" ? { reference: `ghcr.io/owner/${role}@${id(i + 4)}` } : {}) }])) });
function fixture(t, lock = makeLock()) {
  const dir = mkdtempSync(join(tmpdir(), "tempo-cache-test-")); t.after(() => rmSync(dir, { recursive: true, force: true }));
  const outputPath = join(dir, "images.json");
  const calls = [], available = new Set(roles.map(role => lock.images[role].expected_local_image_id));
  const run = async args => {
    calls.push(args); assert.deepEqual(args.slice(0, 2), ["--context", "owned-context"]);
    if (args[2] === "info") return { status: 0, stdout: "owned-daemon\n" };
    if (args[2] === "pull") { const role = roles.find(r => lock.images[r].reference === args.at(-1)); available.add(lock.images[role].expected_local_image_id); return { status: 0 }; }
    const role = roles.find(r => [lock.images[r].expected_local_image_id, lock.images[r].reference].includes(args.at(-1)));
    const config = lock.images[role].expected_local_image_id;
    if (!available.has(config)) return { status: 1, stderr: "Error: No such image" };
    return { status: 0, stdout: JSON.stringify([{ Id: config, Os: "linux", Architecture: "arm64", Config: { Labels: { "org.opencontainers.image.revision": revision } } }]) };
  };
  return { lock, calls, available, outputPath, run, prepare: (override = {}) => prepareLifecycleImages({ lock, context: "owned-context", outputPath, run, ...override }) };
}
test("warm historical images produce manifest without pull or build on selected context", async t => {
  const f = fixture(t); const evidence = await f.prepare();
  assert.deepEqual(JSON.parse(readFileSync(f.outputPath)), { revision, images: Object.fromEntries(roles.map(r => [r, f.lock.images[r].expected_local_image_id])) });
  assert.equal(f.calls.length, 4); assert(f.calls.every(c => !c.includes("pull") && !c.includes("build")));
  assert.equal(evidence.daemon_id, "owned-daemon"); assert.equal(evidence.roles.api.acquisition, "hit");
});
test("missing registry image pulls only pinned reference and verifies its local image identity", async t => {
  const f = fixture(t); f.available.delete(f.lock.images.web.expected_local_image_id);
  const evidence = await f.prepare(); const pulls = f.calls.filter(c => c.includes("pull"));
  assert.equal(pulls.length, 1); assert.equal(pulls[0].at(-1), f.lock.images.web.reference);
  assert.equal(evidence.roles.web.acquisition, "pull");
  assert(f.calls.some(c => c.includes("inspect") && c.at(-1) === f.lock.images.web.reference));
});
test("malformed partial wrong-revision and daemon-failure inspections refuse without acquisition", async t => {
  for (const response of [
    { status: 0, stdout: "{" },
    { status: 0, stdout: "[]" },
    { status: 0, stdout: JSON.stringify([{ Id: id(1), Os: "linux", Architecture: "arm64", Config: { Labels: { "org.opencontainers.image.revision": "wrong" } } }]) },
    { status: 1, stderr: "Cannot connect to the Docker daemon" },
  ]) {
    const f = fixture(t);
    await assert.rejects(f.prepare({ run: async args => args[2] === "info" ? f.run(args) : (f.calls.push(args), response) }));
    assert(!f.calls.some(c => c.includes("pull")));
  }
});
test("invalid lock fails before Docker and local seed never pulls", async t => {
  for (const change of [l => l.revision = "wrong", l => l.platform = "linux/amd64", l => delete l.images.web,
    l => l.images.api.reference = "ghcr.io/owner/api:latest", l => l.provenance.source_revision = "wrong"]) {
    const lock = makeLock(); change(lock); assert.throws(() => validateLifecycleImageLock(lock));
  }
  const f = fixture(t, makeLock("local-seed")); f.available.delete(f.lock.images.web.expected_local_image_id);
  await assert.rejects(f.prepare(), /Missing local historical web/); assert(!f.calls.some(c => c.includes("pull")));
});
test("inspection mismatch and pull failure never publish partial manifest", async t => {
  const f = fixture(t); writeFileSync(f.outputPath, "previous");
  await assert.rejects(f.prepare({ run: async args => {
    const result = await f.run(args); if (args.at(-1) === f.lock.images.web.expected_local_image_id && result.status === 0) {
      const images = JSON.parse(result.stdout); images[0].Architecture = "amd64"; result.stdout = JSON.stringify(images);
    } return result;
  }}), /identity or platform mismatch/);
  assert.equal(readFileSync(f.outputPath, "utf8"), "previous");
  f.available.delete(f.lock.images.migration.expected_local_image_id);
  await assert.rejects(f.prepare({ run: async args => args.includes("pull") ? { status: 1 } : f.run(args) }), /pull failed/);
  assert.equal(readFileSync(f.outputPath, "utf8"), "previous");
});
test("normal lifecycle parent acquires before candidate construction and never consumes a failed manifest", async () => {
  const source = readFileSync(new URL("../../database/tests/test-postgres-docker.mjs", import.meta.url), "utf8");
  const start = source.indexOf("  image_build: async () => {");
  const end = source.indexOf("  maintenance_cli: async () => {", start);
  for (const fail of [false, true]) {
    const calls = [], environment = {};
    const context = { stages: ["deployment_lifecycle"], environment, assert, join, candidateMode: undefined, borrowedCandidate: null, candidateMetadataPath: null,
      waitForStudyQuiet: async () => calls.push("quiet"),
      spawnSync: () => ({ status: 0, stdout: "owned-context\n" }),
      secretsDirectory: "/owned/secrets", readFileSync: () => JSON.stringify(makeLock("local-seed")),
      prepareLifecycleImages: async request => {
        calls.push("acquire"); assert.equal(request.context, "owned-context");
        if (fail) throw new Error("acquisition refused"); return { roles: {} };
      },
      atomicJson: () => {}, project: "owned", recordBudgetResource: async () => {}, resourcesCreated: false,
      managedComposeBuild: async () => calls.push("build"), options: { mode: "lifecycle" }, compose: [], run: () => {},
    };
    const actions = runInNewContext(`({${source.slice(start, end)}})`, context);
    if (fail) {
      await assert.rejects(actions.image_build(), /acquisition refused/);
      assert.equal(environment.TEMPO_LIFECYCLE_SCHEMA61_IMAGES, undefined);
      assert.deepEqual(calls, ["quiet", "acquire"]);
    } else {
      await actions.image_build();
      assert.equal(environment.TEMPO_LIFECYCLE_SCHEMA61_IMAGES, "/owned/secrets/schema61-images.json");
      assert.deepEqual(calls, ["quiet", "acquire", "build"]);
    }
  }
});
test("automatic historical path refuses inherited host overrides before resources while explicit fixtures keep compatibility", () => {
  const source = readFileSync(new URL("../../database/tests/test-postgres-docker.mjs", import.meta.url), "utf8");
  const start = source.indexOf('if (stages.includes("deployment_lifecycle")');
  const end = source.indexOf('// Docker Desktop', start);
  assert(start >= 0 && end < source.indexOf('const secretsDirectory ='));
  for (const name of ["DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"]) {
    const env = { [name]: "inherited-override" };
    assert.throws(() => runInNewContext(source.slice(start, end), {
      stages: ["deployment_lifecycle"], options: { mode: "lifecycle" }, process: { env }, resolve, checkLifecycleSource: () => {},
    }), /does not support inherited Docker host or TLS overrides/);
    for (const bypass of [{ TEMPO_LIFECYCLE_SCHEMA61_IMAGES: "/explicit/images.json" }, { TEMPO_LIFECYCLE_P0_59_ONLY: "1" }]) {
      assert.doesNotThrow(() => runInNewContext(source.slice(start, end), {
        stages: ["deployment_lifecycle"], options: { mode: "lifecycle" }, process: { env: { ...env, ...bypass } }, resolve, checkLifecycleSource: () => {},
      }));
    }
  }
});

test("missing schema62 bridge image refuses before candidate construction or lifecycle mutation", async () => {
  const source = readFileSync(new URL("../../database/tests/test-postgres-docker.mjs", import.meta.url), "utf8");
  const start = source.indexOf("  image_build: async () => {");
  const end = source.indexOf("  maintenance_cli: async () => {", start);
  const calls = [];
  const context = {
    stages: ["deployment_lifecycle"], environment: { TEMPO_LIFECYCLE_SCHEMA62_ROOT: "/owned/62", TEMPO_LIFECYCLE_SCHEMA61_IMAGES: "/owned/61.json" },
    assert, waitForStudyQuiet: async () => calls.push("quiet"),
    spawnSync: () => ({ status: 0, stdout: "owned-context\n" }),
    readSchema62Bridge: () => ({ manifest: { images: { api: id(1) } } }),
    inspectImmutableImageSet: async () => { calls.push("bridge-inspect"); throw new Error("Bridge image unavailable"); },
    managedComposeBuild: () => { calls.push("build"); }, resourcesCreated: false,
    options: { mode: "lifecycle" }, candidateMode: null, borrowedCandidate: null,
  };
  await assert.rejects(runInNewContext(`({${source.slice(start, end)}})`, context).image_build(), /Bridge image unavailable/);
  assert.deepEqual(calls, ["quiet", "bridge-inspect"]);
  assert.equal(context.resourcesCreated, false);
});
