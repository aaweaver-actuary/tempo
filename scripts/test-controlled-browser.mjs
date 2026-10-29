import { createHash } from "node:crypto";
import { spawn, spawnSync, execFileSync } from "node:child_process";
import { runWithDevelopmentBudget, waitForStudyQuiet } from './development-budget.mjs';
import { mkdtemp, mkdir, readFile, writeFile, rm, readdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";
import { release, version } from "node:os";
import { startControlledHost, controlledEnvironment, prepareControlledAssets } from "./controlled-browser-host.mjs";
import { parseSelection, reportIdentities, verifyControlledResults } from "./controlled-browser-inventory.mjs";
const rootDirectory = fileURLToPath(new URL("../", import.meta.url));
const cli = ["node_modules/@playwright/test/cli.js", "test", "--config", "playwright.controlled.config.ts"];

export function collectSelection(selection, root = rootDirectory) {
  const result = spawnSync(process.execPath, [...cli, ...selection.playwrightArgs, "--list", "--reporter=json"], {
    cwd: root, env: { ...controlledEnvironment(process.env), TEMPO_CONTROLLED_COLLECTION: "1" }, encoding: "utf8", maxBuffer: 8 * 1024 * 1024, timeout: 30000,
  });
  if (result.error || result.status !== 0) throw new Error(`Controlled collection failed: ${result.error?.message ?? result.stderr}`);
  const identities = reportIdentities(JSON.parse(result.stdout));
  if (JSON.stringify(identities) !== JSON.stringify(selection.expected)) throw new Error("Collected identities differ from requested identities");
  return identities;
}
async function assetPaths(directory, prefix = "") {
  const paths = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = `${prefix}/${entry.name}`;
    if (entry.isDirectory()) paths.push(...await assetPaths(join(directory, entry.name), path));
    else if (entry.isFile()) paths.push(path);
    else throw new Error("Unexpected linked controlled output");
  }
  return paths;
}
export async function runControlledBrowser(selection, options = {}) {
  const invocationStarted = performance.now();
  const root = options.root ?? rootDirectory;
  const environment = controlledEnvironment(process.env);
  const outputRoot = options.outputRoot ?? join(root, "test-results/controlled-browser");
  const overlayHash = createHash("sha256").update(execFileSync("git", ["diff", "--binary", "HEAD"], { cwd: root, env: environment }));
  for (const path of execFileSync("git", ["ls-files", "--others", "--exclude-standard", "-z"], { cwd: root, env: environment, encoding: "utf8" }).split("\0").filter(Boolean).sort())
    overlayHash.update(path).update(await readFile(join(root, path)));
  const browsers = JSON.parse(await readFile(join(root, "node_modules/playwright-core/browsers.json"), "utf8"));
  await mkdir(outputRoot, { recursive: true });
  const runDirectory = await mkdtemp(join(outputRoot, "run-"));
  const runId = runDirectory.split(/[\\/]/).at(-1);
  const buildDirectory = join(runDirectory, "build");
  const publicDirectory = join(runDirectory, "public");
  const reportPath = join(runDirectory, "playwright.json");
  const manifestPath = join(runDirectory, "assets.json");
  const evidence = { success: false, origin: /** @type {string | undefined} */ (undefined), version: 2, run_id: runId, checkout: root,
    head: execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, env: environment, encoding: "utf8" }).trim(),
    dirty: execFileSync("git", ["status", "--porcelain"], { cwd: root, env: environment, encoding: "utf8" }).trim() !== "",
    source_overlay_sha256: overlayHash.digest("hex"), selected: selection.expected,
    runtime: { browser_revisions: browsers.browsers.map(({ name, revision }) => ({ name, revision })), node: process.version, platform: process.platform, architecture: process.arch, os_release: release(), os_version: version() },
    environment: "allowlisted; database/broker/proxy/backend/product/model configuration excluded", children: [], stages: {}, api_escapes: [], cleanup: false };
  let activeChild, host, cancelled = false, killDeadline;
  function signalChild(signal) {
    if (!activeChild?.pid) return;
    try { process.kill(process.platform === "win32" ? activeChild.pid : -activeChild.pid, signal); } catch (error) { if (error.code !== "ESRCH") throw error; }
  }
  function cancel() {
    cancelled = true;
    signalChild("SIGTERM");
    killDeadline ??= setTimeout(() => signalChild("SIGKILL"), 5000);
  }
  async function stage(name, action) {
    const started = performance.now();
    try { if (cancelled) throw new Error("Cancelled before " + name); return await action(); }
    finally { evidence.stages[name] = (performance.now() - started) / 1000; }
  }
  async function runNode(name, args, extraEnvironment = {}) {
    await stage(name, async () => {
      if (options.runNode) return options.runNode(name, args, extraEnvironment, { runDirectory, buildDirectory, reportPath });
      await new Promise((resolveChild, rejectChild) => {
        activeChild = spawn(process.execPath, args, { cwd: root, env: { ...environment, ...extraEnvironment }, stdio: ["ignore", "pipe", "pipe"], detached: process.platform !== "win32" });
        const childEvidence = { executable: process.execPath, args, pid: activeChild.pid, stage: name };
        evidence.children.push(childEvidence);
        const stdout = [], stderr = [];
        activeChild.stdout.on("data", data => { stdout.push(data); process.stdout.write(data); });
        activeChild.stderr.on("data", data => { stderr.push(data); process.stderr.write(data); });
        const deadline = setTimeout(() => { childEvidence.timed_out = true; cancel(); }, name === "browser_execution" ? 180000 : 120000);
        activeChild.once("error", rejectChild);
        activeChild.once("close", async (code, signal) => {
          clearTimeout(deadline); clearTimeout(killDeadline); killDeadline = undefined; activeChild = undefined;
          childEvidence.exit_code = code; childEvidence.signal = signal;
          try {
            await writeFile(join(runDirectory, `${name}.stdout.log`), Buffer.concat(stdout));
            await writeFile(join(runDirectory, `${name}.stderr.log`), Buffer.concat(stderr));
            if (code === 0 && !cancelled) resolveChild(); else rejectChild(new Error(`${name} exited ${code ?? signal}`));
          } catch (error) { rejectChild(error); }
        });
      });
    });
  }
  process.on("SIGINT", cancel); process.on("SIGTERM", cancel);
  try {
    await stage("setup", async () => { evidence.asset_hashes = await prepareControlledAssets(root, publicDirectory); });
    evidence.stages.setup = (performance.now() - invocationStarted) / 1000;
    await runNode("package_build", ["node_modules/typescript/bin/tsc", "-p", "packages/chess-line/tsconfig.json"]);
    for (const [name, directory] of [["browser-intents", "database/browser-intents"], ["study-session", "packages/study-session"], ["board-ui", "packages/board-ui"]]) await runNode(`package_build_${name}`, ["node_modules/typescript/bin/tsc", "-p", `${directory}/tsconfig.json`]);
    await waitForStudyQuiet();
    await runNode("production_build", ["node_modules/vite/bin/vite.js", "build", "--config", "vite.controlled.config.ts"], { TEMPO_CONTROLLED_BUILD_DIR: buildDirectory, TEMPO_CONTROLLED_PUBLIC_DIR: publicDirectory });
    await writeFile(manifestPath, JSON.stringify(["/", ...await assetPaths(buildDirectory)]));
    await stage("host_startup", async () => {
      host = await (options.startHost ?? startControlledHost)(buildDirectory, runId);
      evidence.origin = host.origin;
      const readiness = await (await fetch(`${host.origin}/__controlled_host/ready`, { signal: AbortSignal.timeout(3000) })).json();
      if (readiness.run_id !== runId || readiness.host !== "controlled-frontend-only" || readiness.backend_services.length) throw new Error("Invalid frontend-only host readiness");
    });
    await runNode("browser_execution", [...cli, ...selection.playwrightArgs], {
      DEBUG: "pw:browser", TEMPO_CONTROLLED_ORIGIN: host.origin, TEMPO_CONTROLLED_RUN_ID: runId,
      TEMPO_CONTROLLED_ASSET_MANIFEST: manifestPath, TEMPO_CONTROLLED_OUTPUT_DIR: join(runDirectory, "browser"), TEMPO_CI_REPORT: reportPath,
    });
    if (cancelled) throw new Error("Cancelled during execution");
    evidence.identities = verifyControlledResults(JSON.parse(await readFile(reportPath, "utf8")), selection.expected);
    if (host.apiEscapes.length) throw new Error("API requests escaped interception");
    evidence.success = true;
  } catch (error) { evidence.success = false; evidence.error = String(error); }
  finally {
    const started = performance.now();
    try {
      if (host) {
        evidence.api_escapes = host.apiEscapes;
        await host.close();
        let stillListening = false;
        try { await fetch(`${host.origin}/__controlled_host/ready`, { signal: AbortSignal.timeout(1000) }); stillListening = true; } catch { /* A closed owned port must refuse requests. */ }
        if (stillListening) throw new Error("Owned host still listening after cleanup");
      }
      await rm(buildDirectory, { recursive: true, force: true });
      await rm(publicDirectory, { recursive: true, force: true });
      evidence.cleanup = true;
    } catch (error) { evidence.cleanup_error = String(error); evidence.success = false; }
    evidence.stages.cleanup = (performance.now() - started) / 1000;
    evidence.stages.total = (performance.now() - invocationStarted) / 1000;
    clearTimeout(killDeadline); process.off("SIGINT", cancel); process.off("SIGTERM", cancel);
    await writeFile(join(runDirectory, "evidence.json"), JSON.stringify(evidence, null, 2) + "\n");
  }
  return { ...evidence, runDirectory };
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    process.chdir(rootDirectory);
    const selection = parseSelection(process.argv.slice(2));
    const identities = collectSelection(selection);
    console.log(JSON.stringify({ selectedIdentities: identities, cases: identities.length }));
    if (!selection.list) {
      if (!process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN)
        process.exit(await runWithDevelopmentBudget(process.execPath, process.argv.slice(1)));
      const evidence = await runControlledBrowser(selection);
      console.log(`Controlled browser evidence: ${evidence.runDirectory}`);
      if (!evidence.success) throw new Error(evidence.error ?? evidence.cleanup_error);
    }
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
