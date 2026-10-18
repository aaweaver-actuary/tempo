import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { access, cp, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = fileURLToPath(new URL(".", import.meta.url));
const output = resolve(process.argv[2] ?? join(packageRoot, "evidence", `qualification-${Date.now()}`));
await mkdir(output, { recursive: true });
await writeFile(join(output, "running.json"), JSON.stringify({ started: new Date().toISOString() }), { flag: "wx" });
const receipts = [];
const sha256 = content => createHash("sha256").update(content).digest("hex");
async function hashInputs(directory, prefix = "") {
  const result = {};
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    if (["node_modules", "dist", "evidence", "browser-results", ".git"].includes(entry.name)) continue;
    const name = prefix + entry.name;
    if (entry.isDirectory()) Object.assign(result, await hashInputs(join(directory, entry.name), name + "/"));
    else result[name] = sha256(await readFile(join(directory, entry.name)));
  }
  return result;
}
function run(label, command, args, cwd) {
  const start = performance.now();
  const result = spawnSync(command, args, { cwd, encoding: "utf8", timeout: 120000,
    env: { ...process.env, npm_config_workspaces: "false", npm_config_audit: "false", npm_config_fund: "false" },
    maxBuffer: 4 * 1024 * 1024 });
  const receipt = { label, command: [command, ...args], cwd, exitCode: result.status,
    seconds: (performance.now() - start) / 1000, error: result.error?.message };
  receipts.push(receipt);
  const log = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  return writeFile(join(output, `${label}.log`), log).then(() => {
    console.log(`${label}: exit ${result.status}, ${receipt.seconds.toFixed(2)}s`);
    if (result.error || result.status !== 0) throw new Error(`${label} failed; see ${join(output, `${label}.log`)}`);
    return result.stdout;
  });
}

const inputHashes = await hashInputs(packageRoot);
const staging = await mkdtemp(join(tmpdir(), "tempo-board-ui-qualify-"));
const producer = join(staging, "producer");
const consumer = join(staging, "consumer");
let outcome = "failed";
try {
  // Only the package is present. Application, services, root lock/config, and Rust are never copied.
  await cp(packageRoot, producer, { recursive: true,
    filter: source => !["node_modules", "dist", "evidence", "browser-results"].includes(basename(source)) });
  await run("producer-install", "npm", ["ci", "--offline", "--ignore-scripts", "--workspaces=false"], producer);
  await run("producer-typecheck", "npm", ["run", "typecheck"], producer);
  await run("producer-tests", "npm", ["test", "--", "--reporter=json", "--outputFile=" + join(output, "unit-results.json")], producer);
  const unitResults = JSON.parse(await readFile(join(output, "unit-results.json"), "utf8"));
  assert.equal(unitResults.numTotalTests, 50, "Execute the complete local regression inventory");
  assert.equal(unitResults.numPassedTests, 50);
  assert.equal(unitResults.numFailedTests, 0);
  assert.equal(unitResults.numPendingTests, 0);
  assert.equal(unitResults.numTodoTests, 0);
  assert.equal(unitResults.testResults.length, 5);
  await run("producer-build", "npm", ["run", "build"], producer);
  const packResult = JSON.parse(await run("producer-pack", "npm", ["pack", "--ignore-scripts", "--json", "--pack-destination", output], producer));
  const tarball = join(output, packResult[0].filename);
  const tarballHash = sha256(await readFile(tarball));
  await cp(join(packageRoot, "consumer"), consumer, { recursive: true, filter: source => basename(source) !== "dist" });
  const manifest = JSON.parse(await readFile(join(consumer, "package.json"), "utf8"));
  manifest.dependencies["@tempo/board-ui"] = "file:" + tarball;
  await writeFile(join(consumer, "package.json"), JSON.stringify(manifest, null, 2));
  await run("consumer-install", "npm", ["install", "--offline", "--ignore-scripts", "--workspaces=false"], consumer);
  // Removal is restricted to fresh disposable staging. The worktree is never modified.
  assert(staging.startsWith(join(tmpdir(), "tempo-board-ui-qualify-")));
  await rm(join(producer, "src"), { recursive: true });
  await rm(join(consumer, "node_modules/@tempo/board-ui/src"), { recursive: true });
  await assert.rejects(access(join(producer, "src")));
  await assert.rejects(access(join(consumer, "node_modules/@tempo/board-ui/src")));
  await run("consumer-typecheck", process.execPath, [join(consumer, "node_modules/typescript/bin/tsc"), "-p", "tsconfig.json"], consumer);
  await run("consumer-smoke", process.execPath, ["smoke.mjs"], consumer);
  await run("consumer-build", process.execPath, ["build.mjs"], consumer);
  await cp(join(consumer, "dist"), join(output, "consumer-dist"), { recursive: true });
  await cp(join(consumer, "package-lock.json"), join(output, "consumer-package-lock.json"));
  assert.deepEqual(await hashInputs(packageRoot), inputHashes, "Qualification changed producer source inputs");
  await writeFile(join(output, "artifact.json"), JSON.stringify({ filename: basename(tarball), sha256: tarballHash,
    integrity: packResult[0].integrity, files: packResult[0].files, sourceRemovedOnlyIn: staging }, null, 2));
  outcome = "passed";
} finally {
  if (outcome === "passed") await rm(staging, { recursive: true });
  await writeFile(join(output, "qualification.json"), JSON.stringify({ outcome, node: process.version,
    platform: process.platform, architecture: process.arch, inputHashes, receipts,
    browser: "not run: requires parent resource grant", staging, stagingRemoved: outcome === "passed",
    finished: new Date().toISOString() }, null, 2));
  console.log(`Evidence retained: ${output}`);
  // Failed staging is retained for diagnosis. Successful evidence includes the built consumer.
}
