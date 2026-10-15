import { realpathSync, readFileSync, readdirSync, mkdtempSync, rmSync, existsSync } from "node:fs";
import { resolve, join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { runNodeFiles } from "./test-node.mjs";
const root = resolve(fileURLToPath(new URL("../../", import.meta.url)));
export const inventory = JSON.parse(readFileSync(resolve(root, "tools/component-build/test-inventory.json"), "utf8"));
export function validateInventory() {
  const declared = ["frontend", "producer-parity", "legacy-runner-wrappers"].flatMap(lane => inventory.lanes[lane]);
  const actual = readdirSync(resolve(root, "tests/unit")).filter(name => /\.test\.[cm]?tsx?$/.test(name)).map(name => "tests/unit/" + name);
  if (new Set(declared).size !== declared.length || declared.sort().join() !== actual.sort().join()) throw new Error("Missing/duplicate test ownership; update test-inventory.json");
  const runnerFiles = readdirSync(resolve(root, "tests/runner")).filter(name => /\.test\.mjs$/.test(name)).map(name => "tests/runner/" + name);
  if (runnerFiles.sort().join() !== [...inventory.lanes.runners].sort().join()) throw new Error("Missing runner test ownership");
  // Minimal repositories may omit this lane entirely. Owning files always require registration.
  const controlledDirectory = "tests/controlled-browser/unit";
  if (existsSync(resolve(root, controlledDirectory)) || Object.hasOwn(inventory.lanes, "controlled-browser")) {
    const controlledFiles = readdirSync(resolve(root, controlledDirectory)).filter(name => name.endsWith(".test.ts"))
      .map(name => controlledDirectory + "/" + name).sort();
    const registered = inventory.lanes["controlled-browser"];
    if (!controlledFiles.length || !Array.isArray(registered) || new Set(registered).size !== registered.length ||
        JSON.stringify([...registered].sort()) !== JSON.stringify(controlledFiles))
      throw new Error("Missing/duplicate/unexpected controlled unit ownership; update test-inventory.json");
  }
  return { unitFiles: actual.length, runnerFiles: runnerFiles.length };
}
export function selectFiles(lane, filter) {
  if (!inventory.lanes[lane]) throw new Error("Unknown test lane: " + lane);
  const files = inventory.lanes[lane].filter(file => !filter || file.includes(filter));
  if (!files.length) throw new Error("Zero selected tests: " + lane);
  return files;
}
export function runLane(lane, filter, nameFilter) {
  console.log(JSON.stringify({ inventory: validateInventory() }));
  const files = selectFiles(lane, filter);
  console.log(JSON.stringify({ lane, selectedFiles: files }));
  if (lane === "controlled-browser") {
    return import("../../tests/controlled-browser/test-unit.mjs")
      .then(({ runControlledUnitSuite }) => runControlledUnitSuite({ files, nameFilter }));
  }
  if (lane === "runners") {
    runNodeFiles(files, nameFilter, { cwd: root, lane });
    return;
  }
  const build = spawnSync("npm", ["run", "build:packages"], { cwd: root, stdio: "inherit" });
  if (build.error || build.status !== 0) throw new Error("Component package build failed");
  const directory = mkdtempSync(join(tmpdir(), "tempo-m02-collection-"));
  try {
    const reportPath = join(directory, "vitest.json");
    const result = spawnSync(process.execPath, [resolve(root, "node_modules/vitest/vitest.mjs"), "run", "--config", "vitest.lanes.config.ts", ...files, ...(nameFilter ? ["-t", nameFilter] : []), "--reporter=json", "--outputFile=" + reportPath], { cwd: root, stdio: "inherit", env: { ...process.env, TEMPO_TEST_LANE: lane } });
    const report = JSON.parse(readFileSync(reportPath, "utf8"));
    const assertions = report.testResults.flatMap(file => file.assertionResults);
    console.log(JSON.stringify({ lane, executedIdentities: assertions.filter(item => item.status === "passed").map(item => item.fullName), passed: report.numPassedTests }));
    if (result.status !== 0 || !report.numPassedTests || report.numFailedTests) throw new Error("Vitest failed or zero selected tests");
  } finally { rmSync(directory, { recursive: true, force: true }); }
}
if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const argumentsList = process.argv.slice(2);
    if (argumentsList.some(argument => ["--component", "--suite"].includes(argument))) {
      const { runComponentSelection } = await import("./component-tests.mjs");
      runComponentSelection(argumentsList);
    } else if (argumentsList.includes("--list")) {
      const [lane, filter] = argumentsList.filter(argument => argument !== "--list");
      console.log(JSON.stringify({ inventory: validateInventory(), lane, selectedFiles: selectFiles(lane, filter) }));
    } else await runLane(process.argv[2], process.argv[3], process.argv[4]);
  }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
