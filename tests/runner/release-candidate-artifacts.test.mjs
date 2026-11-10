import assert from "node:assert/strict";
import { test } from "node:test";
import { createCandidateReceipt, verifyCandidateReceipt } from "../../scripts/release-candidate-artifacts.mjs";
const roles = ["schema", "api", "foreground-worker", "background-worker", "background-scheduler", "web", "maintenance"];
const id = n => `sha256:${String(n).repeat(64)}`;
const identity = () => ({ source: { revision: "a".repeat(40), tree: "b".repeat(40), clean: true },
  context: "desktop-linux", daemon_id: "daemon-1", platform: "linux/arm64",
  recipes: Object.fromEntries(roles.map(role => [role, { context: ".", dockerfile: role === "web" ? "Dockerfile.web" : "backend/Dockerfile", args: { FEATURE: "one" } }])),
  build_dependencies: { python: id(1), rust: id(2), node: id(3), nginx: id(4) }, runtime_dependencies: { postgres: id(5), redis: id(6) } });
const images = () => Object.fromEntries(roles.map((role, i) => [role, id(i + 1)]));
const inspect = async image => ({ Id: image, Os: "linux", Architecture: "arm64", Config: { Labels: { "org.opencontainers.image.revision": "a".repeat(40) } } });
const prepare = () => { const observed = identity(); return { receipt: createCandidateReceipt({ owner: "attempt-1", identity: observed, images: images() }), owner: "attempt-1", observed: structuredClone(observed), inspect }; };
test("candidate receipt verifies same clean recipe independently of key order", async () => {
  const state = prepare(); state.observed.runtime_dependencies = { redis: id(6), postgres: id(5) };
  assert.deepEqual(await verifyCandidateReceipt(state), state.receipt.images);
});
test("candidate receipt rejects changed source, args, dependencies, daemon, platform, and owner", async () => {
  for (const mutate of [
    s => s.observed.source.clean = false, s => s.observed.source.revision = "c".repeat(40), s => s.observed.source.tree = "c".repeat(40),
    s => s.observed.recipes.api.args.FEATURE = "two", s => s.observed.build_dependencies.python = id(9),
    s => s.observed.runtime_dependencies.redis = id(9), s => s.observed.daemon_id = "other", s => s.observed.context = "other",
    s => s.observed.platform = "linux/amd64", s => s.owner = "other", s => delete s.receipt.images.web,
  ]) { const state = prepare(); mutate(state); await assert.rejects(verifyCandidateReceipt(state)); }
});
test("candidate receipt refuses missing or changed images and incomplete build facts", async () => {
  const state = prepare();
  await assert.rejects(verifyCandidateReceipt({ ...state, inspect: async () => null }), /unavailable or mismatched/);
  await assert.rejects(verifyCandidateReceipt({ ...state, inspect: async image => ({ ...(await inspect(image)), Architecture: "amd64" }) }), /unavailable or mismatched/);
  await assert.rejects(verifyCandidateReceipt({ ...state, inspect: async image => ({ ...(await inspect(image)), Id: id(9) }) }), /unavailable or mismatched/);
  const incomplete = identity(); incomplete.build_dependencies = {}; assert.throws(() => createCandidateReceipt({ owner: "attempt-1", identity: incomplete, images: images() }), /build dependency/);
});

test("build metadata binds complete literal FROM materials and output", async () => {
  const { buildMaterials } = await import("../../scripts/release-candidate-artifacts.mjs");
  const dockerfile = "FROM rust:1.91-slim AS core\nFROM node:22-slim AS web\nFROM nginx:1.28-alpine\n";
  const metadata = { "containerimage.descriptor": { digest: id(1) }, "buildx.build.provenance": { invocation: { environment: { platform: "linux/arm64" } },
    materials: ["nginx@1.28-alpine", "node@22-slim", "rust@1.91-slim"].map((v, i) => ({ uri: `pkg:docker/${v}?platform=linux%2Farm64`, digest: { sha256: String(i + 2).repeat(64) } })) } };
  assert.equal(Object.keys(buildMaterials(metadata, { dockerfile, platform: "linux/arm64", expectedImageId: id(1) })).length, 3);
  for (const mutate of [m => m["buildx.build.provenance"].materials.pop(), m => m["containerimage.descriptor"].digest = id(9), m => m["buildx.build.provenance"].invocation.environment.platform = "linux/amd64"]) {
    const changed = structuredClone(metadata); mutate(changed); assert.throws(() => buildMaterials(changed, { dockerfile, platform: "linux/arm64", expectedImageId: id(1) }));
  }
  assert.throws(() => buildMaterials(metadata, { dockerfile: "ARG BASE\nFROM $BASE", platform: "linux/arm64", expectedImageId: id(1) }), /dynamic/);
});

test("candidate Bake normalization excludes only verified project aliases and labels", async () => {
  const { normalizeCandidateBake } = await import("../../scripts/release-candidate-artifacts.mjs");
  const names = roles.filter(role => role !== "maintenance");
  const bake = project => ({ target: Object.fromEntries(names.map(name => [name, { context: ".", dockerfile: "backend/Dockerfile", tags: [`${project}-${name}:latest`], labels: { "com.docker.compose.project": project, custom: "kept" }, args: { important: "kept" } }])) });
  assert.deepEqual(normalizeCandidateBake(bake("first"), "first"), normalizeCandidateBake(bake("second"), "second"));
  const changed = bake("first"); changed.target.api.args.important = "changed";
  assert.notDeepEqual(normalizeCandidateBake(changed, "first"), normalizeCandidateBake(bake("first"), "first"));
  changed.target.api.tags = ["foreign:latest"]; assert.throws(() => normalizeCandidateBake(changed, "first"), /Unexpected/);
});

test("parent borrower skips construction and creates suite-owned aliases", async () => {
  const { readFileSync } = await import("node:fs");
  const { runInNewContext } = await import("node:vm");
  const source = readFileSync(new URL("../../database/tests/test-postgres-docker.mjs", import.meta.url), "utf8");
  const start = source.indexOf("  image_build: async () => {");
  const end = source.indexOf("  maintenance_cli: async () => {", start);
  const calls = [], borrowed = images();
  const context = { candidateMode: "borrow", candidateIdentity: null, candidateOwner: "attempt-12345", candidateReceiptPath: "/owned/receipt", candidateMetadataPath: null,
    borrowedCandidate: null, candidateRoles: roles.filter(r => r !== "maintenance"), candidateSuiteTags: [], maintenanceImageCleanupRequired: false,
    stages: [], environment: {}, options: { mode: "browser" }, project: "suite-1", maintenanceImage: "suite-1-maintenance", compose: [], resourcesCreated: false,
    waitForStudyQuiet: async () => {}, candidatePrepareIdentity: () => identity(), candidateRuntimeDependencies: () => identity().runtime_dependencies,
    readFileSync: () => JSON.stringify({ identity: identity(), images: borrowed }), verifyCandidateReceipt: async () => borrowed,
    inspectImmutableImageSet: async () => new Map(),
    assertCandidateTagAbsent: tag => calls.push(["absence", tag]), recordBudgetResource: async () => {},
    run: (command, args) => calls.push([command, ...args]), managedComposeBuild: async () => { throw new Error("unexpected build"); },
  };
  const actions = runInNewContext(`({${source.slice(start, end)}})`, context);
  await actions.image_build();
  assert.equal(calls.filter(call => call[1] === "tag").length, 7);
  assert.equal(context.resourcesCreated, true); assert.equal(context.maintenanceImageCleanupRequired, true);
  assert.equal(context.candidateSuiteTags.length, 6);
});

test("release owner cleanup removes only validated owned aliases and refuses mismatch", async () => {
  const { cleanupCandidateTags } = await import("../../scripts/release-candidate-artifacts.mjs");
  const receipt = createCandidateReceipt({ owner: "attempt-12345", identity: identity(), images: images() });
  const removed = [], args = { receipt, owner: "attempt-12345", context: "desktop-linux", daemonId: "daemon-1",
    inspectTag: async tag => ({ Id: receipt.images[roles.find(role => tag === `tempo-release-attempt-12345-${role}`)] }),
    removeTag: async tag => removed.push(tag) };
  await assert.rejects(cleanupCandidateTags({ ...args, daemonId: "different" }), /daemon differs/);
  await assert.rejects(cleanupCandidateTags({ ...args, inspectTag: async () => ({ Id: id(9) }) }), /tag mismatch/);
  assert.equal(removed.length, 0);
  await cleanupCandidateTags(args); assert.equal(removed.length, 7);
});
