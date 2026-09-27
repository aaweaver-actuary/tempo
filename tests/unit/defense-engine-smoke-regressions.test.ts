// @vitest-environment node
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { expect, it } from "vitest";

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
