import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { collectCases, verificationPlan, inventory } from "../../scripts/ci-verification-plan.mjs";

function collectedCases(grep) {
  const result = spawnSync("npx", ["--no-install", "playwright", "test", "--list", "--reporter=json", ...(grep ? ["--grep", grep] : [])], {
    encoding: "utf8", env: { ...process.env, TEMPO_DOCKER_URL: "http://127.0.0.1:1" }, maxBuffer: 20 * 1024 * 1024,
  });
  assert.equal(result.status, 0, result.stderr);
  return collectCases(JSON.parse(result.stdout));
}
let completeCases;
function completePlan() {
  return verificationPlan({ commit: "revision", paths: [], complete: true,
    files: Object.values(inventory.families).flat(), cases: completeCases ??= collectedCases() });
}

test("four complete browser shards partition required identities exactly once", () => {
  const plan = completePlan();
  assert.equal(plan.browserShards?.count, 4);
  const actual = plan.browserShards.shards.flatMap(shard => shard.testIds).sort();
  assert.deepEqual(actual, plan.collection.filter(item => item.selected).map(item => item.id).sort());
  assert.equal(actual.length, new Set(actual).size);
});

import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runInNewContext } from "node:vm";
import { randomBytes } from "node:crypto";
import { createBrowserShardPlan, browserTimingProfile, aggregateBrowserShards, browserShardReportPath,
  validateBrowserShardPlan, validateBrowserShardJobs } from "../../scripts/ci-browser-shards.mjs";
import { layerCommands, executeLayer } from "../../scripts/ci-run-layer.mjs";
import { layerFailures } from "../../scripts/ci-quality.mjs";
import { suiteFingerprint, validateReuseEvidence } from "../../scripts/ci-evidence.mjs";
import { planHash } from "../../scripts/ci-verification-plan.mjs";
import { summarizeBrowserSpans } from "../../scripts/browser-timing-reporter.mjs";
import { passingBrowserShards, passingBrowserJobs, attachPassingBrowserShards } from "./ci-browser-shard-fixture.mjs";

function aggregateFixture(plan = completePlan()) {
  const report = { version: 2, layer: "browser", commit: plan.commit, planHash: plan.hash, completed: true, status: "success",
    executionKey: suiteFingerprint(plan, "browser", layerCommands("browser", plan)),
    execution: { kind: "executed", runId: 100, attempt: 1 },
    environment: { node: `v${plan.runtime.node}`, platform: plan.runtime.platform, architecture: plan.runtime.architecture },
    commands: layerCommands("browser", plan).map(([name, command, args]) => ({ name, command, args, exit_code: 0 })) };
  attachPassingBrowserShards(plan, report);
  return report;
}

test("browser shard assignment is deterministic across collection order and tied runtimes", () => {
  const collection = completePlan().collection;
  assert.deepEqual(createBrowserShardPlan([...collection].reverse()), createBrowserShardPlan(collection));
  const equal = { version: 1, fallback_duration_ms: 1445, test_durations_ms: {} };
  assert.deepEqual(createBrowserShardPlan([...collection].reverse(), equal), createBrowserShardPlan(collection, equal));
});

test("uneven browser timings and new identities remain exactly once within whole specs", () => {
  const collection = [{ id: "slow", file: "slow.spec.ts", selected: true, fullTitle: "slow" },
    ...Array.from({ length: 39 }, (_, index) => ({ id: `new-${index}`, file: `spec-${index % 8}.ts`, selected: true, fullTitle: `new-${index}` }))];
  const profile = { version: 1, fallback_duration_ms: 1445, test_durations_ms: { slow: 500000 } };
  const assignment = createBrowserShardPlan(collection, profile);
  assert.deepEqual(assignment.shards.flatMap(shard => shard.testIds).sort(), collection.map(item => item.id).sort());
  assert.equal(assignment.shards.reduce((total, shard) => total + shard.estimatedDurationMs, 0), 500000 + 39 * 1445);
  for (const file of new Set(collection.map(item => item.file))) assert.equal(assignment.shards.filter(shard => shard.files.includes(file)).length, 1);
  assert.throws(() => createBrowserShardPlan([...collection, collection[0]], profile), /duplicated/);
});

test("baseline timing profile retains measured identities and bounds balanced shard load", () => {
  const plan = completePlan();
  assert.equal(browserTimingProfile.source.passed, 267);
  assert.equal(browserTimingProfile.fallback_duration_ms, 1445);
  assert.equal(Object.keys(browserTimingProfile.test_durations_ms).length, 267);
  assert.equal(Object.values(browserTimingProfile.test_durations_ms).reduce((sum, duration) => sum + duration, 0), 855810);
  const loads = plan.browserShards.shards.map(shard => shard.estimatedDurationMs);
  const specLoads = new Map();
  for (const item of plan.collection.filter(item => item.selected)) specLoads.set(item.file, (specLoads.get(item.file) ?? 0) + (browserTimingProfile.test_durations_ms[item.id] ?? 1445));
  assert(Math.max(...loads) <= loads.reduce((sum, duration) => sum + duration, 0) / 4 + Math.max(...specLoads.values()));
});

test("actual Playwright shard selectors collect the exact immutable partition", () => {
  const plan = completePlan();
  for (const shard of plan.browserShards.shards) {
    assert.deepEqual(collectedCases(shard.grep).map(item => item.id).sort(), shard.testIds);
    assert.deepEqual(layerCommands("browser", plan, shard.id), shard.commands);
  }
});

test("failed missing cancelled skipped and incomplete shards fail browser qualification", () => {
  const plan = completePlan();
  assert.deepEqual(layerFailures(plan, "browser", "success", aggregateFixture(plan)), []);
  for (const modify of [report => { report.shards.pop(); }, report => { report.shards[0].status = "failed"; },
    report => { report.shards[0].completed = false; }, report => { report.shards[0].commands[0].exit_code = 1; },
    report => { report.shardJobs[0].conclusion = "cancelled"; }, report => { report.shardJobs[0].conclusion = "skipped"; },
    report => { report.shardJobs[0].conclusion = "failure"; }, report => { report.shardJobs.pop(); },
    report => { report.shardJobs[0].status = "in_progress"; }]) {
    const report = aggregateFixture(plan); modify(report);
    assert(layerFailures(plan, "browser", "success", report).length > 0);
  }
});

test("duplicate extra missing and retried shard identities cannot qualify", () => {
  const plan = completePlan();
  for (const modify of [shard => { shard.tests.push(shard.tests[0]); }, shard => { shard.tests.push({ id: "extra", status: "passed", retries: 0 }); },
    shard => { shard.tests.pop(); }, shard => { shard.tests[0].retries = 1; }, shard => { shard.tests[0].status = "skipped"; }]) {
    const report = aggregateFixture(plan); modify(report.shards[0]);
    assert(layerFailures(plan, "browser", "success", report).length > 0);
  }
  const report = aggregateFixture(plan); report.shards[1] = structuredClone(report.shards[0]);
  assert.throws(() => aggregateBrowserShards(plan, report.shards, report.execution), /absent or duplicated/);
});

test("wrong candidate plan assignment runtime commands and workflow shard reports are rejected", () => {
  const plan = completePlan();
  for (const modify of [shard => { shard.commit = "stale"; }, shard => { shard.planHash = "stale"; },
    shard => { shard.assignmentHash = "stale"; }, shard => { shard.executionKey = "stale"; },
    shard => { shard.execution.runId = 99; }, shard => { shard.execution.attempt = 2; },
    shard => { shard.execution.kind = "reused"; }, shard => { shard.commands[1].args = ["other"]; }]) {
    const report = aggregateFixture(plan); modify(report.shards[0]);
    assert(layerFailures(plan, "browser", "success", report).length > 0);
  }
  const remote = completePlan(); remote.event = "pull_request"; remote.hash = planHash(remote);
  const report = aggregateFixture(remote); report.shards[0].environment.architecture = "x64";
  assert(layerFailures(remote, "browser", "success", report).length > 0);
});

test("latest shard attempts reject stale success while retaining untouched successful siblings", () => {
  const plan = completePlan(), reports = passingBrowserShards(plan), jobs = passingBrowserJobs(plan, reports);
  for (const conclusion of ["failure", "cancelled", "skipped"]) {
    assert.throws(() => validateBrowserShardJobs(plan, reports, [...jobs, { ...jobs[0], id: 2000, run_attempt: 2, conclusion }]), /latest/);
  }
  const updated = structuredClone(reports); updated[0].execution.attempt = 2;
  assert.doesNotThrow(() => validateBrowserShardJobs(plan, updated, [...jobs, { ...jobs[0], id: 2000, run_attempt: 2 }]));
  assert.doesNotThrow(() => aggregateBrowserShards(plan, updated, { runId: 100, attempt: 2 }));
  assert.throws(() => validateBrowserShardJobs(plan, reports, [...jobs, { ...jobs[0], id: 2000, run_attempt: 2 }]), /latest/);
});

test("browser aggregate contains exactly planned identities and cannot substitute its own results", () => {
  const plan = completePlan(), report = aggregateFixture(plan);
  assert.deepEqual(report.tests.map(item => item.id), plan.collection.filter(item => item.selected).map(item => item.id).sort());
  for (const change of [value => { value.tests.pop(); }, value => { value.tests.push(value.tests[0]); }, value => { value.tests[0].id = "other"; }]) {
    const invalid = structuredClone(report); change(invalid);
    assert(layerFailures(plan, "browser", "success", invalid).length > 0);
  }
});

test("modified shard assignment invalidates immutable planning and whole-browser reuse", () => {
  const plan = completePlan();
  for (const modify of [assignment => { assignment.algorithm = "different"; }, assignment => { assignment.profileHash = "different"; },
    assignment => { assignment.shards[0].testIds.pop(); }, assignment => { assignment.shards.reverse(); }]) {
    const changed = structuredClone(plan); modify(changed.browserShards); changed.hash = planHash(changed);
    assert.notEqual(suiteFingerprint(plan, "browser", layerCommands("browser", plan)), suiteFingerprint(changed, "browser", layerCommands("browser", plan)));
    assert.throws(() => validateBrowserShardPlan(changed), /assignment/);
  }
});

test("whole-browser evidence reuse verifies every original latest shard and rejects incompatible candidates", () => {
  const plan = completePlan(); Object.assign(plan, { event: "pull_request", repository: "owner/tempo", repositoryId: 7, pullRequest: 1, head: "head", base: "base" }); plan.hash = planHash(plan);
  const report = aggregateFixture(plan), commands = layerCommands("browser", plan);
  const receipt = { version: 2, layer: "browser", commit: plan.commit, planHash: plan.hash, completed: true, status: "success",
    executionKey: report.executionKey, execution: { kind: "reused" },
    source: { plan, report, runId: 100, jobId: 200, artifactId: 300, digest: `sha256:${"a".repeat(64)}` } };
  const metadata = { latestJobId: 200, shardJobs: report.shardJobs,
    run: { id: 100, status: "completed", conclusion: "success", event: "pull_request", path: ".github/workflows/pages.yml", repository: { id: 7, full_name: plan.repository }, head_sha: "head", pull_requests: [{ number: 1 }] },
    job: { id: 200, run_id: 100, run_attempt: 1, name: "browser / verify", status: "completed", conclusion: "success", labels: [plan.runtime.runner], steps: [{ name: "Run isolated verification layer", status: "completed", conclusion: "success" }] },
    artifact: { id: 300, name: "ci-result-browser", expired: false, digest: receipt.source.digest, workflow_run: { id: 100, repository_id: 7, head_sha: "head" } } };
  assert(validateReuseEvidence(plan, "browser", receipt, commands, metadata, layerFailures));
  assert.throws(() => validateReuseEvidence(plan, "browser", receipt, commands, { ...metadata, shardJobs: metadata.shardJobs.slice(1) }, layerFailures), /latest/);
  const laterFailure = { ...metadata.shardJobs[0], id: 4000, run_attempt: 2, conclusion: "failure" };
  assert.throws(() => validateReuseEvidence(plan, "browser", receipt, commands, { ...metadata, shardJobs: [...metadata.shardJobs, laterFailure] }, layerFailures), /latest/);
  for (const field of ["commit", "head", "base"]) {
    const other = { ...plan, [field]: "other" }; other.hash = planHash(other);
    assert.throws(() => validateReuseEvidence(other, "browser", { ...receipt, commit: other.commit, planHash: other.hash }, commands, metadata, layerFailures));
  }
});

test("targeted development and partial qualification retain single browser execution", () => {
  const collection = completePlan().collection;
  for (const options of [{ tier: "development", draft: true, paths: ["tests/browser/content-deletion.spec.ts"] },
    { paths: ["docs/testing.md"] }]) {
    const plan = verificationPlan({ commit: "revision", files: Object.values(inventory.families).flat(), cases: collection, ...options });
    assert.equal(plan.browserShards, undefined);
    assert.equal(layerCommands("browser", plan).at(-1)[0], "browser");
    assert(layerCommands("browser", plan).at(-1)[2].includes("--browser-grep"));
  }
});

test("shard diagnostic success preserves the original failed qualification", () => {
  const commands = layerCommands("browser", completePlan(), 1);
  let count = 0;
  const report = executeLayer(commands, () => ({ status: ++count === 2 ? 1 : 0 }), true);
  assert.equal(report.status, "failed"); assert.equal(report.commands[1].exit_code, 1); assert.equal(report.diagnostics[0].exit_code, 0);
});

test("separate shard resources artifacts and cleanup retain isolated runner ownership", () => {
  const source = readFileSync("scripts/test-postgres-docker.mjs", "utf8");
  const declaration = source.match(/const project = [^;]+;/)[0];
  const first = runInNewContext(`${declaration} project`, { process: { pid: 100 }, randomBytes });
  const second = runInNewContext(`${declaration} project`, { process: { pid: 100 }, randomBytes });
  assert.notEqual(first, second);
  assert.match(source, /server.listen\(0, "127.0.0.1"/);
  assert.match(source, /const compose = \["compose", "-p", project/);
  assert.match(source, /randomBytes\(24\)/);
  assert.match(source, /mkdtempSync\(join\(process.cwd\(\), ".tempo-pg-test-secrets-"\)\)/);
  assert.match(source, /\[\.\.\.compose, "down", "--rmi", "local", "-v"\]/);
  const compose = readFileSync("docker-compose.postgres.test.yml", "utf8");
  assert(!/^\s*(container_name|name):/m.test(compose), "Compose names remain project scoped");
  const paths = [1, 2, 3, 4].map(browserShardReportPath); assert.equal(new Set(paths).size, 4);
  const workflow = readFileSync(".github/workflows/verify-browser.yml", "utf8");
  assert.match(workflow, /fail-fast: false/); assert.match(workflow, /runs-on: ubuntu-24.04-arm/);
  assert.match(workflow, /name: ci-browser-shard-\$\{\{ matrix.shard \}\}/);
  assert.match(workflow, /needs: \[reuse, shard\]\n    if: always\(\)/);
  assert.match(workflow, /name: ci-result-browser/);
  assert(!/docker\s+.*prune/.test(source + workflow));
});

test("fixture and polling timing unions avoid counting nested spans twice", () => {
  const summary = summarizeBrowserSpans([{ kind: "fixture_hook", start: 0, end: 100 }, { kind: "fixture_hook", start: 10, end: 70 },
    { kind: "poll_wait", start: 20, end: 60 }, { kind: "poll_wait", start: 40, end: 80 }, { kind: "poll_wait", start: 110, end: 150 }]);
  assert.deepEqual(summary, { fixtureHookMs: 100, pollWaitMs: 100, overlapMs: 60, observedMs: 140 });
});


test("browser aggregation CLI rejects extra artifacts and unsuccessful matrix results", () => {
  const directory = mkdtempSync(join(tmpdir(), "tempo-ci-browser-aggregation-"));
  const plan = completePlan(); plan.repository = "owner/tempo"; plan.hash = planHash(plan);
  const reports = passingBrowserShards(plan), jobs = passingBrowserJobs(plan, reports);
  const script = new URL("../../scripts/ci-aggregate-browser.mjs", import.meta.url).pathname;
  try {
    mkdirSync(`${directory}/test-results/ci`, { recursive: true });
    writeFileSync(`${directory}/test-results/ci/plan.json`, JSON.stringify(plan));
    for (const report of reports) {
      const path = `${directory}/test-results/browser-shards/ci-browser-shard-${report.shardId}/ci/browser-shards/${report.shardId}`;
      mkdirSync(path, { recursive: true }); writeFileSync(`${path}/report.json`, JSON.stringify(report));
    }
    writeFileSync(`${directory}/jobs.json`, JSON.stringify({ total_count: jobs.length, jobs }));
    writeFileSync(`${directory}/gh`, `#!/bin/sh\ncat '${directory}/jobs.json'\n`); chmodSync(`${directory}/gh`, 0o755);
    const env = { ...process.env, PATH: `${directory}:${process.env.PATH}`, GITHUB_RUN_ID: "100", GITHUB_RUN_ATTEMPT: "1", GITHUB_REPOSITORY: "owner/tempo", TEMPO_BROWSER_SHARD_RESULT: "success" };
    const run = extra => spawnSync(process.execPath, [script], { cwd: directory, encoding: "utf8", env: { ...env, ...extra } });
    const success = run(); assert.equal(success.status, 0, success.stderr);
    const aggregate = JSON.parse(readFileSync(`${directory}/test-results/ci/browser-aggregate.json`));
    assert.deepEqual(aggregate.tests.map(test => test.id), plan.collection.filter(test => test.selected).map(test => test.id).sort());
    for (const result of ["failure", "cancelled", "skipped", ""]) {
      const failed = run({ TEMPO_BROWSER_SHARD_RESULT: result }); assert.notEqual(failed.status, 0); assert.match(failed.stderr, /matrix/);
    }
    mkdirSync(`${directory}/test-results/browser-shards/ci-browser-shard-5`);
    const extra = run(); assert.notEqual(extra.status, 0); assert.match(extra.stderr, /extra browser shard artifacts/);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test("Docker capability probe requests the server version and rejects unavailable or empty daemons", () => {
  const directory = mkdtempSync(join(tmpdir(), "tempo-ci-browser-daemon-"));
  const script = new URL("../../scripts/check-test-capabilities.mjs", import.meta.url).pathname;
  const run = () => spawnSync(process.execPath, [script, "--docker"], { encoding: "utf8", env: { ...process.env, PATH: `${directory}:${process.env.PATH}` } });
  const fakeDocker = body => { writeFileSync(`${directory}/docker`, `#!/bin/sh\n${body}\n`); chmodSync(`${directory}/docker`, 0o755); };
  try {
    fakeDocker('test "$1" = version && test "$2" = --format && test "$3" = "{{.Server.Version}}" || exit 69\nprintf "29.0.0"');
    const available = run(); assert.equal(available.status, 0, available.stderr);
    fakeDocker('printf "daemon unavailable" >&2\nexit 1');
    const unavailable = run(); assert.notEqual(unavailable.status, 0); assert.match(unavailable.stderr, /daemon unavailable/);
    fakeDocker('exit 0');
    const empty = run(); assert.notEqual(empty.status, 0); assert.match(empty.stderr, /server version/);
    assert.match(readFileSync(script, "utf8"), /timeout: 5_000/);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
