import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

// Each real command scenario gets its own existing deadline. Fake processes
// remain serial; a larger aggregate group must not exceed that deadline as
// safety cases grow. Prefixes are disjoint and cover every actual CLI case.
const regressionGroups = [
  { name: "contracts", pattern: "^(?:restart|CLI(?! automatic)|blocked updates|release evidence|target maintenance|deployment records|start after merge|changed dependency|dependency recreation|repeated compatible|failed|source update)" },
  ...["start waits", "start timeout preserves", "start timeout validates", "migrate timeout", "start cancels", "start relaunch"].map(name => ({ name: `automatic ${name}`, pattern: `^CLI automatic ${name}` })),
  ...["migration guard partial", "migration guard completed", "migration guard newer", "migration guard interrupted",
    "migration diagnostics applying", "migration diagnostics failed", "migration diagnostics invalid", "migration diagnostics verified",
    "migration diagnostics pending", "migration diagnostics retry", "migration diagnostics normal",
    "upgrades", "dependency startup", "falls back", "read-only", "failed migration", "failed builds",
    "backup restores the prior", "blocked update", "backup restores stopped", "detects", "fallback corrects",
    "compatible fallback", "interrupted fallback", "concurrent", "backup rejects", "diagnostics explain", "diagnostics keep", "diagnostics identify",
    "diagnostics report", "diagnostics distinguish", "diagnostics retain", "diagnostics preserve", "diagnostic safety"].map(name => ({ name, pattern: `^actual CLI ${name}` })),
];

it("Every named CLI Node regression has exactly one nonempty regular-suite group", () => {
  const runnerSource = readFileSync("tests/runner/tempo-cli.test.mjs", "utf8");
  // Template titles vary only after their stable behavior prefix.
  const declaredTitles = [...runnerSource.matchAll(/(?<!\.)\btest\(\s*(["'`])(.*?)\1/g)].map(match => match[2]);
  expect(declaredTitles.length).toBeGreaterThan(0);
  expect(declaredTitles.length, "Each Node case needs a named literal/template behavior prefix").toBe([...runnerSource.matchAll(/(?<!\.)\btest\(/g)].length);
  for (const title of declaredTitles) expect(regressionGroups.filter(group => new RegExp(group.pattern).test(title)), title).toHaveLength(1);
  for (const group of regressionGroups) expect(declaredTitles.some(title => new RegExp(group.pattern).test(title)), group.name).toBe(true);
});

it.each(regressionGroups)("Tempo lifecycle CLI safety regressions run in the regular suite: $name", ({ pattern }) => {
  const result = spawnSync(process.execPath, ["--test", "--test-reporter=tap", `--test-name-pattern=${pattern}`, "tests/runner/tempo-cli.test.mjs"], { encoding: "utf8" });
  expect(result.status, result.stdout + result.stderr).toBe(0);
  expect(result.stdout, "The selected CLI group must execute at least one passing case").toMatch(/# pass [1-9]\d*\b/);
}, 60_000);
