import { realpathSync, readdirSync, statSync } from "node:fs";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

export function testFiles(directory) {
  return readdirSync(directory).sort().flatMap(name => {
    const path = join(directory, name);
    return statSync(path).isDirectory() ? testFiles(path) : /\.test\.mjs$/.test(name) ? [path] : [];
  });
}
// Intentionally mirrored in the independently installed chess-line test runner.
export function accountNodeEvents(output) {
  const report = { executedIdentities: [], passed: 0, failed: 0, skipped: 0, todo: 0 };
  let finalSummary;
  let lastEvent;
  const lines = output.trim().split("\n").filter(Boolean);
  for (const line of lines) {
    let event;
    try { event = JSON.parse(line); }
    catch { throw new Error("Malformed Node reporter output"); }
    if (!event || !event.data || !["test:pass", "test:fail", "test:summary", "test:stdout", "test:stderr", "test:diagnostic"].includes(event.type)) {
      throw new Error("Malformed Node reporter event");
    }
    lastEvent = event;
    const data = event.data;
    if (event.type === "test:stdout" || event.type === "test:stderr") {
      if (typeof data.message !== "string") throw new Error("Malformed Node diagnostic");
      (event.type === "test:stdout" ? process.stdout : process.stderr).write(data.message);
      continue;
    }
    if (event.type === "test:diagnostic") continue;
    if (event.type === "test:summary") {
      if (typeof data.success !== "boolean" || !data.counts ||
          !["tests", "passed", "failed", "skipped", "todo", "cancelled", "suites"].every(key => Number.isInteger(data.counts[key]) && data.counts[key] >= 0)) {
        throw new Error("Malformed Node result summary");
      }
      if (!data.file) finalSummary = data;
      continue;
    }
    if (!data.details || !["test", "suite"].includes(data.details.type) || typeof data.name !== "string") {
      throw new Error("Malformed Node case event");
    }
    if (event.type === "test:fail") console.error(JSON.stringify(event));
    if (data.details.type !== "test") continue;
    if (data.name === data.file && data.line === 1 && data.column === 1) continue;
    if (typeof data.file !== "string" || !Number.isInteger(data.line) || !Number.isInteger(data.column) || !Number.isInteger(data.testNumber) || !Number.isInteger(data.nesting)) {
      throw new Error("Missing Node case identity");
    }
    if (data.skip) report.skipped++;
    else if (data.todo) report.todo++;
    else if (event.type === "test:fail") report.failed++;
    else {
      report.passed++;
      report.executedIdentities.push(`${data.file}:${data.line}:${data.column} ${data.name} [case ${report.passed}; ${data.nesting}:${data.testNumber}]`);
    }
  }
  if (!finalSummary || lastEvent?.type !== "test:summary" || lastEvent.data.file) {
    throw new Error("Missing final Node result summary");
  }
  return { ...report, success: report.passed > 0 && report.failed === 0 && finalSummary.success && finalSummary.counts.failed === 0 && finalSummary.counts.cancelled === 0 };
}

export function runNodeFiles(files, filter, { cwd, lane = "node" } = {}) {
  if (!files.length) throw new Error("Zero selected tests");
  const result = spawnSync(process.execPath, ["--test", "--test-reporter=" + fileURLToPath(new URL("./node-test-reporter.mjs", import.meta.url)), ...(filter ? ["--test-name-pattern=" + filter] : []), ...files.map(file => resolve(cwd ?? process.cwd(), file))], { cwd, encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
  process.stderr.write(result.stderr ?? "");
  if (result.error) throw result.error;
  const report = accountNodeEvents(result.stdout ?? "");
  console.log(JSON.stringify({ lane, collectedFiles: files, ...report }));
  if (!report.passed && result.status === 0 && !report.failed) throw new Error("Zero selected tests");
  if (result.status !== 0 || !report.success || report.failed) throw new Error("Node tests failed");
  return report;
}
export function runTests(directory, filter) {
  return runNodeFiles(testFiles(directory), filter);
}
if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { runTests(resolve(process.argv[2] ?? "tests"), process.argv[3]); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
