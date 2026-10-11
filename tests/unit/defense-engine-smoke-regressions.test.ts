// @vitest-environment node
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import { expect, it } from "vitest";

it("issue171 engine image script manifest imports the position journal and all diagnostic dependencies", () => {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "tempo-engine-image-import-"));
  try {
    copyFileSync("package.json", join(temporaryDirectory, "package.json"));
    for (const instruction of readFileSync("Dockerfile.engine", "utf8").split("\n")) {
      const copy = instruction.match(/^COPY (scripts\/\S+) (\S+)$/);
      if (!copy) continue;
      const destination = join(temporaryDirectory, copy[2]);
      mkdirSync(dirname(destination), { recursive: true });
      copyFileSync(copy[1], destination);
    }
    const journalModule = pathToFileURL(join(temporaryDirectory, "scripts/durable-engine-request.mjs")).href;
    const imported = spawnSync(process.execPath, ["--input-type=module", "--eval",
      `await import(${JSON.stringify(journalModule)})`], { encoding: "utf8", timeout: 5_000 });
    expect(imported.status, imported.stderr).toBe(0);
  } finally { rmSync(temporaryDirectory, { recursive: true, force: true }); }
});

it("standalone defense engine smoke does not poll an unavailable API", () => {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "tempo-engine-smoke-"));
  try {
    const immediateIntervalPath = join(temporaryDirectory, "immediate-interval.mjs");
    writeFileSync(immediateIntervalPath,
      "globalThis.setInterval = (callback) => { queueMicrotask(callback); return 1; };\n");
    const smoke = spawnSync(process.execPath, [
      "--import", pathToFileURL(immediateIntervalPath).href,
      "scripts/test-defense-engine.mjs",
    ], {
      cwd: process.cwd(), encoding: "utf8", timeout: 15_000,
      env: { ...process.env, TEMPO_API_URL: "http://127.0.0.1:1" },
    });
    expect(smoke.status, smoke.stderr).toBe(0);
    expect(smoke.stdout).toContain("Restricted Stockfish search passed");
  } finally {
    rmSync(temporaryDirectory, { recursive: true, force: true });
  }
});
