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
      schemaVersion: 1, commit, timestamp: "2026-09-27T12:01:00Z", browser: "chromium",
      fixture: { name: "prepareVisualUI-default", boardReloads: 5, warmMoves: 5 },
      boardReadySummary: { p50: 20, p95: 25 },
      moveToPaintSummary: { p50: 15, p95: moveP95 },
      viewSwitchSummary: { Builder: { p50: 20, p95: 30 } },
    }));
    writeFileSync(join(directory, "builder-similarity-chromium.json"), JSON.stringify({
      schemaVersion: 1, commit, timestamp: "2026-09-27T12:02:00Z",
      fixture: { name: "legal-two-ply-opening-pairs-v1", lines: 250 },
      roundtripSummary: { p50: 10, p95: 20 },
      paintSummary: { p50: 30, p95: 40 },
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
    expect(summary).toContain("Builder after-move to rAF p95 (legacy move-to-paint)");
    expect(summary).toContain("Builder query to paint p95");
    expect(summary).toContain("30.0% slower");
    const structured = JSON.parse(readFileSync(join(currentDirectory, "performance-summary.json"), "utf8"));
    expect(structured.regressions.map((regression: { metric: string }) => regression.metric))
      .toEqual(["unit", "Builder after-move to rAF p95 (legacy move-to-paint)"]);

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
      commit: "older", timestamp: "2026-09-27T12:01:00Z", browser: "chromium",
      moveToPaintSummary: { p95: 999 },
    }));
    const report = spawnSync(process.execPath, [
      "scripts/report-performance.mjs", "--directory", outputDirectory,
    ], { cwd: process.cwd(), encoding: "utf8" });
    expect(report.status, report.stderr).toBe(0);
    const summary = readFileSync(join(outputDirectory, "performance-summary.md"), "utf8");
    expect(summary).toContain("Stale performance artifacts excluded");
    expect(summary).not.toContain("999");

    writeFileSync(join(outputDirectory, "browser-chromium.json"), JSON.stringify({
      commit: "current", timestamp: "2026-09-27T11:59:00Z", browser: "chromium",
      moveToPaintSummary: { p95: 999 },
    }));
    const earlierRun = spawnSync(process.execPath, [
      "scripts/report-performance.mjs", "--directory", outputDirectory,
    ], { cwd: process.cwd(), encoding: "utf8" });
    expect(earlierRun.status, earlierRun.stderr).toBe(0);
    expect(readFileSync(join(outputDirectory, "performance-summary.md"), "utf8"))
      .not.toContain("999");
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

it("performance summary separates Docker browser and durability timings", () => {
  const outputDirectory = mkdtempSync(join(tmpdir(), "tempo-perf-docker-"));
  try {
    writeFileSync(join(outputDirectory, "test-stages-full.json"), JSON.stringify({
      schema_version: 1, tier: "full", commit: "current", timestamp: "2026-09-27T12:00:00Z",
      environment: { platform: "linux", architecture: "arm64" },
      stages: { docker: { duration_seconds: 120, exit_code: 0 } },
    }));
    writeFileSync(join(outputDirectory, "docker-stages.json"), JSON.stringify({
      schema_version: 1, commit: "current", timestamp: "2026-09-27T12:01:00Z", stages: {
        browser: { duration_seconds: 80, exit_code: 0 },
        durability: { duration_seconds: 30, exit_code: 0 },
      },
    }));
    const report = spawnSync(process.execPath, [
      "scripts/report-performance.mjs", "--directory", outputDirectory,
    ], { cwd: process.cwd(), encoding: "utf8" });
    expect(report.status, report.stderr).toBe(0);
    const summary = readFileSync(join(outputDirectory, "performance-summary.md"), "utf8");
    expect(summary).toContain("Docker browser matrix | 80.00 s");
    expect(summary).toContain("Docker durability | 30.00 s");

    const staleDockerPath = join(outputDirectory, "docker-stages.json");
    const staleDockerReport = JSON.parse(readFileSync(staleDockerPath, "utf8"));
    staleDockerReport.commit = "previous";
    writeFileSync(staleDockerPath, JSON.stringify(staleDockerReport));
    const staleReport = spawnSync(process.execPath, [
      "scripts/report-performance.mjs", "--directory", outputDirectory,
    ], { cwd: process.cwd(), encoding: "utf8" });
    expect(staleReport.status, staleReport.stderr).toBe(0);
    expect(readFileSync(join(outputDirectory, "performance-summary.md"), "utf8"))
      .not.toContain("Docker browser matrix");
  } finally {
    rmSync(outputDirectory, { recursive: true, force: true });
  }
});

it("performance summary flags newly observed long tasks on the same browser fixture", () => {
  const currentDirectory = mkdtempSync(join(tmpdir(), "tempo-perf-long-current-"));
  const baselineDirectory = mkdtempSync(join(tmpdir(), "tempo-perf-long-baseline-"));
  const writeFixture = (directory: string, commit: string, longTasks: number[]) => {
    writeFileSync(join(directory, "test-stages-full.json"), JSON.stringify({
      schema_version: 1, tier: "full", commit, timestamp: "2026-09-27T12:00:00Z",
      environment: { platform: "linux", architecture: "arm64" }, stages: {},
    }));
    writeFileSync(join(directory, "browser-chromium.json"), JSON.stringify({
      commit, timestamp: "2026-09-27T12:01:00Z", browser: "chromium",
      fixture: { name: "interaction-v1" }, longTasks,
    }));
  };
  try {
    writeFixture(baselineDirectory, "baseline", []);
    writeFixture(currentDirectory, "current", [52, 68]);
    const report = spawnSync(process.execPath, [
      "scripts/report-performance.mjs", "--directory", currentDirectory,
      "--baseline", baselineDirectory,
    ], { cwd: process.cwd(), encoding: "utf8" });
    expect(report.status, report.stderr).toBe(0);
    const summary = readFileSync(join(currentDirectory, "performance-summary.md"), "utf8");
    expect(summary).toContain("Long tasks | 2.0 count");
    expect(summary).toContain("new long tasks");
    const structured = JSON.parse(readFileSync(join(currentDirectory, "performance-summary.json"), "utf8"));
    expect(structured.regressions).toEqual(expect.arrayContaining([
      expect.objectContaining({ metric: "Long tasks", current: 2, baseline: 0 }),
    ]));
  } finally {
    rmSync(currentDirectory, { recursive: true, force: true });
    rmSync(baselineDirectory, { recursive: true, force: true });
  }
});

it("held_drag_reports_reject_stale_or_incomparable_baselines", () => {
  const currentDirectory = mkdtempSync(join(tmpdir(), "tempo-held-current-"));
  const baselineDirectory = mkdtempSync(join(tmpdir(), "tempo-held-baseline-"));
  try {
    const timestamp = "2026-09-30T12:00:00Z";
    const fixture = { name: "held-v1", repetitions: 3 };
    const environment = { browserVersion: "153", buildMode: "production", viewport: { width: 1280 }, devicePixelRatio: 1, dockerCpus: "4" };
    const artifact = (commit: string, browserEnvironment = environment) => ({ commit, timestamp, environment: browserEnvironment, fixture,
      summaries: [{ workload: "idle", enabled: true, count: 6, frameGapMs: { p95: commit === "current" ? 50 : 16 }, displacementCssPx: { p95: 5 }, interruptionCount: 0 }] });
    for (const [directory, commit] of [[currentDirectory, "current"], [baselineDirectory, "baseline"]]) {
      writeFileSync(join(directory, "test-stages-full.json"), JSON.stringify({ commit, timestamp, environment: { platform: "linux" }, stages: {} }));
      writeFileSync(join(directory, "held-drag-chromium.json"), JSON.stringify(artifact(commit)));
    }
    const runSummary = () => {
      const result = spawnSync(process.execPath, ["scripts/report-performance.mjs", "--directory", currentDirectory, "--baseline", baselineDirectory], { encoding: "utf8" });
      expect(result.status, result.stderr).toBe(0);
      return JSON.parse(readFileSync(join(currentDirectory, "performance-summary.json"), "utf8"));
    };
    expect(runSummary().regressions).toHaveLength(1);
    // Standalone make perf runs have a manifest, not a fabricated full gate.
    writeFileSync(join(currentDirectory, "performance-run.json"), readFileSync(join(currentDirectory, "test-stages-full.json")));
    rmSync(join(currentDirectory, "test-stages-full.json"));
    expect(runSummary().regressions).toHaveLength(1);
    writeFileSync(join(baselineDirectory, "held-drag-chromium.json"), JSON.stringify(artifact("baseline", { ...environment, dockerCpus: "2" })));
    expect(runSummary().regressions).toEqual([]);
    writeFileSync(join(currentDirectory, "held-drag-chromium.json"), JSON.stringify(artifact("stale")));
    const stale = runSummary();
    expect(stale.staleArtifacts).toContain("held-drag-chromium.json");
    expect(stale.metrics).toEqual([]);
  } finally {
    rmSync(currentDirectory, { recursive: true, force: true });
    rmSync(baselineDirectory, { recursive: true, force: true });
  }
});

it("held_drag_reports_flag_new_interruptions_without_inventing_percentages", () => {
  const currentDirectory = mkdtempSync(join(tmpdir(), "tempo-interrupt-current-"));
  const baselineDirectory = mkdtempSync(join(tmpdir(), "tempo-interrupt-baseline-"));
  const timestamp = "2026-09-30T12:00:00Z";
  const writeRun = (directory: string, commit: string, interruptionCount: number, fixture = "held-v3", artifactCommit = commit) => {
    writeFileSync(join(directory, "performance-run.json"), JSON.stringify({ commit, timestamp, stages: {} }));
    writeFileSync(join(directory, "held-drag-chromium.json"), JSON.stringify({
      commit: artifactCommit, timestamp, environment: { browser: "153", architecture: "arm64" }, fixture,
      summaries: [{ workload: "idle", enabled: true, count: 6, interruptionCount }],
    }));
  };
  const report = (withBaseline = true) => {
    const run = spawnSync(process.execPath, ["scripts/report-performance.mjs", "--directory", currentDirectory,
      ...(withBaseline ? ["--baseline", baselineDirectory] : [])], { encoding: "utf8" });
    expect(run.status, run.stderr).toBe(0);
    return { markdown: readFileSync(join(currentDirectory, "performance-summary.md"), "utf8"),
      json: JSON.parse(readFileSync(join(currentDirectory, "performance-summary.json"), "utf8")) };
  };
  try {
    writeRun(baselineDirectory, "baseline", 0);
    writeRun(currentDirectory, "current", 1);
    const interrupted = report();
    expect(interrupted.json.regressions).toEqual([{ metric: "Held drag idle.capture-on interrupted holds",
      current: 1, baseline: 0, percentage: null, unit: "count" }]);
    expect(interrupted.markdown).toContain("| 0.0 count | new interrupted holds |");
    expect(interrupted.markdown).toContain("or new interrupted holds appeared");
    writeRun(currentDirectory, "current", 0);
    const unchanged = report();
    expect(unchanged.json.regressions).toEqual([]);
    expect(unchanged.markdown).toContain("| 0.0 count | no change |");
    expect(unchanged.markdown).toContain("no new interrupted holds appeared");
    writeRun(currentDirectory, "current", 1, "different-fixture");
    expect(report().json.regressions).toEqual([]);
    expect(report().markdown).toContain("not comparable");
    writeRun(currentDirectory, "current", 1, "held-v3", "stale");
    expect(report().json.staleArtifacts).toContain("held-drag-chromium.json");
    expect(report().json.regressions).toEqual([]);
    writeRun(currentDirectory, "current", 1);
    expect(report(false).json.regressions).toEqual([]);
    expect(report(false).markdown).toContain("no regression verdict");
    writeRun(baselineDirectory, "baseline", 1);
    writeRun(currentDirectory, "current", 2);
    expect(report().json.regressions[0].percentage).toBe(100);
  } finally {
    rmSync(currentDirectory, { recursive: true, force: true });
    rmSync(baselineDirectory, { recursive: true, force: true });
  }
});
