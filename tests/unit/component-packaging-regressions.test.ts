// @vitest-environment node
import { spawnSync } from "node:child_process";
import { expect, it } from "vitest";
it("M02 component packaging negative regressions run in their regular owner", () => {
  const result = spawnSync(process.execPath, ["tools/component-build/test-node.mjs", "tools/component-build/tests"], { encoding: "utf8" });
  expect(result.status, result.stdout + result.stderr).toBe(0);
  expect(result.stdout).toMatch(/# pass [1-9]\d*/);
}, 60_000);
