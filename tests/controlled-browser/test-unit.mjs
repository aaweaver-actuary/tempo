import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";
import { controlledEnvironment } from "../../scripts/controlled-browser-host.mjs";
const root = fileURLToPath(new URL("../../", import.meta.url));
const config = "tests/controlled-browser/vitest.config.ts";
const vitest = "node_modules/vitest/vitest.mjs";

export function discoverControlledUnitFiles(directory = root) {
  const files = readdirSync(join(directory, "tests/controlled-browser/unit"))
    .filter(name => name.endsWith(".test.ts"))
    .map(name => "tests/controlled-browser/unit/" + name).sort();
  if (!files.length) throw new Error("Zero controlled unit files");
  return files;
}
export function validateControlledUnitInventory(registeredFiles, directory = root) {
  const files = discoverControlledUnitFiles(directory);
  if (!Array.isArray(registeredFiles) || new Set(registeredFiles).size !== registeredFiles.length ||
      JSON.stringify([...registeredFiles].sort()) !== JSON.stringify(files))
    throw new Error("Missing/duplicate/unexpected controlled unit ownership; update test-inventory.json");
  return files;
}
const caseIdentity = (file, title, directory) => `${resolve(directory, file)}|${title}`;

export function verifyControlledUnitResults(report, expected, files, directory = root, { nameFilter } = {}) {
  const namePattern = nameFilter === undefined ? undefined : new RegExp(nameFilter);
  if (report?.success !== true || !Array.isArray(report.testResults) ||
      ["numFailedTestSuites", "numFailedTests", "numPassedTests", "numTotalTests", "numPendingTests", "numTodoTests"]
        .some(counter => !Number.isInteger(report[counter]) || report[counter] < 0) ||
      report.numFailedTestSuites || report.numFailedTests)
    throw new Error("Invalid or failed controlled unit report");
  const reportedFiles = report.testResults.map(file => file.name).sort();
  if (JSON.stringify(reportedFiles) !== JSON.stringify(files.map(file => resolve(directory, file)).sort()))
    throw new Error("Missing/duplicate/unexpected controlled unit result files");
  const selected = new Set(expected);
  const assertions = report.testResults.flatMap(file => file.assertionResults.map(test => ({
    // Vitest collection/filtering use " > "; its JSON reporter's fullName uses spaces.
    file: file.name, title: Array.isArray(test.ancestorTitles) && typeof test.title === "string"
      ? [...test.ancestorTitles, test.title].join(" > ") : test.fullName, status: test.status,
  })));
  const identities = assertions.filter(test => selected.has(caseIdentity(test.file, test.title, directory)));
  const excluded = assertions.filter(test => !selected.has(caseIdentity(test.file, test.title, directory)));
  if (!expected.length || selected.size !== expected.length ||
      JSON.stringify(identities.map(test => caseIdentity(test.file, test.title, directory)).sort()) !== JSON.stringify([...expected].sort()) ||
      identities.some(test => test.status !== "passed" || (namePattern && !test.title.match(namePattern))) ||
      excluded.some(test => !namePattern || test.title.match(namePattern) || !["pending", "skipped"].includes(test.status)) ||
      report.numPassedTests !== identities.length || report.numTotalTests !== assertions.length ||
      report.numPendingTests !== excluded.length || report.numTodoTests)
    throw new Error("Controlled unit suite failed, skipped selected cases, or returned incomplete cases");
  return identities;
}

export function runControlledUnitSuite(options = {}) {
  const directory = options.root ?? root;
  const registered = JSON.parse(readFileSync(join(directory, "tools/component-build/test-inventory.json"), "utf8")).lanes["controlled-browser"];
  const owned = validateControlledUnitInventory(registered, directory);
  const files = options.files ?? owned;
  if (!files.length || new Set(files).size !== files.length || files.some(file => !owned.includes(file)))
    throw new Error("Zero/duplicate/unowned controlled unit selection");
  if (options.nameFilter !== undefined) new RegExp(options.nameFilter);
  const environment = controlledEnvironment(process.env);
  const commands = [], stages = {};
  function runNode(stage, args, capture = false) {
    const started = performance.now();
    const result = (options.spawnSync ?? spawnSync)(process.execPath, args, {
      cwd: directory, env: environment, ...(capture ? { encoding: "utf8", maxBuffer: 8 * 1024 * 1024 } : { stdio: "inherit" }),
    });
    stages[stage] = (performance.now() - started) / 1000;
    commands.push({ stage, executable: process.execPath, args, pid: result.pid, exit_code: result.status });
    if (result.error || result.status !== 0) throw new Error(`Controlled unit ${stage} failed: ${result.error?.message ?? result.stderr ?? result.status}`);
    return result;
  }
  // One compiler invocation prepares each installed frontend prerequisite once.
  const projects = ["chess-line", "browser-intents", "study-session"]
    .filter(name => name === "chess-line" || existsSync(join(directory, `packages/${name}/tsconfig.json`)))
    .map(name => `packages/${name}/tsconfig.json`);
  runNode("package_build", ["node_modules/typescript/bin/tsc", "--build", ...projects, "--force"]);
  const filters = [...files, ...(options.nameFilter === undefined ? [] : ["-t", options.nameFilter])];
  const collection = JSON.parse(runNode("collection", [vitest, "list", "--config", config, ...filters, "--json", "--staticParse=false"], true).stdout);
  if (!Array.isArray(collection) || !collection.length || collection.some(test => typeof test.name !== "string" || !files.some(file => resolve(directory, file) === test.file)))
    throw new Error("Zero/invalid collected controlled unit cases");
  const expected = collection.map(test => caseIdentity(test.file, test.name, directory));
  if (new Set(expected).size !== expected.length) throw new Error("Duplicate collected controlled unit cases");
  const reports = mkdtempSync(join(tmpdir(), "tempo-controlled-unit-"));
  let summary;
  try {
    const reportPath = join(reports, "vitest.json");
    runNode("execution", [vitest, "run", "--config", config, ...filters, "--reporter=default", "--reporter=json", "--outputFile=" + reportPath]);
    const report = JSON.parse(readFileSync(reportPath, "utf8"));
    const identities = verifyControlledUnitResults(report, expected, files, directory, { nameFilter: options.nameFilter });
    summary = { lane: "controlled-browser-unit", success: true, passed: identities.length, failed: 0,
      skipped: 0, todo: 0, excluded: report.numPendingTests, selectedFiles: files,
      selectedIdentities: expected, executedIdentities: identities, commands, stages };
  } finally {
    const started = performance.now();
    rmSync(reports, { recursive: true, force: true });
    stages.cleanup = (performance.now() - started) / 1000;
  }
  summary.cleanup = true;
  console.log(JSON.stringify(summary));
  return summary;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    if (args.length > 2 || args.some(argument => argument.startsWith("--"))) throw new Error("Use [file-substring] [test-name] for controlled units");
    const files = discoverControlledUnitFiles().filter(file => !args[0] || file.includes(args[0]));
    runControlledUnitSuite({ files, nameFilter: args[1] });
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
