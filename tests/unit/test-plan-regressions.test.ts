// @vitest-environment node
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";

type PlannedStage = { name: string; command: string; args: string[] };

function plannedStages(tier: string): PlannedStage[] {
  const run = spawnSync(process.execPath, ["scripts/test-all.mjs", "--list", tier], {
    cwd: process.cwd(),
    encoding: "utf8",
  });
  expect(run.status, run.stderr).toBe(0);
  return (JSON.parse(run.stdout) as { stages: PlannedStage[] }).stages;
}

it("full verification owns every test family once without repeating regular browser specs", () => {
  const full = plannedStages("full");
  const names = full.map((stage) => stage.name);
  expect(names).toEqual([
    "capabilities", "unit", "defense_engine", "backend", "rust_format", "rust_lint",
    "rust_test", "lint", "typecheck", "wasm_build", "local_build",
    "docker", "visual",
  ]);
  expect(new Set(names).size).toBe(names.length);
  expect(full.find((stage) => stage.name === "docker")?.args).toEqual(["run", "test:docker"]);
  expect(full.find((stage) => stage.name === "visual")?.args).toEqual(["run", "test:visual"]);
  expect(names).not.toContain("browser");
  expect(plannedStages("ui").map((stage) => stage.name)).toEqual(["capabilities", "browser", "visual"]);
  expect(plannedStages("python").map((stage) => stage.name)).toEqual(["backend"]);
  expect(plannedStages("rust").map((stage) => stage.name)).toEqual([
    "rust_format", "rust_lint", "rust_test",
  ]);
});

it("full verification checks Docker and loopback access before any test family", () => {
  const [firstStage] = plannedStages("full");
  expect(firstStage).toEqual({
    name: "capabilities", command: "node",
    args: ["scripts/check-test-capabilities.mjs", "--docker", "--loopback"],
  });
  const fakeCommandDirectory = mkdtempSync(join(tmpdir(), "tempo-denied-docker-"));
  try {
    const fakeDockerPath = join(fakeCommandDirectory, "docker");
    writeFileSync(fakeDockerPath, "#!/bin/sh\necho 'permission denied' >&2\nexit 1\n");
    chmodSync(fakeDockerPath, 0o755);
    const preflight = spawnSync(process.execPath, firstStage.args, {
      cwd: process.cwd(), encoding: "utf8",
      env: { ...process.env, PATH: `${fakeCommandDirectory}:${process.env.PATH ?? ""}` },
    });
    expect(preflight.status).not.toBe(0);
    expect(preflight.stderr).toContain("before any tests ran");
    expect(preflight.stderr).toContain("Docker");
    expect(preflight.stderr).toContain("require_escalated");
  } finally {
    rmSync(fakeCommandDirectory, { recursive: true, force: true });
  }
});

it("Makefile runs one full plan and rejects combined verification scopes", () => {
  const full = spawnSync("make", ["-n", "full"], { cwd: process.cwd(), encoding: "utf8" });
  expect(full.status).toBe(0);
  expect(full.stdout.trim()).toBe("node scripts/test-all.mjs full");
  const duplicate = spawnSync("make", ["-n", "fast", "full"], {
    cwd: process.cwd(), encoding: "utf8",
  });
  expect(duplicate.status).not.toBe(0);
  expect(duplicate.stderr).toContain("Choose one verification target");
});

it("focused browser and Docker Make targets preflight before launching tests", () => {
  const expectedChecks: Record<string, string> = {
    browser: "--loopback",
    "ui-file": "--loopback",
    view: "--loopback",
    visual: "--docker",
    perf: "--docker",
    "docker-durability": "--docker --loopback",
  };
  for (const [target, checks] of Object.entries(expectedChecks)) {
    const planned = spawnSync("make", ["-n", target, "FILE=fixture.spec.ts", "VIEW=Builder"], {
      cwd: process.cwd(), encoding: "utf8",
    });
    expect(planned.status, `${target}: ${planned.stderr}`).toBe(0);
    const commands = planned.stdout.trim().split("\n");
    const capabilityCheckIndex = commands.indexOf(`node scripts/check-test-capabilities.mjs ${checks}`);
    const browserLaunchIndex = commands.findIndex((command) =>
      command.startsWith("npm run test:") || command.startsWith("node scripts/test-docker.mjs"));
    expect(capabilityCheckIndex, target).toBeGreaterThanOrEqual(0);
    expect(browserLaunchIndex, target).toBeGreaterThan(capabilityCheckIndex);
  }
});

it("regular and pinned Playwright plans partition every browser spec without overlap", () => {
  const listedFiles = (configuration: string, pinned: boolean) => {
    const run = spawnSync("npx", [
      "playwright", "test", "--config", configuration, "--list", "--reporter=json",
    ], {
      cwd: process.cwd(),
      encoding: "utf8",
      env: { ...process.env, ...(pinned ? { TEMPO_VISUAL_RUNNER: "linux-pinned" } : {}) },
    });
    expect(run.status, run.stderr).toBe(0);
    return new Set((JSON.parse(run.stdout) as { suites: Array<{ file: string }> }).suites
      .map((suite) => suite.file));
  };
  const regularFiles = listedFiles("playwright.config.ts", false);
  const visualFiles = listedFiles("playwright.visual.config.ts", true);
  const everyBrowserSpec = readdirSync("tests/browser")
    .filter((name) => name.endsWith(".spec.ts"))
    .sort();
  expect([...regularFiles].filter((file) => visualFiles.has(file))).toEqual([]);
  expect([...regularFiles, ...visualFiles].sort()).toEqual(everyBrowserSpec);
});

it("Docker durability recovery excludes only the already-run browser matrix", () => {
  const listedStages = (argumentsToPass: string[]) => {
    const run = spawnSync(process.execPath, ["scripts/test-docker.mjs", "--list", ...argumentsToPass], {
      cwd: process.cwd(),
      encoding: "utf8",
    });
    expect(run.status, run.stderr).toBe(0);
    return (JSON.parse(run.stdout) as { stages: string[] }).stages;
  };
  expect(listedStages([])).toEqual([
    "compose_config", "container_start", "browser", "durability", "container_stop",
  ]);
  expect(listedStages(["--skip-browser"])).toEqual([
    "compose_config", "container_start", "durability", "container_stop",
  ]);
});

it("test plan records per-file Vitest timings without a second unit run", () => {
  const full = plannedStages("full");
  expect(full.filter((stage) => stage.name === "unit")).toHaveLength(1);
  expect(full.find((stage) => stage.name === "unit")?.args).toEqual([
    "run", "test:unit", "--", "--reporter=default", "--reporter=json",
    "--outputFile.json=test-results/performance/unit-files-full.json",
  ]);
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "tempo-unit-profile-"));
  try {
    writeFileSync(join(temporaryDirectory, "unit-files-probe.json"), JSON.stringify({
      testResults: [
        { name: join(process.cwd(), "tests/unit/fast.test.ts"), startTime: 10, endTime: 20, status: "passed" },
        { name: join(process.cwd(), "tests/unit/slow.test.ts"), startTime: 30, endTime: 140, status: "failed" },
      ],
    }));
    const report = spawnSync(process.execPath, ["scripts/report-slow-unit-files.mjs", "probe", "1"], {
      cwd: process.cwd(),
      encoding: "utf8",
      env: { ...process.env, TEMPO_TEST_TIMING_DIR: temporaryDirectory },
    });
    expect(report.status, report.stderr).toBe(0);
    expect(report.stdout).toContain("0.11s  failed  tests/unit/slow.test.ts");
    expect(report.stdout).not.toContain("fast.test.ts");
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true });
  }
});
