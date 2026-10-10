import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { layerCommands, validatePostgresScenarios, validateUnitResults } from "./ci-run-layer.mjs";
import { allLayers, mandatoryLayers, planHash } from "./ci-verification-plan.mjs";
import { sourceMetadata, validateExecutionIdentity, validateReuseEvidence } from "./ci-evidence.mjs";

export function layerFailures(plan, layer, result, report) {
  const failures = [], planned = plan.jobs[layer];
  if (!planned) return [`${layer}: missing plan classification`];
  if (!planned.applicable) return result === "skipped" ? [] : [`${layer}: inapplicable job must be explicitly skipped and reported`];
  if (layer === "lifecycle" && !planned.required) failures.push("lifecycle: selected verification must be mandatory");
  if (result === undefined || ["cancelled", "skipped"].includes(result)) failures.push(`${layer}: applicable work is missing, cancelled or unexpectedly skipped`);
  if (!report || report.layer !== layer || report.planHash !== plan.hash || report.commit !== plan.commit || !report.completed || !report.commands?.length) {
    return [...failures, `${layer}: missing, incomplete or mismatched result report`];
  }
  let expectedCommands;
  try { expectedCommands = layerCommands(layer, plan); validateExecutionIdentity(plan, layer, report, expectedCommands); }
  catch (error) { return [...failures, `${layer}: ${error.message}`]; }
  if (JSON.stringify(expectedCommands) !== JSON.stringify(report.commands.map(({ name, command, args }) => [name, command, args]))) failures.push(`${layer}: absent required command results`);
  if (planned.required && (result !== "success" || report.status !== "success" || report.commands.some(command => command.exit_code !== 0 || command.error))) {
    failures.push(`${layer}: mandatory verification ${result ?? "absent"}`);
  }
  if (["frontend", "backend"].includes(layer)) {
    try { validateUnitResults(layer, plan, report); } catch (error) { failures.push(error.message); }
  }
  if (["postgres", "lifecycle"].includes(layer)) {
    try { validatePostgresScenarios(layer, plan, report.scenarios); } catch (error) { failures.push(error.message); }
  }
  if (["browser", "visual"].includes(layer)) {
    const expected = (layer === "visual" ? plan.pinnedCollection : plan.collection.filter(item => item.selected)).map(item => item.id).sort();
    if (!expected.length || JSON.stringify(expected) !== JSON.stringify((report.tests ?? []).map(item => item.id).sort())
      || report.tests.some(test => test.status !== "passed" || test.retries !== 0)) failures.push(`${layer}: missing, skipped, retried or failed selected tests`);
  }
  return failures;
}

export function evaluateQuality(plan, needs, reports, { development = false, currentPullRequest, verifiedReuse = {} } = {}) {
  const failures = [], nonblocking = [];
  if (!plan || plan.version !== 3 || !plan.hash || needs.plan?.result !== "success") failures.push("Required plan did not succeed or its report is missing");
  if (!plan) return { success: false, failures, nonblocking };
  if (plan.hash !== planHash(plan)) failures.push("Immutable plan hash mismatch");
  if (!development && (plan.tier !== "qualification" || plan.draft)) failures.push("Development evidence or a draft PR cannot qualify for merging");
  if (!development && plan.event === "workflow_dispatch" && plan.ref !== "refs/heads/main") failures.push("Manual branch execution is evidence only; qualify the PR integration candidate when ready");
  if (plan.tier === "qualification") {
    if (mandatoryLayers.some(layer => !plan.jobs[layer]?.applicable || !plan.jobs[layer]?.required)
      || plan.core?.frontend !== "all" || plan.core?.backend !== "all"
      || !plan.collection?.some(item => item.critical)
      || plan.collection.some(item => item.critical && !item.selected)) failures.push("Qualification requires complete core, durability and critical browser coverage");
  }
  if (plan.event === "pull_request") {
    if (!currentPullRequest || currentPullRequest.number !== plan.pullRequest
      || currentPullRequest.base?.repo?.id !== plan.repositoryId || currentPullRequest.base.repo.full_name !== plan.repository
      || currentPullRequest.state !== "open" || currentPullRequest.draft !== plan.draft
      || currentPullRequest.head?.sha !== plan.head || currentPullRequest.base?.sha !== plan.base
      || currentPullRequest.merge_commit_sha !== plan.commit) failures.push("PR identity, state, head, base or integration revision changed; fresh verification required");
  }
  if (plan.tier === "qualification" && plan.scope === "complete" && (!plan.jobs.lifecycle?.applicable || !plan.jobs.lifecycle.required)) failures.push("Complete verification must require deployment lifecycle");
  for (const layer of allLayers) {
    const report = reports[layer], result = needs[layer]?.result;
    if (report?.execution?.kind === "reused") {
      try {
        if (result !== "success" || !plan.jobs[layer]?.applicable) throw new Error(`${layer}: reused layer did not succeed`);
        validateReuseEvidence(plan, layer, report, layerCommands(layer, plan), verifiedReuse[layer], layerFailures);
      } catch (error) { failures.push(error.message); }
    } else failures.push(...layerFailures(plan, layer, result, report));
    if (plan.jobs[layer]?.applicable && !plan.jobs[layer].required && (result !== "success" || report?.status !== "success")) {
      nonblocking.push(`${layer}: confirmed harness failures remain visible (${result ?? "absent"})`);
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
  const reports = {}, verifiedReuse = {}, provenanceFailures = [];
  for (const layer of allLayers) {
    const path = join("test-results/ci", `${layer}.json`);
    if (existsSync(path)) reports[layer] = JSON.parse(readFileSync(path, "utf8"));
    if (reports[layer]?.execution?.kind === "reused") {
      try { verifiedReuse[layer] = sourceMetadata(process.env.GITHUB_REPOSITORY, reports[layer].source); }
      catch (error) { provenanceFailures.push(`${layer}: ${error.message}`); }
    }
  }
  const development = process.argv.includes("--development");
  const currentPath = "test-results/ci/current-pr.json";
  const currentPullRequest = existsSync(currentPath) ? JSON.parse(readFileSync(currentPath, "utf8")) : undefined;
  const revision = (await import("node:child_process")).spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).stdout?.trim();
  const verdict = evaluateQuality(plan, JSON.parse(process.env.TEMPO_CI_NEEDS ?? "{}"), reports, { development, currentPullRequest, verifiedReuse });
  if (revision !== plan?.commit) verdict.failures.push("Aggregation checkout differs from captured integration revision");
  if (process.env.GITHUB_REPOSITORY && plan?.repository !== process.env.GITHUB_REPOSITORY) verdict.failures.push("Aggregation repository differs from captured repository");
  if (process.env.GITHUB_EVENT_NAME && plan?.event !== process.env.GITHUB_EVENT_NAME) verdict.failures.push("Aggregation event differs from captured event");
  verdict.failures.push(...provenanceFailures); verdict.success = verdict.failures.length === 0;
  console.log(JSON.stringify({ verdict, jobs: plan?.jobs }, null, 2));
  if (process.env.GITHUB_STEP_SUMMARY) {
    const { appendFileSync } = await import("node:fs");
    const rows = Object.entries(reports).map(([layer, report]) => {
      const original = report.source?.report ?? report;
      return `| ${layer} | ${report.status} | ${report.execution?.kind} | ${original.test_count ?? original.tests?.length ?? "—"} | ${(original.commands ?? []).reduce((seconds, command) => seconds + (command.duration_seconds ?? 0), 0).toFixed(2)} |`;
    }).join("\n");
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, `\n| Layer | Result | Evidence | Cases | Original command seconds |\n| --- | --- | --- | --- | --- |\n${rows}\n\nSource: ${plan?.commit ?? "missing"}; scope: ${plan?.scope ?? "missing"}. Reused durations describe the original execution.\n`);
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${development ? "Development evidence" : "Quality"}: **${verdict.success ? "passed" : "failed"}**\n\n${[...verdict.failures, ...verdict.nonblocking].map(item => `- ${item}`).join("\n")}\n\nExplicit inapplicability: ${Object.entries(plan?.jobs ?? {}).filter(([, job]) => !job.applicable).map(([name, job]) => `${name} (${job.reason})`).join("; ")}\n`);
  }
  process.exitCode = verdict.success ? 0 : 1;
}
