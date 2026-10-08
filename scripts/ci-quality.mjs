import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { layerCommands, validatePostgresScenarios } from "./ci-run-layer.mjs";
import { allLayers } from "./ci-verification-plan.mjs";

export function evaluateQuality(plan, needs, reports) {
  const failures = [], nonblocking = [];
  if (!plan || plan.version !== 1 || !plan.hash || needs.plan?.result !== "success") failures.push("Required plan did not succeed or its report is missing");
  if (!plan) return { success: false, failures, nonblocking };
  if (plan.scope === "complete" && (!plan.jobs.lifecycle?.applicable || !plan.jobs.lifecycle.required)) {
    failures.push("Complete verification must require deployment lifecycle");
  }
  for (const layer of allLayers) {
    const planned = plan.jobs[layer];
    const result = needs[layer]?.result;
    if (!planned) { failures.push(`${layer}: missing plan classification`); continue; }
    if (!planned.applicable) {
      if (result !== "skipped") failures.push(`${layer}: inapplicable job must be explicitly skipped and reported`);
      continue;
    }
    if (layer === "lifecycle" && !planned.required) failures.push("lifecycle: selected verification must be mandatory");
    if (result === undefined || ["cancelled", "skipped"].includes(result)) failures.push(`${layer}: applicable work is missing, cancelled or unexpectedly skipped`);
    const report = reports[layer];
    if (!report || report.layer !== layer || report.planHash !== plan.hash || report.commit !== plan.commit || !report.completed || !report.commands?.length) {
      failures.push(`${layer}: missing, incomplete or mismatched result report`); continue;
    }
    let expectedCommands;
    try { expectedCommands = layerCommands(layer, plan).map(([name]) => name); }
    catch (error) { failures.push(`${layer}: ${error.message}`); continue; }
    if (JSON.stringify(expectedCommands) !== JSON.stringify(report.commands.map(command => command.name))) failures.push(`${layer}: absent required command results`);
    if (planned.required) {
      if (result !== "success" || report.status !== "success" || report.commands.some(command => command.exit_code !== 0 || command.error)) failures.push(`${layer}: mandatory verification ${result ?? "absent"}`);
    } else if (result !== "success" || report.status !== "success") nonblocking.push(`${layer}: confirmed harness failures remain visible (${result ?? "absent"})`);
    if (["frontend", "backend"].includes(layer) && !report.test_count) failures.push(`${layer}: no unit test results`);
    if (["postgres", "lifecycle"].includes(layer)) {
      try { validatePostgresScenarios(layer, plan, report.scenarios); }
      catch (error) { failures.push(error.message); }
    }
    if (layer === "browser") {
      const expected = plan.collection.filter(item => item.selected).map(item => item.id).sort();
      const observed = (report.tests ?? []).map(item => item.id).sort();
      if (JSON.stringify(expected) !== JSON.stringify(observed) || !expected.length || report.tests.some(test => test.status !== "passed" || test.retries !== 0)) failures.push("browser: missing, skipped, retried or failed selected tests");
    }
    if (layer === "visual") {
      const expected = plan.pinnedCollection.map(item => item.id).sort();
      if (!expected.length || JSON.stringify(expected) !== JSON.stringify((report.tests ?? []).map(item => item.id).sort())
        || report.tests.some(test => test.status !== "passed" || test.retries !== 0)) failures.push("visual: missing, skipped, retried or failed pinned tests");
    }
  }
  return { success: failures.length === 0, failures, nonblocking };
}

export function deploymentAllowed({ event, ref, scope, quality, verificationOnly = true }) {
  return quality === "success" && scope === "complete" && ref === "refs/heads/main"
    && (event === "push" || (event === "workflow_dispatch" && !verificationOnly));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const planPath = "test-results/ci/plan.json";
  const plan = existsSync(planPath) ? JSON.parse(readFileSync(planPath, "utf8")) : null;
  const reports = {};
  for (const layer of allLayers) {
    const path = join("test-results/ci", `${layer}.json`);
    if (existsSync(path)) reports[layer] = JSON.parse(readFileSync(path, "utf8"));
  }
  const verdict = evaluateQuality(plan, JSON.parse(process.env.TEMPO_CI_NEEDS ?? "{}"), reports);
  console.log(JSON.stringify({ verdict, jobs: plan?.jobs }, null, 2));
  if (process.env.GITHUB_STEP_SUMMARY) {
    const { appendFileSync } = await import("node:fs");
    const rows = Object.entries(reports).map(([layer, report]) => `| ${layer} | ${report.status} | ${report.test_count ?? report.tests?.length ?? "—"} | ${(report.commands ?? []).reduce((seconds, command) => seconds + (command.duration_seconds ?? 0), 0).toFixed(2)} |`).join("\n");
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, `\n| Layer | Result | Cases | Command seconds |\n| --- | --- | --- | --- |\n${rows}\n\nCommand times include setup and do not sum to parallel workflow wall time. Source: ${plan?.commit ?? "missing"}; scope: ${plan?.scope ?? "missing"}.\n`);
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, `Quality: **${verdict.success ? "passed" : "failed"}**\n\n${[...verdict.failures, ...verdict.nonblocking].map(item => `- ${item}`).join("\n")}\n\nExplicit inapplicability: ${Object.entries(plan?.jobs ?? {}).filter(([, job]) => !job.applicable).map(([name, job]) => `${name} (${job.reason})`).join("; ")}\n`);
  }
  process.exitCode = verdict.success ? 0 : 1;
}
