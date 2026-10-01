// @vitest-environment node
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
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
    "postgres_docker", "visual",
  ]);
  expect(new Set(names).size).toBe(names.length);
  expect(full.find((stage) => stage.name === "postgres_docker")?.args)
    .toEqual(["scripts/test-postgres-docker.mjs"]);
  expect(full.find((stage) => stage.name === "visual")?.args).toEqual(["run", "test:visual"]);
  expect(names).not.toContain("browser");
  expect(plannedStages("ui").map((stage) => stage.name)).toEqual(["capabilities", "postgres_docker", "visual"]);
  expect(plannedStages("python").map((stage) => stage.name)).toEqual(["backend"]);
  expect(plannedStages("rust").map((stage) => stage.name)).toEqual([
    "rust_format", "rust_lint", "rust_test",
  ]);
});

it("full verification checks Docker and loopback access before any test family", () => {
  const [firstStage] = plannedStages("full");
  expect(firstStage).toEqual({
    name: "capabilities", command: "node",
    args: ["scripts/check-test-capabilities.mjs", "--docker", "--loopback", "--workspace-mount"],
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

it("pinned browser preflight detects an inaccessible checkout mount before tests", () => {
  const fakeCommandDirectory = mkdtempSync(join(tmpdir(), "tempo-missing-mount-"));
  try {
    const fakeDockerPath = join(fakeCommandDirectory, "docker");
    writeFileSync(fakeDockerPath, [
      "#!/bin/sh",
      "if [ \"$1\" = info ]; then echo 28.0; exit 0; fi",
      "if [ \"$1\" = run ]; then echo 'package-lock.json missing from bind mount' >&2; exit 1; fi",
      "exit 1",
      "",
    ].join("\n"));
    chmodSync(fakeDockerPath, 0o755);
    const preflight = spawnSync(process.execPath, [
      "scripts/check-test-capabilities.mjs", "--docker", "--workspace-mount",
    ], {
      cwd: process.cwd(), encoding: "utf8",
      env: { ...process.env, PATH: `${fakeCommandDirectory}:${process.env.PATH ?? ""}` },
    });
    expect(preflight.status).not.toBe(0);
    expect(preflight.stderr).toContain("package-lock.json missing from bind mount");
    expect(preflight.stderr).toContain("before any tests ran");
  } finally {
    rmSync(fakeCommandDirectory, { recursive: true, force: true });
  }
});

it("pinned performance reruns retain raw artifacts in a chosen checkout directory", () => {
  const fakeCommandDirectory = mkdtempSync(join(tmpdir(), "tempo-visual-output-"));
  try {
    const fakeDockerPath = join(fakeCommandDirectory, "docker");
    const capturedArgumentsPath = join(fakeCommandDirectory, "docker-arguments.txt");
    writeFileSync(fakeDockerPath, "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$TEMPO_TEST_DOCKER_CAPTURE\"\n");
    chmodSync(fakeDockerPath, 0o755);
    const run = spawnSync(process.execPath, ["scripts/test-visual.mjs", "--performance-only"], {
      cwd: process.cwd(), encoding: "utf8",
      env: { ...process.env, PATH: `${fakeCommandDirectory}:${process.env.PATH ?? ""}`,
        TEMPO_TEST_DOCKER_CAPTURE: capturedArgumentsPath,
        TEMPO_TEST_TIMING_DIR: "test-results/performance/repeat-fixture" },
    });
    expect(run.status, run.stderr).toBe(0);
    expect(readFileSync(capturedArgumentsPath, "utf8"))
      .toContain("TEMPO_TEST_TIMING_DIR=/workspace/test-results/performance/repeat-fixture");
    expect(readFileSync("tests/browser/performance.spec.ts", "utf8"))
      .toContain("process.env.TEMPO_TEST_TIMING_DIR");
    const outsideCheckout = spawnSync(process.execPath, ["scripts/test-visual.mjs", "--performance-only"], {
      cwd: process.cwd(), encoding: "utf8",
      env: { ...process.env, PATH: `${fakeCommandDirectory}:${process.env.PATH ?? ""}`,
        TEMPO_TEST_DOCKER_CAPTURE: capturedArgumentsPath,
        TEMPO_TEST_TIMING_DIR: join(tmpdir(), "tempo-outside-checkout") },
    });
    expect(outsideCheckout.status).not.toBe(0);
    expect(outsideCheckout.stderr).toContain("inside the checkout");
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

it("PostgreSQL upgrade --plan validates identities and invokes no mutating Docker command", () => {
  const fakeCommandDirectory = mkdtempSync(join(tmpdir(), "tempo-upgrade-plan-"));
  try {
    const capturePath = join(fakeCommandDirectory, "docker-calls.txt");
    const configPath = join(fakeCommandDirectory, "compose.json");
    const volumes = Object.fromEntries(["tempo-postgres-data", "tempo-postgres-backups",
      "tempo-redis-data", "tempo-engine-operations"].map((name) => [name, { external: true, name }]));
    writeFileSync(configPath, JSON.stringify({ name: "tempo", volumes }));
    const dockerPath = join(fakeCommandDirectory, "docker");
    writeFileSync(dockerPath, [
      "#!/bin/sh",
      "printf '%s\\n' \"$*\" >> \"$TEMPO_UPGRADE_DOCKER_CAPTURE\"",
      "cat \"$TEMPO_UPGRADE_COMPOSE_CONFIG\"",
      "",
    ].join("\n"));
    chmodSync(dockerPath, 0o755);
    const plan = spawnSync("sh", ["scripts/upgrade-postgres-schema.sh", "--plan"], {
      cwd: process.cwd(), encoding: "utf8",
      env: { ...process.env, PATH: `${fakeCommandDirectory}:${process.env.PATH ?? ""}`,
        TEMPO_UPGRADE_EXPECTED_PROJECT: "tempo", TEMPO_UPGRADE_DOCKER_CAPTURE: capturePath,
        TEMPO_UPGRADE_COMPOSE_CONFIG: configPath },
    });
    expect(plan.status, plan.stderr).toBe(0);
    expect(plan.stdout).toContain("READ-ONLY PostgreSQL schema upgrade plan");
    expect(readFileSync(capturePath, "utf8").trim()).toBe("compose config --format json");
  } finally {
    rmSync(fakeCommandDirectory, { recursive: true, force: true });
  }
});

it("lint scope excludes ignored local checkout copies", () => {
  const packageJson = JSON.parse(readFileSync("package.json", "utf8")) as {
    scripts: { lint: string };
  };
  expect(packageJson.scripts.lint).toContain("--ignore-pattern .dev-copies");
});

it("focused browser and Docker Make targets preflight before launching tests", () => {
  const expectedChecks: Record<string, string> = {
    browser: "--docker --loopback --workspace-mount",
    "ui-file": "--docker --loopback --workspace-mount",
    view: "--docker --loopback --workspace-mount",
    visual: "--docker --workspace-mount",
    perf: "--docker --workspace-mount",
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
      command.startsWith("npm run test:") || command.startsWith("node scripts/test-docker.mjs")
      || command.startsWith("node scripts/test-postgres-docker.mjs")
      || command.startsWith("node scripts/run-focused-postgres-browser.mjs"));
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
      env: { ...process.env, TEMPO_DOCKER_URL: "http://127.0.0.1:1",
        ...(pinned ? { TEMPO_VISUAL_RUNNER: "linux-pinned" } : {}) },
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
  expect(listedStages([])).toContain("browser");
  expect(listedStages(["--skip-browser"])).not.toContain("browser");
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


it("split complete verification matches the existing full command inventory", () => {
  const full = plannedStages("full").filter(stage => !["capabilities", "postgres_docker", "visual"].includes(stage.name));
  const split = ["ci-frontend", "ci-backend", "ci-build"].flatMap(plannedStages);
  expect(new Set(split.map(stage => stage.name)).size).toBe(split.length);
  const normalized = (stages: PlannedStage[]) => stages.map(stage => ({ ...stage,
    args: stage.args.filter(argument => !argument.startsWith("--junitxml=")).map(argument => argument.replace(/unit-files-[^/]+\.json$/, "unit-files-TIER.json")),
  })).sort((left, right) => left.name.localeCompare(right.name));
  expect(normalized(split)).toEqual(normalized(full));
});
