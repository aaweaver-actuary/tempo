import { aggregateBrowserShards, browserShardFingerprint } from "../../scripts/ci-browser-shards.mjs";

export function passingBrowserShards(plan, runId = 100, attempt = 1) {
  return plan.browserShards.shards.map(shard => ({ version: 2, layer: "browser", shardId: shard.id,
    assignmentHash: plan.browserShards.hash, executionKey: browserShardFingerprint(plan, shard.id),
    planHash: plan.hash, commit: plan.commit, completed: true, status: "success",
    execution: { kind: "executed", runId, attempt },
    environment: { node: `v${plan.runtime.node}`, platform: plan.runtime.platform, architecture: plan.runtime.architecture },
    commands: shard.commands.map(([name, command, args]) => ({ name, command, args, exit_code: 0, duration_seconds: 1 })),
    tests: shard.testIds.map(id => ({ id, status: "passed", retries: 0 })) }));
}

export function passingBrowserJobs(plan, reports) {
  return reports.map(report => ({ id: 1000 + report.shardId, run_id: report.execution.runId,
    run_attempt: report.execution.attempt, name: `browser / shard-${report.shardId}`,
    status: "completed", conclusion: "success", labels: [plan.runtime.runner],
    steps: [{ name: "Run isolated browser shard", status: "completed", conclusion: "success" }] }));
}

export function attachPassingBrowserShards(plan, report) {
  if (!plan.browserShards || plan.browserShards.count < 2) return;
  report.shards = passingBrowserShards(plan, report.execution.runId, report.execution.attempt);
  report.shardJobs = passingBrowserJobs(plan, report.shards);
  report.tests = aggregateBrowserShards(plan, report.shards, report.execution);
}
