// @vitest-environment node
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { it } from "vitest";

// Run the same dependency-free behavioral suite locally and in the regular
// Vitest gate. Node's TAP output preserves the individual regression names.
it("PostgreSQL test speedups preserve full coverage, failure propagation, and disposable cleanup", () => {
  const result = spawnSync(process.execPath, ["--test", "tests/runner/postgres-test-speedups.test.mjs"], {
    cwd: process.cwd(), encoding: "utf8", timeout: 30_000,
  });
  assert.ifError(result.error);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
}, 35_000);
