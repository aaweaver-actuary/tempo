// @vitest-environment node
import { spawnSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";

it("pinned visual runner caches npm downloads while reinstalling locked dependencies", () => {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "tempo-visual-runner-"));
  try {
    const dockerExecutable = join(temporaryDirectory, "docker");
    const recordedArguments = join(temporaryDirectory, "docker-arguments.txt");
    writeFileSync(dockerExecutable, '#!/bin/sh\nprintf "%s\\n" "$@" > "$TEMPO_DOCKER_ARGUMENTS"\n');
    chmodSync(dockerExecutable, 0o755);
    const run = spawnSync(process.execPath, ["scripts/test-visual.mjs", "--performance-only"], {
      cwd: process.cwd(),
      encoding: "utf8",
      env: {
        ...process.env,
        PATH: `${temporaryDirectory}:${process.env.PATH ?? ""}`,
        TEMPO_DOCKER_ARGUMENTS: recordedArguments,
      },
    });
    expect(run.status).toBe(0);
    const argumentsPassed = readFileSync(recordedArguments, "utf8").split("\n");
    expect(argumentsPassed).toContain("type=volume,source=tempo-playwright-npm-cache,target=/root/.npm");
    expect(argumentsPassed).toContain("/workspace/node_modules");
    expect(argumentsPassed.some((argument) => argument.includes("npm ci --no-audit"))).toBe(true);
    expect(argumentsPassed).toContain("linux/arm64");
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true });
  }
});

it("held_drag_nested_performance_manifest_preserves_the_full_run_timestamp", () => {
  const commandDirectory = mkdtempSync(join(tmpdir(), "tempo-nested-runner-"));
  mkdirSync("test-results", { recursive: true });
  const outputDirectory = mkdtempSync(join(process.cwd(), "test-results/manifest-regression-"));
  try {
    // Stub expensive stage execution, but execute the real full runner and its
    // visual runner to verify their manifest contract without Docker or tests.
    const preload = join(commandDirectory, "stage-stub.mjs");
    const visualEnvironment = join(commandDirectory, "visual-environment.json");
    writeFileSync(preload, `import childProcess from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
childProcess.spawnSync = (command, args, options) => {
  if (args?.includes('test:visual')) writeFileSync(process.env.TEMPO_VISUAL_ENVIRONMENT, JSON.stringify(options.env));
  return { status: 0, stdout: 'synthetic-version', stderr: '' };
};
syncBuiltinESMExports();\n`);
    const fakeDocker = join(commandDirectory, "docker");
    writeFileSync(fakeDocker, '#!/bin/sh\nif [ "$1" = info ]; then echo "4 1024"; fi\nexit 0\n');
    chmodSync(fakeDocker, 0o755);
    const fullRun = spawnSync(process.execPath, ["--import", preload, "scripts/test-all.mjs", "full"], {
      encoding: "utf8", env: { ...process.env, TEMPO_TEST_TIMING_DIR: outputDirectory,
        TEMPO_VISUAL_ENVIRONMENT: visualEnvironment },
    });
    expect(fullRun.status, fullRun.stderr).toBe(0);
    const fullManifest = JSON.parse(readFileSync(join(outputDirectory, "test-stages-full.json"), "utf8"));
    const nestedEnvironment = JSON.parse(readFileSync(visualEnvironment, "utf8"));
    const nestedRun = spawnSync(process.execPath, ["scripts/test-visual.mjs", "--performance-only"], {
      encoding: "utf8", env: { ...nestedEnvironment, PATH: `${commandDirectory}:${process.env.PATH ?? ""}` },
    });
    expect(nestedRun.status, nestedRun.stderr).toBe(0);
    const nestedManifest = JSON.parse(readFileSync(join(outputDirectory, "performance-run.json"), "utf8"));
    expect(nestedManifest.timestamp).toBe(fullManifest.timestamp);
    expect(nestedManifest.commit).toBe(fullManifest.commit);
    const standaloneEnvironment = { ...nestedEnvironment };
    delete standaloneEnvironment.TEMPO_FULL_TEST_RUN_TIMESTAMP;
    delete standaloneEnvironment.TEMPO_FULL_TEST_RUN_COMMIT;
    const standaloneRun = spawnSync(process.execPath, ["scripts/test-visual.mjs", "--performance-only"], {
      encoding: "utf8", env: { ...standaloneEnvironment, PATH: `${commandDirectory}:${process.env.PATH ?? ""}` },
    });
    expect(standaloneRun.status, standaloneRun.stderr).toBe(0);
    const standaloneManifest = JSON.parse(readFileSync(join(outputDirectory, "performance-run.json"), "utf8"));
    expect(Date.parse(standaloneManifest.timestamp)).toBeGreaterThan(Date.parse(fullManifest.timestamp));
  } finally {
    rmSync(outputDirectory, { recursive: true, force: true });
    rmSync(commandDirectory, { recursive: true, force: true });
  }
});
