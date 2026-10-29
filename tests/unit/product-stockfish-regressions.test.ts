// @vitest-environment node
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { accountNodeEvents } from "../../tools/component-build/test-node.mjs";

type Report = Omit<ReturnType<typeof accountNodeEvents>, "executedIdentities"> & { executedIdentities: string[] };
const files = ["tests/runner/product-stockfish.test.mjs", "tests/runner/public-surface-stockfish.test.mjs"];
function requireOrdinaryEvidence(report: Report) {
  expect(report.success).toBe(true);
  expect(report.passed).toBeGreaterThan(0);
  expect(report.failed).toBe(0);
  expect(report.skipped).toBe(0);
  expect(report.todo).toBe(0);
  expect(report.executedIdentities).toHaveLength(report.passed);
  expect(new Set(report.executedIdentities).size).toBe(report.passed);
  for (const file of files)
    expect(report.executedIdentities.some(identity => identity.startsWith(resolve(file) + ":")), file).toBe(true);
}

// Only ordinary fake/public cases. Neither real smoke is invoked.
it("bounded Stockfish fake evaluator and public surface suites", () => {
  const environment = { ...process.env };
  delete environment.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, ["--test",
    "--test-reporter=" + resolve("tools/component-build/node-test-reporter.mjs"),
    ...files.map(file => resolve(file)),
  ], { cwd: process.cwd(), env: environment, encoding: "utf8", timeout: 20_000, maxBuffer: 16 * 1024 * 1024 });
  const diagnostic = `${result.error?.message ?? ""}\n${result.stdout ?? ""}\n${result.stderr ?? ""}`;
  expect(result.error, diagnostic).toBeUndefined();
  expect(result.status, diagnostic).toBe(0);
  const report = accountNodeEvents(result.stdout ?? "");
  requireOrdinaryEvidence(report);
  console.log(JSON.stringify({ collectedFiles: files, ...report }));
}, 30_000);

it("ordinary owner rejects zero, skipped, failed, duplicate and missing-file evidence", () => {
  const baseline: Report = { executedIdentities: files.map(file => resolve(file) + ":1:1 supplied case"),
    passed: files.length, failed: 0, skipped: 0, todo: 0, success: true };
  requireOrdinaryEvidence(baseline);
  const mutations: ((value: Report) => void)[] = [
    value => { value.success = false; },
    value => { value.passed = 0; value.executedIdentities = []; },
    value => { value.failed = 1; },
    value => { value.skipped = 1; },
    value => { value.todo = 1; },
    value => { value.executedIdentities = [baseline.executedIdentities[0], baseline.executedIdentities[0]]; value.passed = 2; },
    value => { value.executedIdentities = value.executedIdentities.slice(1); value.passed = value.executedIdentities.length; },
  ];
  for (const mutate of mutations) {
    const evidence = structuredClone(baseline);
    mutate(evidence);
    expect(() => requireOrdinaryEvidence(evidence)).toThrow();
  }
  expect(() => accountNodeEvents("not-json")).toThrow();
  expect(() => accountNodeEvents("")).toThrow();
});
