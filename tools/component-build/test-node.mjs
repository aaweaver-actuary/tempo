import { readdirSync, statSync } from "node:fs";
import { resolve, join } from "node:path";
import { spawnSync } from "node:child_process";

export function testFiles(directory) {
  return readdirSync(directory).sort().flatMap(name => {
    const path = join(directory, name);
    return statSync(path).isDirectory() ? testFiles(path) : /\.test\.mjs$/.test(name) ? [path] : [];
  });
}
export function runTests(directory, filter) {
  const files = testFiles(directory);
  if (!files.length) throw new Error("Zero selected tests: " + directory);
  const result = spawnSync(process.execPath, ["--test", "--test-reporter=tap", ...(filter ? ["--test-name-pattern=" + filter] : []), ...files], { encoding: "utf8" });
  process.stdout.write(result.stdout ?? "");
  process.stderr.write(result.stderr ?? "");
  const passed = [...(result.stdout ?? "").matchAll(/^ok \d+ - (.+)$/gm)].filter(match => !/\.test\.mjs(?: #.*)?$/.test(match[1]) && !match[1].includes("# SKIP")).length;
  const identities = [...(result.stdout ?? "").matchAll(/^ok \d+ - (.+)$/gm)].map(match => match[1]);
  console.log(JSON.stringify({ lane: "node", collectedFiles: files, executedIdentities: identities, passed }));
  if (result.status !== 0 || !passed) throw new Error(result.status === 0 ? "Zero selected tests" : "Node tests failed");
}
if (process.argv[1] && resolve(process.argv[1]) === new URL(import.meta.url).pathname) {
  try { runTests(resolve(process.argv[2] ?? "tests"), process.argv[3]); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
