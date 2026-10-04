import { spawnSync } from "node:child_process";
import { expect, it } from "vitest";

// Each real command scenario gets its own existing deadline. Fake processes
// remain serial; a larger aggregate group must not exceed that deadline as
// safety cases grow. Prefixes are disjoint and cover every actual CLI case.
it.each([
  { name: "contracts", pattern: "^(?:restart|CLI|blocked updates|release evidence|target maintenance|deployment records|start after merge|changed dependency|dependency recreation|repeated compatible|failed|source update)" },
  ...["migration guard partial", "migration guard completed", "migration guard newer", "migration guard interrupted",
    "upgrades", "dependency startup", "falls back", "read-only", "failed migration", "failed builds",
    "backup restores the prior", "blocked update", "backup restores stopped", "detects", "fallback corrects",
    "compatible fallback", "interrupted fallback", "concurrent", "backup rejects"].map(name => ({ name, pattern: `^actual CLI ${name}` })),
])("Tempo lifecycle CLI safety regressions run in the regular suite: $name", ({ pattern }) => {
  const result = spawnSync(process.execPath, ["--test", `--test-name-pattern=${pattern}`, "tests/runner/tempo-cli.test.mjs"], { encoding: "utf8" });
  expect(result.status, result.stdout + result.stderr).toBe(0);
}, 60_000);
