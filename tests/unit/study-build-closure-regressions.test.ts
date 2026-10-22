// @vitest-environment node
import { access, cp, mkdtemp, mkdir, readFile, readdir, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { expect, it, vi } from "vitest";
import type { Plugin, PluginOption } from "vite";
import { optionalStudyImplementations } from "../../scripts/study-composition";

const repositoryRoot = resolve(process.cwd()).replaceAll("\\", "/");

type SharedDependencies = {
  browserIntents: string[];
  chess: string[];
  board: string[];
  workersAndWasm: string[];
};

type StudyCompositionInventory = {
  entry: string;
  modules: string[];
  assets: string[];
  sharedDependencies: SharedDependencies;
};

function isPlugin(value: unknown): value is Plugin {
  if (typeof value !== "object" || value === null || !("name" in value)) return false;
  const candidate = value as { name?: unknown };
  return typeof candidate.name === "string";
}

function isPromiseLike<T>(value: unknown): value is PromiseLike<T> {
  if (typeof value !== "object" || value === null || !("then" in value)) return false;
  const candidate = value as { then?: unknown };
  return typeof candidate.then === "function";
}

async function pluginNames(options: PluginOption[] | undefined): Promise<string[]> {
  if (!options?.length) return [];
  const queue: PluginOption[] = [...options];
  const names: string[] = [];
  while (queue.length) {
    const current = queue.shift();
    if (!current) continue;
    if (Array.isArray(current)) { queue.push(...current); continue; }
    if (isPromiseLike<PluginOption>(current)) { queue.push(await current); continue; }
    if (isPlugin(current)) names.push(current.name);
  }
  return names;
}

async function exists(path: string): Promise<boolean> {
  try { await access(path, fsConstants.F_OK); return true; }
  catch { return false; }
}

async function copyInputs(stageRoot: string) {
  const inputs = [
    "app",
    "static",
    "scripts",
    "public",
    "database/browser",
    "database/browser-intents",
    "packages/chess-line",
    "packages/study-session",
    "module-resolution.ts",
    "vite.static.config.ts",
    "package.json",
    "tsconfig.json",
  ];
  for (const path of inputs) {
    const source = join(repositoryRoot, path);
    const target = join(stageRoot, path);
    await mkdir(dirname(target), { recursive: true });
    await cp(source, target, { recursive: true });
  }
}

async function linkDependencies(stageRoot: string) {
  const sourceNodeModules = join(repositoryRoot, "node_modules");
  const stageNodeModules = join(stageRoot, "node_modules");
  await mkdir(stageNodeModules, { recursive: true });
  const dependencies = await readdir(sourceNodeModules, { withFileTypes: true });
  for (const dependency of dependencies) {
    if (dependency.name === "@tempo") continue;
    await symlink(join(sourceNodeModules, dependency.name), join(stageNodeModules, dependency.name));
  }
  const stageTempo = join(stageNodeModules, "@tempo");
  await mkdir(stageTempo, { recursive: true });
  await symlink(join(stageRoot, "packages/chess-line"), join(stageTempo, "chess-line"));
  await symlink(join(stageRoot, "database/browser-intents"), join(stageTempo, "browser-intents"));
  await symlink(join(stageRoot, "packages/study-session"), join(stageTempo, "study-session"));
}

async function stageReducedSourceBuild(stageRoot: string) {
  await copyInputs(stageRoot);
  await linkDependencies(stageRoot);
  const optionalImplementations = Object.values(optionalStudyImplementations).flat().sort();
  for (const path of optionalImplementations) await rm(join(stageRoot, path), { force: true });
  return optionalImplementations;
}

function parseShellInventory(serviceWorker: string) {
  const match = serviceWorker.match(/const BUILD_SHELL_ASSETS = (\[[^\n]+\]);/);
  if (!match) throw new Error("BUILD_SHELL_ASSETS was not emitted");
  const parsed: unknown = JSON.parse(match[1]);
  if (!Array.isArray(parsed) || parsed.some(value => typeof value !== "string"))
    throw new Error("BUILD_SHELL_ASSETS was not a string array");
  return parsed;
}

async function runStagedStudyBuild(stageRoot: string) {
  const { build } = await import("vite");
  const loadedIds = new Set<string>();
  const firstPartyPrefixes = [
    `${repositoryRoot}/app/`,
    `${repositoryRoot}/static/`,
    `${repositoryRoot}/scripts/`,
    `${repositoryRoot}/database/browser-intents/`,
    `${repositoryRoot}/packages/chess-line/`,
    `${repositoryRoot}/packages/study-session/`,
  ];
  const outputDirectory = join(stageRoot, "local-dist");
  const previousWorkingDirectory = process.cwd();
  const previousTarget = process.env.TEMPO_TARGET;
  const previousBuildDirectory = process.env.TEMPO_BROWSER_BUILD_DIR;
  process.chdir(stageRoot);
  process.env.TEMPO_TARGET = "study";
  process.env.TEMPO_BROWSER_BUILD_DIR = outputDirectory;
  try {
    await build({
      configFile: "vite.static.config.ts",
      logLevel: "silent",
      plugins: [{
        name: "tempo-stage-source-resolution-audit",
        enforce: "pre",
        load(id) {
          loadedIds.add(id.split("?")[0].replaceAll("\\", "/"));
          return null;
        },
      }],
    });
  } finally {
    if (previousTarget === undefined) delete process.env.TEMPO_TARGET;
    else process.env.TEMPO_TARGET = previousTarget;
    if (previousBuildDirectory === undefined) delete process.env.TEMPO_BROWSER_BUILD_DIR;
    else process.env.TEMPO_BROWSER_BUILD_DIR = previousBuildDirectory;
    process.chdir(previousWorkingDirectory);
  }
  const leakedFirstPartyIds = [...loadedIds].filter(id => firstPartyPrefixes.some(prefix => id.startsWith(prefix)));
  return { outputDirectory, leakedFirstPartyIds: [...new Set(leakedFirstPartyIds)].sort() };
}

it("Study static build enables composition guard and omits optional Maia cache asset", async () => {
  const { loadConfigFromFile } = await import("vite");
  try {
    vi.stubEnv("TEMPO_TARGET", "study");
    const loadedStudy = await loadConfigFromFile({ command: "build", mode: "production" }, "vite.static.config.ts", process.cwd(), "silent", undefined, "runner");
    const studyPlugins = await pluginNames(loadedStudy?.config.plugins);
    expect(studyPlugins).toContain("tempo-independent-study-composition");
    expect(studyPlugins).not.toContain("tempo-maia-cache-asset");

    vi.stubEnv("TEMPO_TARGET", "local");
    const loadedLocal = await loadConfigFromFile({ command: "build", mode: "production" }, "vite.static.config.ts", process.cwd(), "silent", undefined, "runner");
    const localPlugins = await pluginNames(loadedLocal?.config.plugins);
    expect(localPlugins).toContain("tempo-maia-cache-asset");
  } finally { vi.unstubAllEnvs(); }
});

it("Study reduced-source build succeeds without optional implementations and fails on reintroduced optional dependency", async () => {
  const stageRoot = await mkdtemp(join(tmpdir(), "tempo-study-closure-stage-"));
  const stageRootRealPath = (await realpath(stageRoot)).replaceAll("\\", "/");
  const optionalImplementations = Object.values(optionalStudyImplementations).flat().sort();
  try {
    const stagedOptionalImplementations = await stageReducedSourceBuild(stageRoot);
    expect(stageRootRealPath).not.toBe(repositoryRoot);
    expect((await realpath(join(stageRoot, "app"))).replaceAll("\\", "/").startsWith(stageRootRealPath)).toBe(true);
    for (const path of optionalImplementations)
      expect(await exists(join(stageRoot, path)), `${path} should be absent from staged source`).toBe(false);
    expect(stagedOptionalImplementations).toEqual(optionalImplementations);
    expect(await exists(join(stageRoot, "app/lib/workbench-navigation.ts"))).toBe(true);

    const positive = await runStagedStudyBuild(stageRoot);
    expect(positive.leakedFirstPartyIds).toEqual([]);

    const composition = JSON.parse(await readFile(join(positive.outputDirectory, "study-composition.json"), "utf8")) as StudyCompositionInventory;
    const optionalSet = new Set<string>(optionalImplementations);
    expect(composition.entry).toBe("static/src/study-main.tsx");
    expect(composition.modules.filter(path => optionalSet.has(path))).toEqual([]);
    expect(composition.sharedDependencies.browserIntents.length).toBeGreaterThan(0);
    expect(composition.sharedDependencies.chess.length).toBeGreaterThan(0);
    expect(composition.sharedDependencies.board.length).toBeGreaterThan(0);
    expect(composition.sharedDependencies.workersAndWasm.length).toBeGreaterThan(0);
    const emittedBundleAssets = composition.assets.filter(path => /\.(?:js|mjs|css)$/.test(path));

    const serviceWorker = await readFile(join(positive.outputDirectory, "sw.js"), "utf8");
    const shellInventory = parseShellInventory(serviceWorker);
    expect(shellInventory.some(path => /study\.worker/.test(path))).toBe(true);
    for (const asset of emittedBundleAssets) expect(shellInventory).toContain(asset);
    expect(shellInventory).not.toContain("maia-worker-cache.js");

    const controlledMissingImport = optionalImplementations.find(path => path.startsWith("app/"));
    if (!controlledMissingImport) throw new Error("No optional implementation path was available for the negative regression.");
    const entry = join(stageRoot, "static/src/study-main.tsx");
    const source = await readFile(entry, "utf8");
    await writeFile(entry, `${source}\nimport "../../${controlledMissingImport.replace(/\.(?:ts|tsx)$/, "")}";\n`);
    const importedStem = controlledMissingImport.split("/").at(-1)?.replace(/\.(?:ts|tsx)$/, "");
    await expect(runStagedStudyBuild(stageRoot)).rejects.toThrow(new RegExp(`${importedStem}|Could not resolve|optional workspace implementation`));
  } finally {
    await rm(stageRoot, { recursive: true, force: true });
  }
});
