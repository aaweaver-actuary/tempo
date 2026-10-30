import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { unitWorkerAllocation } from "../../../scripts/unit-worker-allocation.mjs";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const allocationUrl = new URL("../../../scripts/unit-worker-allocation.mjs", import.meta.url).href;
const budgetUrl = new URL("../../../scripts/development-budget.mjs", import.meta.url).href;
function fixture(t) {
  const directory = mkdtempSync(join(tmpdir(), "tempo-unit-allocation-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  return directory;
}
function invoke(args, env = {}) {
  const environment = { ...process.env, ...env };
  delete environment.NODE_TEST_CONTEXT;
  delete environment.TEMPO_DEVELOPMENT_BUDGET_TOKEN;
  delete environment.TEMPO_DEVELOPMENT_BUDGET_DIRECTORY;
  if (!Object.hasOwn(env, "TEMPO_QUALIFICATION_CPUS")) delete environment.TEMPO_QUALIFICATION_CPUS;
  return spawnSync(process.execPath, args, { cwd: root, env: environment, encoding: "utf8", timeout: 20_000 });
}

test("unit workers use available parallelism for unrestricted unmanaged runs", async () => {
  assert.deepEqual(await unitWorkerAllocation({}, 8), { maxWorkers: 8, env: { TEMPO_QUALIFICATION_CPUS: "1" } });
});

test("managed token without an explicit CPU cap retains unrestricted unit workers", async () => {
  assert.deepEqual(await unitWorkerAllocation({ TEMPO_DEVELOPMENT_BUDGET_TOKEN: "owner" }, 8),
    { maxWorkers: 8, env: { TEMPO_QUALIFICATION_CPUS: "1" } });
});

test("unit workers bound explicit and fractional grants and partition nested pools", async () => {
  for (const [cpus, maxWorkers, childCpus] of [["8", 8, "1"], ["3.5", 3, "1"], ["0.25", 1, "0.25"], ["99", 8, "1"]]) {
    const allocation = await unitWorkerAllocation({ TEMPO_QUALIFICATION_CPUS: cpus, TEMPO_DEVELOPMENT_BUDGET_TOKEN: "owner" }, 8);
    assert.deepEqual(allocation, { maxWorkers, env: { TEMPO_QUALIFICATION_CPUS: childCpus } });
    assert.deepEqual(await unitWorkerAllocation(allocation.env, 8), { maxWorkers: 1, env: allocation.env });
  }
});

test("unit workers reject malformed grants and invalid available parallelism", async () => {
  for (const cpus of ["", " ", "NaN", "Infinity", "0", "-1", "0.000001", 2, null])
    await assert.rejects(unitWorkerAllocation({ TEMPO_QUALIFICATION_CPUS: cpus }, 8), /positive|CPU/);
  for (const parallelism of [0, -1, 1.5, NaN, Infinity])
    await assert.rejects(unitWorkerAllocation({}, parallelism), /positive integer/);
});

test("actual managed launcher forwards unrestricted or explicit CPU policy through nested runners", t => {
  const directory = fixture(t), receipt = join(directory, "receipt.json"), lock = join(directory, "lock");
  const child = join(directory, "child.mjs"), launcher = join(directory, "launcher.mjs");
  writeFileSync(child, `import {writeFileSync} from 'node:fs';
import {availableParallelism} from 'node:os';
import {unitWorkerAllocation} from ${JSON.stringify(allocationUrl)};
const allocation=await unitWorkerAllocation();
writeFileSync(${JSON.stringify(receipt)},JSON.stringify({owner:process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN,
  receivedCpus:process.env.TEMPO_QUALIFICATION_CPUS??null, parallelism:availableParallelism(), allocation,
  nested:await unitWorkerAllocation({...process.env,...allocation.env})}));`);
  writeFileSync(launcher, `import {acquireDevelopmentBudget,runWithDevelopmentBudget} from ${JSON.stringify(budgetUrl)};
process.exitCode=await runWithDevelopmentBudget(process.execPath,[${JSON.stringify(child)}],{
  acquire:()=>acquireDevelopmentBudget({directory:${JSON.stringify(lock)}}),quiet:async()=>{}});`);
  for (const cpus of [undefined, "3.5", "0.25"]) {
    const result = invoke([launcher], cpus === undefined ? {} : { TEMPO_QUALIFICATION_CPUS: cpus });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const report = JSON.parse(readFileSync(receipt, "utf8"));
    assert.ok(report.owner);
    assert.equal(report.receivedCpus, cpus ?? null);
    assert.equal(report.allocation.maxWorkers, Math.max(1, Math.floor(Math.min(cpus === undefined ? report.parallelism : Number(cpus), report.parallelism))));
    assert.equal(report.nested.maxWorkers, 1);
    assert.throws(() => readFileSync(join(lock, "owner.json")), { code: "ENOENT" });
  }
});

test("Node runner awaits allocation and passes only its CPU share to real test children", t => {
  const directory = fixture(t), file = join(directory, "owned.test.mjs");
  writeFileSync(file, `import test from 'node:test';import assert from 'node:assert/strict';
import {unitWorkerAllocation} from ${JSON.stringify(allocationUrl)};
test('nested worker receives its parent share',async()=>{
assert.equal(process.env.TEMPO_QUALIFICATION_CPUS,'0.25');
assert.equal((await unitWorkerAllocation()).maxWorkers,1);});`);
  const runner = join(root, "tools/component-build/test-node.mjs");
  const passed = invoke([runner, directory], { TEMPO_QUALIFICATION_CPUS: "0.25" });
  assert.equal(passed.status, 0, passed.stdout + passed.stderr);
  const report = JSON.parse(passed.stdout.trim().split("\n").at(-1));
  assert.equal(report.passed, 1);
  assert.equal(report.failed, 0);
  const rejected = invoke([runner, directory], { TEMPO_QUALIFICATION_CPUS: "invalid" });
  assert.notEqual(rejected.status, 0);
  assert.match(rejected.stderr, /positive finite/);
});

test("async root and lane Vitest configurations preserve allocation and timer settings", () => {
  for (const cpus of [undefined, "3.5", "0.25"]) {
    const source = `import {loadConfigFromFile} from 'vite';
import {availableParallelism} from 'node:os';
process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN='configuration-fixture';
process.env.TEMPO_TEST_LANE='frontend';
const environment={command:'serve',mode:'test'};
const root=await loadConfigFromFile(environment,'vitest.config.ts');
const lane=await loadConfigFromFile(environment,'vitest.lanes.config.ts');
console.log(JSON.stringify({parallelism:availableParallelism(),root:root.config.test,lane:lane.config.test}));`;
    const result = invoke(["--input-type=module", "-e", source], cpus === undefined ? {} : { TEMPO_QUALIFICATION_CPUS: cpus });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const report = JSON.parse(result.stdout.trim().split("\n").at(-1));
    const capacity = Math.min(cpus === undefined ? report.parallelism : Number(cpus), report.parallelism);
    for (const config of [report.root, report.lane]) {
      assert.equal(config.maxWorkers, Math.max(1, Math.floor(capacity)));
      assert.deepEqual(config.env, { TEMPO_QUALIFICATION_CPUS: String(Math.min(capacity, 1)) });
      assert.equal(config.environment, "jsdom");
      assert.ok(config.include.length);
    }
    assert.deepEqual(report.lane.fakeTimers, report.root.fakeTimers);
  }
});
