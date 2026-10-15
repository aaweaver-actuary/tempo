import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { controlledEnvironment } from "../../scripts/controlled-browser-host.mjs";
const root = fileURLToPath(new URL("../../", import.meta.url));
process.chdir(root);
const directory = mkdtempSync(join(tmpdir(), "tempo-controlled-unit-"));
try {
  const environment = controlledEnvironment(process.env);
  for (const packageName of ["chess-line", "browser-intents", "study-session"]) {
    const build = spawnSync(process.execPath, ["node_modules/typescript/bin/tsc", "-p", `packages/${packageName}/tsconfig.json`], { cwd: root, env: environment, stdio: "inherit" });
    if (build.error || build.status !== 0) throw new Error(packageName + " prerequisite failed");
  }
  const reportPath = join(directory, "vitest.json");
  const result = spawnSync(process.execPath, ["node_modules/vitest/vitest.mjs", "run", "--config", "tests/controlled-browser/vitest.config.ts", "--reporter=json", "--outputFile=" + reportPath], { cwd: root, env: environment, stdio: "inherit" });
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  const identities = report.testResults.flatMap(file => file.assertionResults.map(test => ({ file: file.name, title: test.fullName, status: test.status })));
  if (result.error || result.status !== 0 || !report.success || report.numFailedTestSuites || !report.numPassedTests || report.numFailedTests || report.numPendingTests || report.numTodoTests || identities.length !== report.numPassedTests || identities.some(test => test.status !== "passed"))
    throw new Error("Controlled unit suite failed, skipped cases, or selected zero cases");
  console.log(JSON.stringify({ lane: "controlled-browser-unit", passed: identities.length, executedIdentities: identities }));
} catch (error) { console.error(error.message); process.exitCode = 1; }
finally { rmSync(directory, { recursive: true, force: true }); }
