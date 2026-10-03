import { spawnSync } from "node:child_process";
import { expect, it } from "vitest";

// This suite exercises the actual executable and many discrete Git/Docker
// processes. Its measured macOS duration is 37 seconds, beyond the DOM default.
it("Tempo lifecycle CLI safety regressions run in the regular suite", () => {
  const result = spawnSync(process.execPath, ["--test", "tests/runner/tempo-cli.test.mjs"], { encoding: "utf8" });
  expect(result.status, result.stdout + result.stderr).toBe(0);
}, 60_000);
