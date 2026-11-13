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
  // Exact prior BUI inventory plus the three FE20 diagnostic regressions.
  const expectedTests = [
    "Board.test.tsx :: BUI-09 Strict Mode constructs clean lifecycles, fences previous callbacks and destroys each instance once",
    "Board.test.tsx :: BUI-10 accessible focus and keyboard seams stay available while read-only blocks imperative input",
    "Board.test.tsx :: BUI-11 heading translation and input capture clear bounds without reconstructing or cancelling a held board",
    "Board.test.tsx :: FE20 the public diagnostic handle is inert after unmount",
    "boundaries.test.ts :: BUI-13 renderer imports stay within its package, React peer, and pinned Chessground",
    "boundaries.test.ts :: BUI-14 package metadata, styles, pieces and attribution form an installable local contract",
    "boundaries.test.ts :: BUI-25 blue theme/black orientation has dark a1/h8 and light h1/a8 across all 64 squares",
    "boundaries.test.ts :: BUI-25 blue theme/white orientation has dark a1/h8 and light h1/a8 across all 64 squares",
    "boundaries.test.ts :: BUI-25 brown theme/black orientation has dark a1/h8 and light h1/a8 across all 64 squares",
    "boundaries.test.ts :: BUI-25 brown theme/white orientation has dark a1/h8 and light h1/a8 across all 64 squares",
    "boundaries.test.ts :: BUI-25 green theme/black orientation has dark a1/h8 and light h1/a8 across all 64 squares",
    "boundaries.test.ts :: BUI-25 green theme/white orientation has dark a1/h8 and light h1/a8 across all 64 squares",
    "chessground.test.tsx :: BUI-15 real Chessground legal destinations and read-only permissions constrain the keyboard seam",
    "chessground.test.tsx :: BUI-15 real Chessground preserves selection across equivalent renders and delivers the latest legal handler once",
    "chessground.test.tsx :: BUI-15 real deferred Chessground moves are fenced and pieces restored by same-FEN identity replacement",
    "controller.test.ts :: BUI-06 nested shape inputs, outputs, defaults, and destination maps are detached from Chessground mutation",
    "controller.test.ts :: BUI-07 translation clears cached bounds without cancellation; editor transition refreshes geometry without rebuilding DOM",
    "controller.test.ts :: BUI-08 responsive sizes have integral squares and respect bounds including zero-width containers",
    "controller.test.ts :: BUI-12 identity replacement cancels a live drawing before installing the replacement handler",
    "controller.test.ts :: FE20 diagnostic events use the latest handler and stop on disposal",
    "controller.test.ts :: FE20 diagnostic snapshots are detached and unavailable after disposal",
    "controller.test.ts :: held input and authoritative position BUI-01 equivalent props and fresh callbacks preserve held input and forward the latest handler once",
    "controller.test.ts :: held input and authoritative position BUI-02 annotations, appearance, check and last move never reapply FEN or cancel held input",
    "controller.test.ts :: held input and authoritative position BUI-03 FEN replacement cancels input, restores FEN, and fences stale move/select/drawing callbacks",
    "controller.test.ts :: held input and authoritative position BUI-03 changed destinations cancels input, restores FEN, and fences stale move/select/drawing callbacks",
    "controller.test.ts :: held input and authoritative position BUI-03 drawing lock cancels input, restores FEN, and fences stale move/select/drawing callbacks",
    "controller.test.ts :: held input and authoritative position BUI-03 free mode cancels input, restores FEN, and fences stale move/select/drawing callbacks",
    "controller.test.ts :: held input and authoritative position BUI-03 lock cancels input, restores FEN, and fences stale move/select/drawing callbacks",
    "controller.test.ts :: held input and authoritative position BUI-03 orientation cancels input, restores FEN, and fences stale move/select/drawing callbacks",
    "controller.test.ts :: held input and authoritative position BUI-03 read-only cancels input, restores FEN, and fences stale move/select/drawing callbacks",
    "controller.test.ts :: held input and authoritative position BUI-03 same-FEN identity replacement cancels input, restores FEN, and fences stale move/select/drawing callbacks",
    "controller.test.ts :: held input and authoritative position BUI-03 same-FEN revision replacement cancels input, restores FEN, and fences stale move/select/drawing callbacks",
    "controller.test.ts :: held input and authoritative position BUI-03 select mode cancels input, restores FEN, and fences stale move/select/drawing callbacks",
    "controller.test.ts :: held input and authoritative position BUI-03 turn cancels input, restores FEN, and fences stale move/select/drawing callbacks",
    "controller.test.ts :: held input and authoritative position BUI-04 free/select modes route only their typed event, and lock/unlock replaces the input generation",
    "controller.test.ts :: held input and authoritative position BUI-05 obsolete callbacks after explicit cancellation and destruction cannot affect current handlers",
    "repairs.test.tsx :: BUI-16 drawnShapes labels are literal, including markup, executable attributes and entity-like text",
    "repairs.test.tsx :: BUI-16 shapes labels are literal, including markup, executable attributes and entity-like text",
    "repairs.test.tsx :: BUI-17 drawnShapes discards undeclared nested fields and detaches caller/render/event snapshots",
    "repairs.test.tsx :: BUI-17 shapes discards undeclared nested fields and detaches caller/render/event snapshots",
    "repairs.test.tsx :: BUI-18 drawnShapes reconciles label fill/text/removal/plane and preserves a held piece",
    "repairs.test.tsx :: BUI-18 shapes reconciles label fill/text/removal/plane and preserves a held piece",
    "repairs.test.tsx :: BUI-19 destruction during held input (button 0) stops frames, callbacks and DOM mutation",
    "repairs.test.tsx :: BUI-19 destruction during held input (button 2) stops frames, callbacks and DOM mutation",
    "repairs.test.tsx :: BUI-20 pending animation/redraw/deferred move cannot mutate a destroyed or replacement instance",
    "repairs.test.tsx :: BUI-21 initially disabled features survive permission/mode/orientation transitions ({\"drawingEnabled\":false})",
    "repairs.test.tsx :: BUI-21 initially disabled features survive permission/mode/orientation transitions ({\"locked\":true})",
    "repairs.test.tsx :: BUI-21 initially disabled features survive permission/mode/orientation transitions ({\"mode\":\"select\"})",
    "repairs.test.tsx :: BUI-21 initially disabled features survive permission/mode/orientation transitions ({\"readOnly\":true})",
    "repairs.test.tsx :: BUI-21 initially disabled features survive permission/mode/orientation transitions ({})",
    "repairs.test.tsx :: BUI-22 Strict Mode, mode/orientation changes and retained observer callbacks leave no live resources",
    "repairs.test.tsx :: BUI-23 real drawing completion emits detached literal labels and accepts its snapshot without double encoding",
    "repairs.test.tsx :: BUI-24 obsolete DOM generation redraws and input listeners cannot affect the current orientation"
  ];
  const actualTests = unitResults.testResults.flatMap(file => file.assertionResults.map(test =>
    `${basename(file.name)} :: ${test.fullName}`)).sort();
  assert.deepEqual(actualTests, expectedTests, "Execute the exact board regression inventory");
  assert.equal(unitResults.numTotalTests, 53);
  assert.equal(unitResults.numPassedTests, 53);
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
