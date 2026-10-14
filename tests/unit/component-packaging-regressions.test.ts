// @vitest-environment node
import { spawnSync } from "node:child_process";
import { expect, it } from "vitest";
it("M02 component packaging negative regressions run in their regular owner", () => {
  const result = spawnSync(process.execPath, ["tools/component-build/test-node.mjs", "tools/component-build/tests"], { encoding: "utf8" });
  expect(result.status, result.stdout + result.stderr).toBe(0);
  const report = JSON.parse(result.stdout.trim().split("\n").at(-1)!);
  expect(report.passed).toBeGreaterThan(0);
  expect(report.executedIdentities).toHaveLength(report.passed);
  expect(report.failed).toBe(0);
}, 60_000);
