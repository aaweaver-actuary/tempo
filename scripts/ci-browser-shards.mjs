import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

export const browserTimingProfile = JSON.parse(readFileSync(new URL("./ci-browser-timings.json", import.meta.url), "utf8"));
const digest = value => createHash("sha256").update(JSON.stringify(value)).digest("hex");
const compareNames = (left, right) => left < right ? -1 : left > right ? 1 : 0;
const escapeRegex = text => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function createBrowserShardPlan(collection, profile = browserTimingProfile, requestedCount = 4) {
  if (profile.version !== 1 || !Number.isSafeInteger(profile.fallback_duration_ms) || profile.fallback_duration_ms <= 0
    || Object.values(profile.test_durations_ms ?? {}).some(duration => !Number.isSafeInteger(duration) || duration < 0)) {
    throw new Error("Invalid browser timing profile");
  }
  if (!Number.isInteger(requestedCount) || requestedCount < 1 || requestedCount > 4) throw new Error("Invalid browser shard count");
  const selected = collection.filter(item => item.selected);
  if (!selected.length || new Set(selected.map(item => item.id)).size !== selected.length) throw new Error("Empty or duplicated browser shard inventory");
  const groups = new Map();
  for (const item of selected) {
    const group = groups.get(item.file) ?? { file: item.file, duration: 0, tests: [] };
    group.duration += profile.test_durations_ms[item.id] ?? profile.fallback_duration_ms;
    group.tests.push(item);
    groups.set(item.file, group);
  }
  const count = Math.min(requestedCount, groups.size);
  const shards = Array.from({ length: count }, (_, index) => ({ id: index + 1, estimatedDurationMs: 0, files: [], testIds: [], grep: "" }));
  for (const group of [...groups.values()].sort((left, right) => right.duration - left.duration || compareNames(left.file, right.file))) {
    const shard = [...shards].sort((left, right) => left.estimatedDurationMs - right.estimatedDurationMs || left.id - right.id)[0];
    shard.estimatedDurationMs += group.duration;
    shard.files.push(group.file);
    shard.testIds.push(...group.tests.map(item => item.id));
  }
  for (const shard of shards) {
    shard.files.sort(); shard.testIds.sort();
    shard.grep = selected.filter(item => shard.testIds.includes(item.id)).sort((left, right) => compareNames(left.id, right.id))
      .map(item => item.grep ?? `^${escapeRegex(item.fullTitle)}$`).join("|");
    shard.commands = [["capabilities", "node", ["scripts/check-test-capabilities.mjs", "--docker", "--loopback", "--workspace-mount"]],
      ["browser", "node", ["scripts/test-postgres-docker.mjs", "--mode", "browser", "--browser-grep", shard.grep]]];
  }
  const assignment = { version: 1, algorithm: "longest-spec-first-v1", count, profileHash: digest(profile), shards };
  return { ...assignment, hash: digest(assignment) };
}

export function validateBrowserShardPlan(plan) {
  if (!plan.browserShards) return;
  const expected = createBrowserShardPlan(plan.collection);
  if (plan.tier !== "qualification" || !plan.jobs.browser.applicable || !plan.collection.every(item => item.selected || item.quarantined)
    || JSON.stringify(plan.browserShards) !== JSON.stringify(expected)) throw new Error("Browser shard assignment differs from immutable complete inventory");
}

export function browserShard(plan, shardId) {
  validateBrowserShardPlan(plan);
  const shard = plan.browserShards?.shards.find(item => item.id === shardId);
  if (!shard) throw new Error("Unknown planned browser shard");
  return shard;
}

export function browserShardFingerprint(plan, shardId) {
  const shard = browserShard(plan, shardId);
  return digest({ version: 1, repository: plan.repository, repositoryId: plan.repositoryId, pullRequest: plan.pullRequest,
    head: plan.head, base: plan.base, commit: plan.commit, inventoryRevision: plan.inventoryRevision, runtime: plan.runtime,
    assignmentHash: plan.browserShards.hash, shard });
}

export function browserShardReportPath(shardId) {
  if (!Number.isInteger(shardId) || shardId < 1 || shardId > 4) throw new Error("Invalid browser shard report path");
  return `test-results/ci/browser-shards/${shardId}/report.json`;
}

export function validateBrowserShardReport(plan, shardId, report) {
  const shard = browserShard(plan, shardId);
  if (!report || report.version !== 2 || report.layer !== "browser" || report.shardId !== shardId
    || report.planHash !== plan.hash || report.commit !== plan.commit || report.assignmentHash !== plan.browserShards.hash
    || report.executionKey !== browserShardFingerprint(plan, shardId) || report.execution?.kind !== "executed"
    || !Number.isInteger(report.execution.runId) || !Number.isInteger(report.execution.attempt)
    || !report.completed || report.status !== "success"
    || JSON.stringify(shard.commands) !== JSON.stringify((report.commands ?? []).map(({ name, command, args }) => [name, command, args]))
    || report.commands.some(command => command.exit_code !== 0 || command.error)
    || JSON.stringify(shard.testIds) !== JSON.stringify((report.tests ?? []).map(item => item.id).sort())
    || report.tests.some(item => item.status !== "passed" || item.retries !== 0)) throw new Error(`Browser shard ${shardId}: missing, failed, incomplete or mismatched execution`);
  if (plan.event !== "local" && (report.execution.runId < 1 || report.execution.attempt < 1
    || report.environment?.node !== `v${plan.runtime.node}` || report.environment?.platform !== plan.runtime.platform
    || report.environment?.architecture !== plan.runtime.architecture)) throw new Error(`Browser shard ${shardId}: wrong runtime or provenance`);
}

export function aggregateBrowserShards(plan, reports, execution) {
  validateBrowserShardPlan(plan);
  if (!plan.browserShards || reports.length !== plan.browserShards.count) throw new Error("Expected browser shard is absent or duplicated");
  const tests = [];
  for (const shard of plan.browserShards.shards) {
    const matches = reports.filter(report => report.shardId === shard.id);
    if (matches.length !== 1) throw new Error("Expected browser shard is absent or duplicated");
    const report = matches[0]; validateBrowserShardReport(plan, shard.id, report);
    if (report.execution.runId !== execution.runId || report.execution.attempt > execution.attempt) throw new Error("Browser shard workflow provenance is stale");
    tests.push(...report.tests);
  }
  const expected = plan.collection.filter(item => item.selected).map(item => item.id).sort();
  if (JSON.stringify(expected) !== JSON.stringify(tests.map(item => item.id).sort()) || tests.length !== new Set(tests.map(item => item.id)).size) {
    throw new Error("Aggregated browser identities differ from complete planned inventory");
  }
  return tests.sort((left, right) => compareNames(left.id, right.id));
}

export function validateBrowserShardJobs(plan, reports, jobs) {
  for (const shard of plan.browserShards.shards) {
    const matching = (jobs ?? []).filter(job => job.name === `browser / shard-${shard.id}`)
      .sort((left, right) => right.run_attempt - left.run_attempt || right.id - left.id);
    const job = matching[0], report = reports.find(item => item.shardId === shard.id);
    if (!job || job.status !== "completed" || job.conclusion !== "success" || job.run_id !== report?.execution.runId
      || job.run_attempt !== report.execution.attempt || !job.labels?.includes(plan.runtime.runner)
      || !job.steps?.some(step => step.name === "Run isolated browser shard" && step.status === "completed" && step.conclusion === "success")) {
      throw new Error(`Browser shard ${shard.id}: latest expected GitHub execution is absent, skipped, cancelled or failed`);
    }
  }
}
