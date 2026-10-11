// @vitest-environment node
import { spawnSync } from "node:child_process";
import { expect, it } from "vitest";

it("study_worker_regular_gate_discovers_investigation_cohort_accounting_regression", () => {
  const result = spawnSync(process.execPath, ["--test", "tests/runner/study-worker-investigation-statistics.test.mjs"], {
    cwd: process.cwd(), encoding: "utf8", timeout: 10_000,
  });
  expect(result.error).toBeUndefined();
  expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
  expect(result.stdout).toContain("study_worker_statistics_exclude_contaminated_attempts_warmups_and_probes_without_reducing_paired_counts");
  expect(result.stdout).toMatch(/(?:#|ℹ) tests 1/);
});
