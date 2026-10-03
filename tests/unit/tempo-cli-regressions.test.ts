import { spawnSync } from "node:child_process";
import { expect, it } from "vitest";

// Disjoint process groups retain every case without increasing timeouts or
// running multiple command fixtures concurrently as the safety coverage grows.
it.each([
  { name: "contracts", pattern: "^(?:restart|CLI|blocked updates|release evidence|target maintenance|deployment records|start after merge|changed dependency|dependency recreation|repeated compatible|failed|source update)" },
  { name: "startup", pattern: "^actual CLI (?!(?:backup|blocked|detects|fallback|compatible|concurrent))" },
  { name: "maintenance and recovery", pattern: "^actual CLI (?:backup|blocked|detects|fallback|compatible|concurrent)" },
])("Tempo lifecycle CLI safety regressions run in the regular suite: $name", ({ pattern }) => {
  const result = spawnSync(process.execPath, ["--test", `--test-name-pattern=${pattern}`, "tests/runner/tempo-cli.test.mjs"], { encoding: "utf8" });
  expect(result.status, result.stdout + result.stderr).toBe(0);
}, 60_000);
