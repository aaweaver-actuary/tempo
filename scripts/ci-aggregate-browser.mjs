import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { aggregateBrowserShards, browserShardReportPath, validateBrowserShardJobs } from "./ci-browser-shards.mjs";

const plan = JSON.parse(readFileSync("test-results/ci/plan.json", "utf8"));
const expectedArtifacts = plan.browserShards.shards.map(shard => `ci-browser-shard-${shard.id}`).sort();
if (JSON.stringify(readdirSync("test-results/browser-shards").sort()) !== JSON.stringify(expectedArtifacts)) throw new Error("Missing or extra browser shard artifacts");
const reports = plan.browserShards.shards.map(shard => JSON.parse(readFileSync(
  `test-results/browser-shards/ci-browser-shard-${shard.id}/${browserShardReportPath(shard.id).slice("test-results/".length)}`, "utf8")));
const execution = { runId: Number(process.env.GITHUB_RUN_ID), attempt: Number(process.env.GITHUB_RUN_ATTEMPT) };
const tests = aggregateBrowserShards(plan, reports, execution);
if (process.env.TEMPO_BROWSER_SHARD_RESULT !== "success") throw new Error("Browser shard matrix did not complete successfully");
if (plan.repository !== process.env.GITHUB_REPOSITORY) throw new Error("Browser aggregation repository differs from plan");
const result = spawnSync("gh", ["api", `repos/${plan.repository}/actions/runs/${execution.runId}/jobs?filter=all&per_page=100`],
  { encoding: "utf8", maxBuffer: 8 * 1024 * 1024 });
if (result.status !== 0) throw new Error("Browser shard job provenance unavailable");
const metadata = JSON.parse(result.stdout);
if (metadata.total_count > 100) throw new Error("Browser job history exceeds bounded validation");
validateBrowserShardJobs(plan, reports, metadata.jobs);
writeFileSync("test-results/ci/browser-aggregate.json", JSON.stringify({ tests, shards: reports,
  shardJobs: metadata.jobs.filter(job => /^browser \/ shard-[1-4]$/.test(job.name)),
  slowestRunnerSeconds: Math.max(...reports.map(report => report.commands.reduce((seconds, command) => seconds + command.duration_seconds, 0))),
}, null, 2));
