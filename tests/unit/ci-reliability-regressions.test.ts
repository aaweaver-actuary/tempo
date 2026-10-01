// @vitest-environment node
import { spawnSync } from "node:child_process";
import { expect, it } from "vitest";
it("CI reliability planning and mandatory gating regressions pass in the regular suite", () => {
  const result = spawnSync(process.execPath, ["--test", "tests/runner/ci-reliability.test.mjs"], { encoding: "utf8" });
  expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
}, 30_000);
