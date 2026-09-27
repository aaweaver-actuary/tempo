// @vitest-environment node
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
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
    "unit", "defense_engine", "backend", "rust_format", "rust_lint",
    "rust_test", "lint", "typecheck", "wasm_build", "local_build",
    "docker", "visual",
  ]);
  expect(new Set(names).size).toBe(names.length);
  expect(full.find((stage) => stage.name === "docker")?.args).toEqual(["run", "test:docker"]);
  expect(full.find((stage) => stage.name === "visual")?.args).toEqual(["run", "test:visual"]);
  expect(names).not.toContain("browser");
  expect(plannedStages("ui").map((stage) => stage.name)).toEqual(["browser", "visual"]);
  expect(plannedStages("python").map((stage) => stage.name)).toEqual(["backend"]);
  expect(plannedStages("rust").map((stage) => stage.name)).toEqual([
    "rust_format", "rust_lint", "rust_test",
  ]);
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
