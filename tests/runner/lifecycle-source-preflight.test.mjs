import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { checkLifecycleSource, renderInterruptedLifecycle } from "../../scripts/lifecycle-source-preflight.mjs";

const revision = "4aa0ad1b731402365c1b866a6ba7bed484d2b6ac";
test("lifecycle preflight parses rendered interruption harness before resources", () => {
  assert.doesNotThrow(() => checkLifecycleSource({ root: "/temporary path", environment: {} }));
  assert.match(renderInterruptedLifecycle("/temporary path"), /interrupted|executeLifecycle/);
  assert.throws(() => checkLifecycleSource({ environment: {}, render: () => "const broken = ;" }), /Invalid generated interruption harness/);
});
test("lifecycle preflight rejects missing, malformed, stale and incomplete explicit manifests", t => {
  const dir = mkdtempSync(join(tmpdir(), "tempo-preflight-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = join(dir, "images.json");
  const environment = { TEMPO_LIFECYCLE_SCHEMA61_IMAGES: file };
  assert.throws(() => checkLifecycleSource({ environment }), /Cannot read historical image manifest/);
  writeFileSync(file, "{");
  assert.throws(() => checkLifecycleSource({ environment }), /Cannot read historical image manifest/);
  writeFileSync(file, JSON.stringify({ revision: "stale", images: {} }));
  assert.throws(() => checkLifecycleSource({ environment }), /Unexpected historical image revision/);
  writeFileSync(file, JSON.stringify({ revision, images: { api: "api", web: "web" } }));
  assert.throws(() => checkLifecycleSource({ environment }), /Missing historical image for migration/);
  writeFileSync(file, JSON.stringify({ revision, images: { api: "api", web: "web", migration: "migration" } }));
  assert.doesNotThrow(() => checkLifecycleSource({ environment }));
  assert.throws(() => checkLifecycleSource({ environment: { TEMPO_LIFECYCLE_P0_59_ONLY: "1" } }), /schema-59 image manifest/);
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
