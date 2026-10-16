import {managedDockerEnvironment} from '../../scripts/development-docker.mjs';
import {recordBudgetResourceSync} from '../../scripts/development-budget.mjs';
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { spawnSync, execFileSync } from "node:child_process";
import { performance } from "node:perf_hooks";
import { repository, validateManifest, validateSuite } from "./check-components.mjs";
import { parseSelection, readManifest, selectComponents, selectSuites } from "./selection.mjs";

export function componentSelectionNeedsBudget(argumentsList, root = process.env.TEMPO_COMPONENT_ROOT ?? repository) {
  const options = parseSelection(argumentsList);
  if(options.list) return false;
  const manifest = readManifest(root);
  const suites = [...(options.componentIds ? selectComponents(manifest,options.componentIds).flatMap(component=>component.tests) : []),
    ...(options.suiteIds ? selectSuites(manifest,options.suiteIds) : [])];
  return suites.some(suite=>suite.kind === 'postgres');
}

const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
export function runComponentSelection(argumentsList, root = process.env.TEMPO_COMPONENT_ROOT ?? repository) {
  const options = parseSelection(argumentsList);
  if (options.positional.length || options.consumers) throw new Error("Unexpected component test argument");
  const manifest = readManifest(root);
  const components = options.componentIds ? selectComponents(manifest, options.componentIds) : [];
  const shared = options.suiteIds ? selectSuites(manifest, options.suiteIds) : [];
  const selected = [...components.flatMap(component => component.tests.map(suite => ({ ...suite, id: component.id, cwd: suite.cwd ?? component.root }))), ...shared];
  if (!selected.length) throw new Error("Zero selected suites");
  validateManifest(manifest, root, { componentIds: components.map(component => component.id), suiteIds: options.suiteIds });
  for (const suite of selected) validateSuite(suite, root);
  if (options.list) { console.log(JSON.stringify({ selected }, null, 2)); return; }
  const python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3";
  for (const suite of selected) {
    const reports = mkdtempSync(join(tmpdir(), "tempo-d-cases-"));
    const started = performance.now();
    let managedEnvironment;
    try {
      let environment = { ...process.env, PYTHONPATH: "", PYTHONDONTWRITEBYTECODE: "1", PYTEST_DISABLE_PLUGIN_AUTOLOAD: "1" };
      delete environment.NODE_TEST_CONTEXT;
      delete environment.PYTHONHOME;
      if (suite.kind === 'postgres' && process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN) {
        environment = managedDockerEnvironment(environment,reports); managedEnvironment=environment;
        recordBudgetResourceSync('component-suite:'+suite.id,true);
      }
      let command = suite.command.replace(/^python(?=\s)/, quote(python));
      if (suite.kind === "python") command += " --junitxml=" + quote(join(reports, "cases.xml"));
      if (suite.kind === "postgres") environment.PYTEST_ADDOPTS = "--junitxml=" + join(reports, "cases.xml");
      if (suite.kind === "contract-exchange") command = [python, resolve(repository, "tools/component-build/contract-exchange.py"), resolve(root, suite.cwd), reports].map(quote).join(" ");
      const result = spawnSync("/bin/sh", ["-c", command], { cwd: resolve(root, suite.cwd), env: environment, encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
      process.stdout.write(result.stdout ?? "");
      process.stderr.write(result.stderr ?? "");
      if (result.error || result.status !== 0) throw new Error("Owning suite failed: " + suite.id + ": " + (result.error?.message ?? result.status));
      let report;
      if (suite.kind === "frontend" || suite.kind === "node") {
        report = (result.stdout ?? "").trim().split("\n").filter(line => line.startsWith("{")).map(line => JSON.parse(line)).findLast(value => Array.isArray(value.executedIdentities));
        if (!report?.success || !report.passed || report.failed || report.executedIdentities.length !== report.passed) throw new Error("Missing/zero/failed structured owning cases: " + suite.id);
      } else {
        const parsed = spawnSync(python, [resolve(repository, "tools/component-build/case-results.py"), reports], { encoding: "utf8", env: environment });
        if (parsed.error || parsed.status !== 0) throw new Error(parsed.error?.message ?? parsed.stdout + parsed.stderr);
        report = JSON.parse(parsed.stdout);
      }
      console.log(JSON.stringify({ suite: suite.id, cwd: suite.cwd, command: suite.command, seconds: (performance.now() - started) / 1000, ...report }));
    } finally {
      if(managedEnvironment) {
        const remaining=execFileSync(managedEnvironment.TEMPO_REAL_DOCKER,['ps','-aq','--filter',`label=tempo.resource-owner=${process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN}`],{encoding:'utf8'}).trim();
        if(remaining) throw new Error('Managed component resources still need verified cleanup; retaining the development budget');
        recordBudgetResourceSync('component-suite:'+suite.id,false);
      }
      rmSync(reports, { recursive: true, force: true });
    }
  }
}
