// @vitest-environment node
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
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
