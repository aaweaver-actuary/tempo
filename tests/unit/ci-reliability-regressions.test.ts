// @vitest-environment node
import { spawnSync } from "node:child_process";
import { expect, it } from "vitest";
it("committed merge-conflict guard regressions pass in the regular suite", () => {
  const result = spawnSync(process.execPath, ["--test", "tests/runner/merge-conflict-guard.test.mjs"], { encoding: "utf8" });
  expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
  expect(result.stdout).toContain("committed conflict guard detects Python and JSON integration conflicts with line diagnostics");
  expect(result.stdout).toMatch(/(?:#|ℹ) tests 5/);
});
it("CI reliability planning and browser selection regressions pass in the regular suite", () => {
  const result = spawnSync(process.execPath, ["--test", "tests/runner/ci-reliability.test.mjs"], { encoding: "utf8" });
  expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
  expect(result.stdout).toContain("demoted critical cases remain required by their complete browser families");
  expect(result.stdout).toContain("documented global browser smoke count and titles match inventory and real collection");
}, 30_000);

it("draft development and merge qualification regressions execute nonzero cases", () => {
  const result = spawnSync(process.execPath, ["--test", "tests/runner/ci-development.test.mjs"], { encoding: "utf8" });
  expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
  expect(result.stdout).toContain("draft ready and converted-to-draft transitions separate execution from qualification");
  expect(result.stdout).toMatch(/(?:#|ℹ) tests [1-9][0-9]*/);
}, 30_000);

it("four isolated browser shards retain exact qualification and reuse regressions", () => {
  const result = spawnSync(process.execPath, ["--test", "tests/runner/ci-browser-shards.test.mjs"], { encoding: "utf8" });
  expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
  expect(result.stdout).toContain("actual Playwright shard selectors collect the exact immutable partition");
  expect(result.stdout).toMatch(/(?:#|ℹ) tests [1-9][0-9]*/);
}, 30_000);
