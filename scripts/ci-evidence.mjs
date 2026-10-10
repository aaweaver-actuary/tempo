import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { appendFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { planHash } from "./ci-verification-plan.mjs";

// Execution identity excludes orchestration state, never source or suite identity.
export function suiteFingerprint(plan, layer, commands) {
  return planHash({ version: 1, repository: plan.repository, repositoryId: plan.repositoryId,
    pullRequest: plan.pullRequest, head: plan.head, base: plan.base, commit: plan.commit,
    inventoryRevision: plan.inventoryRevision, runtime: plan.runtime, layer, commands,
    core: plan.core?.[layer], regressions: plan.regressionFiles?.[layer],
    tests: layer === "browser" ? plan.collection.filter(test => test.selected).map(test => test.id).sort()
      : layer === "visual" ? plan.pinnedCollection.map(test => test.id).sort() : null,
    stages: plan.jobs[layer]?.planned_stages });
}

export function runtimeMatches(layer, plan, report) {
  const actual = report.environment, required = plan.runtime;
  return !!actual && actual.platform === required.platform && actual.architecture === required.architecture
    && actual.node === `v${required.node}`
    && (!["frontend", "backend"].includes(layer) || actual.python === `Python ${required.python}`)
    && (layer !== "build" || (actual.rust?.startsWith(`rustc ${required.rust} `) && actual.wasmPack === `wasm-pack ${required.wasmPack}`));
}

export function validateExecutionIdentity(plan, layer, report, commands) {
  if (report.version !== 2 || report.executionKey !== suiteFingerprint(plan, layer, commands)
    || report.execution?.kind !== "executed" || !Number.isInteger(report.execution.runId)
    || !Number.isInteger(report.execution.attempt)) throw new Error(`${layer}: missing or mismatched execution identity`);
  if (plan.event !== "local" && (!runtimeMatches(layer, plan, report) || report.execution.runId < 1 || report.execution.attempt < 1)) {
    throw new Error(`${layer}: execution runtime or workflow provenance differs from the plan`);
  }
}

export function validateReuseEvidence(plan, layer, receipt, commands, metadata, validateOriginal) {
  const source = receipt.source, sourcePlan = source?.plan, original = source?.report;
  if (plan.event !== "pull_request" || receipt.version !== 2 || receipt.execution?.kind !== "reused"
    || receipt.planHash !== plan.hash || receipt.commit !== plan.commit || receipt.layer !== layer
    || receipt.status !== "success" || !receipt.completed || !sourcePlan || sourcePlan.version !== 3
    || sourcePlan.hash !== planHash(sourcePlan) || sourcePlan.event !== "pull_request"
    || original?.execution?.kind !== "executed" || original.executionKey !== suiteFingerprint(plan, layer, commands)
    || receipt.executionKey !== original.executionKey) throw new Error(`${layer}: invalid reused evidence or candidate`);
  const { run, job, artifact, latestJobId } = metadata ?? {};
  if (!run || run.status !== "completed" || !["success", "failure"].includes(run.conclusion)
    || run.event !== "pull_request" || run.path !== ".github/workflows/pages.yml"
    || run.repository?.id !== plan.repositoryId || run.repository.full_name !== plan.repository
    || run.head_sha !== plan.head || !run.pull_requests?.some(pr => pr.number === plan.pullRequest)
    || run.id !== source.runId || !job || job.id !== source.jobId || latestJobId !== job.id || job.run_id !== run.id
    || job.run_attempt !== original.execution.attempt || original.execution.runId !== run.id
    || job.name !== `${layer} / verify` || job.status !== "completed" || job.conclusion !== "success"
    || !job.steps?.some(step => step.name === "Run isolated verification layer" && step.status === "completed" && step.conclusion === "success")
    || !job.labels?.includes(plan.runtime.runner)
    || !artifact || artifact.id !== source.artifactId || artifact.name !== `ci-result-${layer}` || artifact.expired
    || !/^sha256:[a-f0-9]{64}$/.test(artifact.digest ?? "") || artifact.digest !== source.digest
    || artifact.workflow_run?.id !== run.id || artifact.workflow_run.repository_id !== plan.repositoryId
    || artifact.workflow_run.head_sha !== plan.head) throw new Error(`${layer}: original GitHub job or artifact is not trustworthy passing evidence`);
  const failures = validateOriginal(sourcePlan, layer, "success", original);
  if (failures.length) throw new Error(`${layer}: original execution failed validation: ${failures.join("; ")}`);
  return true;
}

export function verifiedArtifactBytes(bytes, artifact) {
  const digest = `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
  if (artifact.expired || artifact.digest !== digest) throw new Error("Artifact expired or its digest differs from downloaded bytes");
  return bytes;
}

function githubApi(path, binary = false) {
  const result = spawnSync("gh", ["api", path], { encoding: binary ? null : "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (result.status !== 0) throw new Error("GitHub evidence metadata or artifact unavailable");
  return binary ? result.stdout : JSON.parse(result.stdout);
}

function boundedRunJobs(prefix) {
  const result = githubApi(`${prefix}/jobs?filter=all&per_page=100`);
  if (result.total_count > 100) throw new Error("Job attempt history exceeds bounded inspection; execute freshly");
  return result.jobs;
}

function artifactJson(repository, artifact, member) {
  const bytes = verifiedArtifactBytes(githubApi(`repos/${repository}/actions/artifacts/${artifact.id}/zip`, true), artifact);
  const temporary = mkdtempSync(join(tmpdir(), "tempo-ci-evidence-"));
  try {
    const archive = join(temporary, "artifact.zip"); writeFileSync(archive, bytes);
    const result = spawnSync("unzip", ["-p", archive, member], { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
    if (result.status !== 0) throw new Error("Required report absent from source artifact");
    return JSON.parse(result.stdout);
  } finally { rmSync(temporary, { recursive: true, force: true }); }
}

export function sourceMetadata(repository, source) {
  if (!/^[\w.-]+\/[\w.-]+$/.test(repository ?? "")
    || [source?.runId, source?.jobId, source?.artifactId].some(id => !Number.isSafeInteger(id) || id < 1)) throw new Error("Invalid source provenance identifiers");
  const prefix = `repos/${repository}/actions`;
  const job = githubApi(`${prefix}/jobs/${source.jobId}`);
  const matchingJobs = boundedRunJobs(`${prefix}/runs/${source.runId}`)
    .filter(candidate => candidate.name === job.name).sort((left, right) => right.run_attempt - left.run_attempt);
  return { run: githubApi(`${prefix}/runs/${source.runId}`), job, latestJobId: matchingJobs[0]?.id,
    artifact: githubApi(`${prefix}/artifacts/${source.artifactId}`) };
}

// Newest matching execution is authoritative: never search past its failure.
export function findReusableEvidence(plan, layer, commands, runs, readRun, validateOriginal) {
  for (const run of runs.slice(0, 20)) {
    if (run.id === Number(process.env.GITHUB_RUN_ID) || run.head_sha !== plan.head
      || !run.pull_requests?.some(pr => pr.number === plan.pullRequest)) continue;
    const candidate = readRun(run, layer);
    if (!candidate || candidate.plan?.version !== 3) return null; // Ambiguous/expired evidence cannot authorize an older pass.
    if (suiteFingerprint(candidate.plan, layer, commands) !== suiteFingerprint(plan, layer, commands)) continue;
    if (run.status !== "completed" || !["success", "failure"].includes(run.conclusion)) return null;
    const original = candidate.report;
    if (original?.execution?.kind === "reused") {
      if (run.conclusion !== "success" || candidate.job?.conclusion !== "success" || original.status !== "success" || !original.completed) return null;
      continue; // Always retain a direct original execution, never a receipt chain.
    }
    if (!original || original.status !== "success" || candidate.job?.conclusion !== "success") return null;
    const receipt = { version: 2, layer, commit: plan.commit, planHash: plan.hash, completed: true, status: "success",
      executionKey: original.executionKey, execution: { kind: "reused" },
      source: { plan: candidate.plan, report: original, runId: run.id, jobId: candidate.job.id,
        artifactId: candidate.artifact.id, digest: candidate.artifact.digest } };
    validateReuseEvidence(plan, layer, receipt, commands, { run, job: candidate.job, artifact: candidate.artifact, latestJobId: candidate.job.id }, validateOriginal);
    return receipt;
  }
  return null;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const layer = process.argv[2], plan = JSON.parse(readFileSync("test-results/ci/plan.json", "utf8"));
  let receipt = null;
  try {
    if (plan.event === "pull_request") {
      if (plan.hash !== planHash(plan) || plan.repository !== process.env.GITHUB_REPOSITORY) throw new Error("Current plan identity differs from repository");
      const { layerCommands } = await import("./ci-run-layer.mjs");
      const { layerFailures } = await import("./ci-quality.mjs");
      const runs = githubApi(`repos/${plan.repository}/actions/workflows/pages.yml/runs?event=pull_request&head_sha=${plan.head}&per_page=20`).workflow_runs;
      receipt = findReusableEvidence(plan, layer, layerCommands(layer, plan), runs, run => {
        const prefix = `repos/${plan.repository}/actions/runs/${run.id}`;
        const artifactInventory = githubApi(`${prefix}/artifacts?per_page=100`);
        if (artifactInventory.total_count > 100) throw new Error("Artifact inventory exceeds bounded inspection");
        const artifacts = artifactInventory.artifacts;
        const plans = artifacts.filter(artifact => artifact.name === "ci-plan" && !artifact.expired);
        const reports = artifacts.filter(artifact => artifact.name === `ci-result-${layer}` && !artifact.expired);
        if (plans.length !== 1) return null;
        const sourcePlan = artifactJson(plan.repository, plans[0], "plan.json");
        if (sourcePlan.version !== 3) return null;
        const jobs = boundedRunJobs(prefix).filter(job => job.name === `${layer} / verify`)
          .sort((left, right) => right.run_attempt - left.run_attempt);
        return { plan: sourcePlan, report: reports.length === 1 ? artifactJson(plan.repository, reports[0], `ci/${layer}.json`) : null,
          job: jobs[0], artifact: reports.length === 1 ? reports[0] : null };
      }, layerFailures);
    }
  } catch (error) { console.log(`Reuse unavailable for ${layer}: ${error.message}; execute the suite.`); }
  if (receipt) {
    writeFileSync(`test-results/ci/${layer}.json`, JSON.stringify(receipt, null, 2));
    console.log(`${layer}: verified passing original job ${receipt.source.jobId} from run ${receipt.source.runId}`);
  }
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `reused=${!!receipt}\n`);
}
