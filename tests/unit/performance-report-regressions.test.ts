// @vitest-environment node
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";

it("performance summary flags measured regressions without repeating test stages", () => {
  const currentDirectory = mkdtempSync(join(tmpdir(), "tempo-perf-current-"));
  const baselineDirectory = mkdtempSync(join(tmpdir(), "tempo-perf-baseline-"));
  const writeFixture = (directory: string, commit: string, unitSeconds: number, moveP95: number) => {
    writeFileSync(join(directory, "test-stages-full.json"), JSON.stringify({
      schema_version: 1, tier: "full", commit, timestamp: "2026-09-27T12:00:00Z",
      environment: { platform: "linux", architecture: "arm64", node: "v22" },
      stages: { capabilities: { duration_seconds: 0.2, exit_code: 0 },
        unit: { duration_seconds: unitSeconds, exit_code: 0 } },
    }));
    writeFileSync(join(directory, "browser-chromium.json"), JSON.stringify({
      schemaVersion: 1, commit, browser: "chromium",
      fixture: { name: "prepareVisualUI-default", boardReloads: 5, warmMoves: 5 },
      boardReadySummary: { p50: 20, p95: 25 },
      moveToPaintSummary: { p50: 15, p95: moveP95 },
      viewSwitchSummary: { Builder: { p50: 20, p95: 30 } },
    }));
  };
  try {
    writeFixture(baselineDirectory, "baseline", 40, 20);
    writeFixture(currentDirectory, "current", 52, 26);
    const report = spawnSync(process.execPath, [
      "scripts/report-performance.mjs", "--directory", currentDirectory,
      "--baseline", baselineDirectory,
    ], { cwd: process.cwd(), encoding: "utf8" });
    expect(report.status, report.stderr).toBe(0);
    const summary = readFileSync(join(currentDirectory, "performance-summary.md"), "utf8");
    expect(summary).toContain("unit");
    expect(summary).toContain("Builder move to paint p95");
    expect(summary).toContain("30.0% slower");
    const structured = JSON.parse(readFileSync(join(currentDirectory, "performance-summary.json"), "utf8"));
    expect(structured.regressions.map((regression: { metric: string }) => regression.metric))
      .toEqual(["unit", "Builder move to paint p95"]);

    const baselineStagePath = join(baselineDirectory, "test-stages-full.json");
    const baselineStage = JSON.parse(readFileSync(baselineStagePath, "utf8"));
    baselineStage.environment.architecture = "x64";
    writeFileSync(baselineStagePath, JSON.stringify(baselineStage));
    const differentRunner = spawnSync(process.execPath, [
      "scripts/report-performance.mjs", "--directory", currentDirectory,
      "--baseline", baselineDirectory,
    ], { cwd: process.cwd(), encoding: "utf8" });
    expect(differentRunner.status, differentRunner.stderr).toBe(0);
    const differentRunnerSummary = JSON.parse(readFileSync(
      join(currentDirectory, "performance-summary.json"), "utf8"));
    expect(differentRunnerSummary.regressions).toEqual([]);
    expect(readFileSync(join(currentDirectory, "performance-summary.md"), "utf8"))
      .toContain("not comparable");
  } finally {
    rmSync(currentDirectory, { recursive: true, force: true });
    rmSync(baselineDirectory, { recursive: true, force: true });
  }
});

it("performance summary excludes stale browser artifacts from another commit", () => {
  const outputDirectory = mkdtempSync(join(tmpdir(), "tempo-perf-stale-"));
  try {
    writeFileSync(join(outputDirectory, "test-stages-full.json"), JSON.stringify({
      schema_version: 1, tier: "full", commit: "current", timestamp: "2026-09-27T12:00:00Z",
      environment: { platform: "linux", architecture: "arm64" }, stages: {},
    }));
    writeFileSync(join(outputDirectory, "browser-chromium.json"), JSON.stringify({
      commit: "older", browser: "chromium", moveToPaintSummary: { p95: 999 },
    }));
    const report = spawnSync(process.execPath, [
      "scripts/report-performance.mjs", "--directory", outputDirectory,
    ], { cwd: process.cwd(), encoding: "utf8" });
    expect(report.status, report.stderr).toBe(0);
    const summary = readFileSync(join(outputDirectory, "performance-summary.md"), "utf8");
    expect(summary).toContain("Stale browser artifacts excluded");
    expect(summary).not.toContain("999");
  } finally {
    rmSync(outputDirectory, { recursive: true, force: true });
  }
});

it("performance summary names failed stages and slow unit files from the same run", () => {
  const outputDirectory = mkdtempSync(join(tmpdir(), "tempo-perf-unit-profile-"));
  try {
    const runStartedAt = Date.parse("2026-09-27T12:00:00Z");
    writeFileSync(join(outputDirectory, "test-stages-full.json"), JSON.stringify({
      schema_version: 1, tier: "full", commit: "current", timestamp: "2026-09-27T12:00:00Z",
      environment: { platform: "linux" },
      stages: { unit: { duration_seconds: 12, exit_code: 0 },
        backend: { duration_seconds: 4, exit_code: 1 } },
    }));
    writeFileSync(join(outputDirectory, "unit-files-full.json"), JSON.stringify({
      startTime: runStartedAt + 1000,
      testResults: [
        { name: join(process.cwd(), "tests/unit/fast.test.ts"), startTime: 10, endTime: 110, status: "passed" },
        { name: join(process.cwd(), "tests/unit/slow.test.ts"), startTime: 10, endTime: 1010, status: "passed" },
      ],
    }));
    const report = spawnSync(process.execPath, [
      "scripts/report-performance.mjs", "--directory", outputDirectory,
    ], { cwd: process.cwd(), encoding: "utf8" });
    expect(report.status, report.stderr).toBe(0);
    const summary = readFileSync(join(outputDirectory, "performance-summary.md"), "utf8");
    expect(summary).toContain("backend | 4.00 s | failed (1)");
    expect(summary).toContain("Slowest unit files");
    expect(summary.indexOf("slow.test.ts")).toBeLessThan(summary.indexOf("fast.test.ts"));

    const unitProfilePath = join(outputDirectory, "unit-files-full.json");
    const staleUnitProfile = JSON.parse(readFileSync(unitProfilePath, "utf8"));
    staleUnitProfile.startTime = runStartedAt - 1000;
    writeFileSync(unitProfilePath, JSON.stringify(staleUnitProfile));
    const staleReport = spawnSync(process.execPath, [
      "scripts/report-performance.mjs", "--directory", outputDirectory,
    ], { cwd: process.cwd(), encoding: "utf8" });
    expect(staleReport.status, staleReport.stderr).toBe(0);
    expect(readFileSync(join(outputDirectory, "performance-summary.md"), "utf8"))
      .not.toContain("Slowest unit files");
  } finally {
    rmSync(outputDirectory, { recursive: true, force: true });
  }
});
