// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { readFileSync, mkdirSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { controlledEnvironment } from "../../../scripts/controlled-browser-host.mjs";
import { discoverControlledUnitFiles, validateControlledUnitInventory, runControlledUnitSuite, verifyControlledUnitResults } from "../test-unit.mjs";

const directories: string[] = [];
afterEach(() => { for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true }); vi.restoreAllMocks(); });
function temporary() { const directory = mkdtempSync(join(tmpdir(), "tempo-controlled-registration-")); directories.push(directory); return directory; }
const selectedFile = "tests/controlled-browser/unit/registration-regressions.test.ts";
function passingReport(title = "owned selected case") {
  return { success: true, numFailedTestSuites: 0, numFailedTests: 0, numPassedTests: 1, numTotalTests: 1,
    numPendingTests: 0, numTodoTests: 0,
    testResults: [{ name: resolve(selectedFile), assertionResults: [{ fullName: title, status: "passed" }] }] };
}

it("controlled_browser_supported_aliases_use_the_owned_runner_without_setup_hooks", () => {
  const { scripts } = JSON.parse(readFileSync("package.json", "utf8"));
  expect(scripts["test:ui-isolated"]).toBe("node scripts/test-controlled-browser.mjs");
  expect(scripts["test:ui-isolated:list"]).toBe("node scripts/test-controlled-browser.mjs --list");
  for (const alias of ["test:ui-isolated", "test:ui-isolated:list"])
    for (const hook of ["pre", "post"]) expect(scripts[hook + alias]).toBeUndefined();
});

it("controlled_browser_registered_owning_suite_lists_without_setup", () => {
  const guard = join(temporary(), "no-provision.mjs");
  writeFileSync(guard, `import children from "node:child_process"; import fs from "node:fs"; import net from "node:net"; import {syncBuiltinESMExports} from "node:module";
    const forbidden = () => { throw new Error("Listing provisioned a child, output or service"); };
    children.spawnSync = forbidden; children.spawn = forbidden; fs.mkdtempSync = forbidden; net.Server.prototype.listen = forbidden; syncBuiltinESMExports();`);
  const result = spawnSync(process.execPath, ["--import", guard, "tools/component-build/test-lane.mjs", "controlled-browser", "--list"], {
    env: { ...controlledEnvironment(process.env), NODE_ENV: "test" }, encoding: "utf8",
  });
  expect(result.status, result.stderr).toBe(0);
  const report = JSON.parse(result.stdout);
  expect(report.selectedFiles).toEqual(discoverControlledUnitFiles());
});

it("controlled_browser_discovery_rejects_missing_duplicate_and_extra_registration", () => {
  const root = temporary();
  mkdirSync(join(root, "tests/controlled-browser/unit"), { recursive: true });
  const file = "tests/controlled-browser/unit/example.test.ts";
  writeFileSync(join(root, file), "");
  expect(validateControlledUnitInventory([file], root)).toEqual([file]);
  for (const registered of [[], [file, file], [file, "tests/controlled-browser/unit/stale.test.ts"]])
    expect(() => validateControlledUnitInventory(registered, root)).toThrow("controlled unit ownership");
  writeFileSync(join(root, "tests/controlled-browser/unit/new.test.ts"), "");
  expect(() => validateControlledUnitInventory([file], root)).toThrow("controlled unit ownership");
});

it("controlled_browser_listing_and_rejected_browser_selections_do_not_provision", () => {
  const guard = join(temporary(), "browser-no-provision.mjs");
  writeFileSync(guard, `import children from "node:child_process"; import fs from "node:fs/promises"; import net from "node:net"; import {syncBuiltinESMExports} from "node:module";
    const collect = children.spawnSync;
    children.spawnSync = (executable,args,options) => {
      if (executable !== process.execPath || args[0] !== "node_modules/@playwright/test/cli.js" || !args.includes("--list")) throw new Error("Unexpected setup child");
      return collect(executable,args,options);
    };
    const forbidden = () => { throw new Error("Browser selection provisioned output or service"); };
    children.spawn = forbidden; fs.mkdir = forbidden; fs.mkdtemp = forbidden; net.Server.prototype.listen = forbidden; syncBuiltinESMExports();`);
  for (const args of [["--list"], ["phone.spec.ts", "--project=firefox"], ["missing.spec.ts"], ["phone.spec.ts", "phone.spec.ts"], ["--update-snapshots"], ["--grep=absent-required-case"]]) {
    const result = spawnSync(process.execPath, ["--import", guard, "scripts/test-controlled-browser.mjs", ...args], {
      env: { ...controlledEnvironment(process.env), NODE_ENV: "test" }, encoding: "utf8",
    });
    expect(result.stderr).not.toMatch(/provisioned|Unexpected setup child/);
    expect(result.status).toBe(args[0] === "--list" ? 0 : 1);
    if (args[0] === "--list") expect(JSON.parse(result.stdout).selectedIdentities.length).toBeGreaterThan(0);
  }
});

it("controlled_browser_central_dispatch_uses_dedicated_configuration_and_one_sanitized_build", () => {
  const probe = join(temporary(), "dispatch-probe.mjs");
  writeFileSync(probe, `import children from "node:child_process"; import {writeFileSync} from "node:fs"; import {resolve} from "node:path"; import {syncBuiltinESMExports} from "node:module";
    let builds = 0;
    children.spawnSync = (executable, args, options) => {
      if (executable !== process.execPath || options.env.NODE_OPTIONS || options.env.DATABASE_URL || options.env.PYTHONPATH || options.env.HTTP_PROXY) throw new Error("Unsafe controlled command");
      if (args[0] === "node_modules/typescript/bin/tsc") { if (++builds !== 1) throw new Error("Duplicate build"); return {status:0}; }
      if (args[0] !== "node_modules/vitest/vitest.mjs" || args[args.indexOf("--config")+1] !== "tests/controlled-browser/vitest.config.ts" || !args.includes(${JSON.stringify(selectedFile)})) throw new Error("Wrong owner/configuration");
      if (args[1] === "list") return {status:0,stdout:JSON.stringify([{file:resolve(${JSON.stringify(selectedFile)}),name:"owned selected case"}])};
      if (builds !== 1) throw new Error("Missing build");
      writeFileSync(args.find(arg=>arg.startsWith("--outputFile=")).slice(13), JSON.stringify(${JSON.stringify(passingReport())})); return {status:0};
    }; syncBuiltinESMExports();`);
  const result = spawnSync(process.execPath, ["--import", probe, "tools/component-build/test-lane.mjs", "controlled-browser", "registration-regressions", "owned selected case"], {
    env: { ...controlledEnvironment(process.env), NODE_ENV: "test", DATABASE_URL: "forbidden", PYTHONPATH: "forbidden", HTTP_PROXY: "http://forbidden.invalid" }, encoding: "utf8",
  });
  expect(result.status, result.stderr).toBe(0);
  const summary = JSON.parse(result.stdout.trim().split("\n").at(-1)!);
  expect(summary.lane).toBe("controlled-browser-unit");
  expect(summary.commands.map((command: { stage: string }) => command.stage)).toEqual(["package_build", "collection", "execution"]);
  expect(summary.passed).toBe(1);
});

it("controlled_browser_unit_accounting_rejects_missing_duplicate_skipped_and_incomplete_selected_cases", () => {
  const expected = [resolve(selectedFile) + "|owned selected case"];
  expect(verifyControlledUnitResults(passingReport(), expected, [selectedFile])).toHaveLength(1);
  for (const change of [
    (report: ReturnType<typeof passingReport>) => { report.numPendingTests = 1; report.testResults[0].assertionResults[0].status = "pending"; },
    (report: ReturnType<typeof passingReport>) => { report.testResults = []; },
    (report: ReturnType<typeof passingReport>) => { report.testResults.push(report.testResults[0]); },
    (report: ReturnType<typeof passingReport>) => { report.testResults[0].assertionResults.push(report.testResults[0].assertionResults[0]); },
    (report: ReturnType<typeof passingReport>) => { report.numTotalTests = 2; },
    (report: ReturnType<typeof passingReport>) => { report.numTodoTests = 1; },
  ]) { const report = passingReport(); change(report); expect(() => verifyControlledUnitResults(report, expected, [selectedFile])).toThrow(); }
  const filtered = passingReport();
  filtered.testResults[0].assertionResults.push({ fullName: "deliberately excluded", status: "pending" });
  filtered.numTotalTests = 2; filtered.numPendingTests = 1;
  expect(verifyControlledUnitResults(filtered, expected, [selectedFile])).toHaveLength(1);
  expect(() => verifyControlledUnitResults(filtered, [...expected, resolve(selectedFile) + "|deliberately excluded"], [selectedFile])).toThrow();
});

it("controlled_browser_invalid_unit_file_selection_fails_before_build", () => {
  const spawn = vi.fn();
  expect(() => runControlledUnitSuite({ files: [], spawnSync: spawn })).toThrow("selection");
  expect(() => runControlledUnitSuite({ files: ["unknown.test.ts"], spawnSync: spawn })).toThrow("selection");
  expect(spawn).not.toHaveBeenCalled();
});
