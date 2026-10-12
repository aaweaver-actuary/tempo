import { createHash } from "node:crypto";
import { spawn, execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { resolve, join } from "node:path";
import { performance } from "node:perf_hooks";
import { startControlledHost, controlledEnvironment } from "./controlled-browser-host.mjs";
import { verifyPilotSource, verifyPilotResults } from "./controlled-browser-inventory.mjs";

if (process.argv.length > 2) throw new Error("The qualification runner executes the exact pilot; no implicit filters");
const root = process.cwd();
const outputRoot = resolve(root, "test-results/controlled-browser");
await mkdir(outputRoot, { recursive: true });
const runDirectory = await mkdtemp(join(outputRoot, "run-"));
const runId = runDirectory.split(/[\\/]/).at(-1);
const buildDirectory = join(runDirectory, "build");
const reportPath = join(runDirectory, "playwright.json");
const environment = controlledEnvironment(process.env);
const trackedDiff = execFileSync("git", ["diff", "--binary", "HEAD"]);
const untrackedFiles = execFileSync("git", ["ls-files", "--others", "--exclude-standard", "-z"], { encoding: "utf8" }).split("\0").filter(Boolean);
const overlayHash = createHash("sha256").update(trackedDiff);
for (const path of untrackedFiles.sort()) overlayHash.update(path).update(await readFile(path));
const browsers = JSON.parse(await readFile("node_modules/playwright-core/browsers.json", "utf8"));
const evidence = { version: 1, run_id: runId, checkout: root,
  head: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
  dirty: execFileSync("git", ["status", "--porcelain"], { encoding: "utf8" }).trim() !== "",
  base: execFileSync("git", ["rev-parse", "origin/main"], { encoding: "utf8" }).trim(),
  source_overlay_sha256: overlayHash.digest("hex"),
  runtime: { browser_revisions: browsers.browsers.map(({ name, revision }) => ({ name, revision })), node: process.version, platform: process.platform, architecture: process.arch },
  credentials: "child environment allowlisted; no database/API/Redis/worker configuration", children: [], stages: {}, api_escapes: [], cleanup: false };
let activeChild, host;
let killDeadline;
async function runNode(stage, args, extraEnvironment = {}) {
  if (cancelled) throw new Error(`Cancelled before ${stage}`);
  const started = performance.now();
  await new Promise((resolveChild, rejectChild) => {
    activeChild = spawn(process.execPath, args, { env: { ...environment, ...extraEnvironment }, stdio: "inherit" });
    evidence.children.push({ executable: process.execPath, args, pid: activeChild.pid });
    activeChild.once("error", rejectChild);
    activeChild.once("exit", (code, signal) => { clearTimeout(killDeadline); activeChild = undefined; if (code === 0) resolveChild(); else rejectChild(new Error(`${stage} exited ${code ?? signal}`)); });
  }).finally(() => { evidence.stages[stage] = (performance.now() - started) / 1000; });
}
let cancelled = false;
function cancel() {
  cancelled = true;
  if (activeChild) {
    activeChild.kill("SIGTERM");
    killDeadline ??= setTimeout(() => activeChild?.kill("SIGKILL"), 5000);
  }
}
process.on("SIGINT", cancel); process.on("SIGTERM", cancel);
try {
  verifyPilotSource();
  await runNode("production_build", ["node_modules/vite/bin/vite.js", "build", "--config", "vite.controlled.config.ts"], { TEMPO_CONTROLLED_BUILD_DIR: buildDirectory });
  if (cancelled) throw new Error("Cancelled before host startup");
  const started = performance.now();
  host = await startControlledHost(buildDirectory, runId);
  evidence.origin = host.origin;
  const readiness = await (await fetch(`${host.origin}/__controlled_host/ready`)).json();
  if (readiness.run_id !== runId || readiness.backend_services.length) throw new Error("Invalid frontend-only host readiness");
  evidence.stages.host_startup = (performance.now() - started) / 1000;
  await runNode("browser_execution", ["node_modules/@playwright/test/cli.js", "test", "--config", "playwright.controlled.config.ts"], {
    TEMPO_CONTROLLED_ORIGIN: host.origin, TEMPO_CONTROLLED_RUN_ID: runId,
    TEMPO_CONTROLLED_OUTPUT_DIR: join(runDirectory, "browser"), TEMPO_CI_REPORT: reportPath,
  });
  if (cancelled) throw new Error("Cancelled during browser execution");
  evidence.identities = verifyPilotResults(JSON.parse(await readFile(reportPath, "utf8")));
  if (host.apiEscapes.length) throw new Error("API requests escaped interception");
  evidence.success = true;
} catch (error) {
  evidence.success = false; evidence.error = String(error); process.exitCode = 1;
} finally {
  const started = performance.now();
  try {
    if (host) { evidence.api_escapes = host.apiEscapes; await host.close();
      try { await fetch(`${host.origin}/__controlled_host/ready`, { signal: AbortSignal.timeout(1000) }); throw new Error("Owned host still listening after cleanup"); }
      catch (error) { if (error.message === "Owned host still listening after cleanup") throw error; }
    }
    await rm(buildDirectory, { recursive: true, force: true });
    evidence.cleanup = true;
  } catch (error) { evidence.cleanup_error = String(error); evidence.success = false; process.exitCode = 1; }
  evidence.stages.teardown = (performance.now() - started) / 1000;
  await writeFile(join(runDirectory, "evidence.json"), JSON.stringify(evidence, null, 2) + "\n");
  console.log(`Controlled browser evidence: ${runDirectory}`);
  if (evidence.error) console.error(evidence.error);
  clearTimeout(killDeadline);
  process.off("SIGINT", cancel); process.off("SIGTERM", cancel);
}
