// @vitest-environment node
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, it, vi } from "vitest";
import { optionalStudyImplementations } from "../../scripts/study-composition";

it("Study static build enables composition guard and omits optional Maia cache asset", async () => {
  const { loadConfigFromFile } = await import("vite");
  try {
    vi.stubEnv("TEMPO_TARGET", "study");
    const loadedStudy = await loadConfigFromFile({ command: "build", mode: "production" }, "vite.static.config.ts", process.cwd(), "silent", undefined, "runner");
    const studyPlugins = (loadedStudy?.config.plugins ?? []).map(plugin => plugin?.name).filter(Boolean);
    expect(studyPlugins).toContain("tempo-independent-study-composition");
    expect(studyPlugins).not.toContain("tempo-maia-cache-asset");

    vi.stubEnv("TEMPO_TARGET", "local");
    const loadedLocal = await loadConfigFromFile({ command: "build", mode: "production" }, "vite.static.config.ts", process.cwd(), "silent", undefined, "runner");
    const localPlugins = (loadedLocal?.config.plugins ?? []).map(plugin => plugin?.name).filter(Boolean);
    expect(localPlugins).toContain("tempo-maia-cache-asset");
  } finally { vi.unstubAllEnvs(); }
});

it("Study build passes with optional implementations blocked and emits auditable closure evidence", async () => {
  const { build } = await import("vite");
  const outputDirectory = await mkdtemp(join(tmpdir(), "tempo-study-closure-build-"));
  try {
    vi.stubEnv("TEMPO_TARGET", "study");
    vi.stubEnv("TEMPO_BROWSER_BUILD_DIR", outputDirectory);
    const blockedModules = new Set(Object.values(optionalStudyImplementations).flat()
      .map(path => resolve(path).replaceAll("\\", "/")));
    await build({
      configFile: "vite.static.config.ts",
      logLevel: "silent",
      plugins: [{
        name: "tempo-study-source-removed-proof",
        enforce: "pre",
        load(id) {
          const path = id.split("?")[0].replaceAll("\\", "/");
          if (blockedModules.has(path)) throw new Error(`Optional implementation source unavailable in proof build: ${path}`);
          return null;
        },
      }],
    });
    const composition = JSON.parse(await readFile(join(outputDirectory, "study-composition.json"), "utf8"));
    const blocked = Object.values(optionalStudyImplementations).flat();
    expect(composition.entry).toBe("static/src/study-main.tsx");
    expect(composition.modules.filter((path: string) => blocked.includes(path))).toEqual([]);
    expect(composition.sharedDependencies.browserIntents.length).toBeGreaterThan(0);
    expect(composition.sharedDependencies.chess.length).toBeGreaterThan(0);
    expect(composition.sharedDependencies.board.length).toBeGreaterThan(0);
    expect(composition.sharedDependencies.workersAndWasm.length).toBeGreaterThan(0);

    const serviceWorker = await readFile(join(outputDirectory, "sw.js"), "utf8");
    expect(serviceWorker).toContain("study.worker");
    expect(serviceWorker).not.toContain("maia-worker-cache.js");
  } finally {
    vi.unstubAllEnvs();
    await rm(outputDirectory, { recursive: true, force: true });
  }
});
