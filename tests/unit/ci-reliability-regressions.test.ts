// @vitest-environment node
import { spawnSync } from "node:child_process";
import { expect, it } from "vitest";
it("CI reliability planning and browser selection regressions pass in the regular suite", () => {
  const result = spawnSync(process.execPath, ["--test", "tests/runner/ci-reliability.test.mjs"], { encoding: "utf8" });
  expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
  expect(result.stdout).toContain("demoted critical cases remain required by their complete browser families");
  expect(result.stdout).toContain("documented global browser smoke count and titles match inventory and real collection");
}, 30_000);
