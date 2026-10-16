import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, cp, mkdir, readFile, writeFile, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { buildArtifact, stageExternalInputs, componentRoot } from '../build.mjs';
import { checkBoundary } from '../qualify.mjs';
const registration = { artifact:{name:'@tempo/stockfish-worker',version:'1.0.0'}, dependencies:['ajv','chess.js'], publicExports:['StockfishWorker','inspectEngine','stockfishInputDigest'] };
test('owned boundary has declared passive exports and no application imports',()=>{
  assert.equal(checkBoundary(registration).success,true);
  assert.throws(()=>checkBoundary({...registration,publicExports:['missing']}),/exports differ/);
});

const digest = bytes => createHash('sha256').update(bytes).digest('hex');
async function runnerRepository(t) {
  const repository = await mkdtemp(join(tmpdir(), 'stockfish-runner-test-'));
  t.after(() => rm(repository, { recursive: true, force: true }));
  const component = join(repository, 'workers/stockfish'); await mkdir(component, { recursive: true });
  // Copy real runner/build inputs; the probe replaces ordinary tests to avoid recursive test runs.
  for (const name of ['test.mjs', 'build.mjs', 'package.json', 'package-lock.json']) await cp(join(componentRoot, name), join(component, name));
  const manifest = await stageExternalInputs(component, { repositoryRoot: resolve(componentRoot, '../..') });
  await cp(join(componentRoot, 'build-inputs.json'), join(component, 'build-inputs.json'));
  for (const input of manifest.external_inputs) {
    const source = join(repository, input.source); await mkdir(dirname(source), { recursive: true });
    await cp(join(component, input.target), source);
  }
  await mkdir(join(component, 'tests'));
  await cp(join(componentRoot, 'tests/reporter.mjs'), join(component, 'tests/reporter.mjs'));
  await symlink(join(componentRoot, 'node_modules'), join(component, 'node_modules'), 'dir');
  await writeFile(join(component, 'tests/resources.test.mjs'), `
import test from 'node:test'; import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs'; import { createHash } from 'node:crypto';
test('runner input digest probe', () => {
 const root = new URL('../', import.meta.url);
 const manifest = JSON.parse(readFileSync(new URL('build-inputs.json', root)));
 for (const input of manifest.external_inputs) {
   const actual = createHash('sha256').update(readFileSync(new URL(input.target, root))).digest('hex');
   assert.equal(actual, input.sha256, 'stale staged input: ' + input.target);
 }
 console.log('input-snapshot:' + JSON.stringify(root.pathname));
});
`);
  return { repository, component, manifest };
}
function runProbe(component, group = 'pure', pattern) {
  return new Promise((resolveResult, reject) => {
    const environment = { ...process.env }; delete environment.NODE_TEST_CONTEXT;
    const child = spawn(process.execPath, [join(component, 'test.mjs'), group, ...(pattern ? [pattern] : [])], { cwd: component, env: environment });
    let output = '';
    child.stdout.on('data', chunk => { output += chunk; }); child.stderr.on('data', chunk => { output += chunk; });
    const timeout = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('Runner probe timed out')); }, 15000);
    child.on('error', reject); child.once('close', status => { clearTimeout(timeout); resolveResult({ status, output }); });
  });
}
function successfulProbe(result) { assert.equal(result.status, 0, result.output); assert.match(result.output, /input-snapshot:/); }

test('B_test_runner_refreshes_staged_contract_inputs', async t => {
  const { component } = await runnerRepository(t);
  successfulProbe(await runProbe(component));
  const schema = join(component, 'schemas/v1/StockfishJob.schema.json');
  await writeFile(schema, (await readFile(schema, 'utf8')) + '\n');
  successfulProbe(await runProbe(component));
});

test('B_test_runner_refreshes_missing_partial_and_corrupted_assets', async t => {
  const { component, manifest } = await runnerRepository(t);
  for (const input of manifest.external_inputs) {
    await rm(join(component, input.target)); successfulProbe(await runProbe(component));
  }
  await rm(join(component, 'schemas'), { recursive: true, force: true });
  await writeFile(join(component, 'assets/sf_19_smallnet.wasm'), 'stale-but-present');
  successfulProbe(await runProbe(component));
});

test('B_test_runner_rejects_authoritative_mismatch_and_accepts_valid_manifest_update', async t => {
  const { repository, component, manifest } = await runnerRepository(t);
  const input = manifest.external_inputs.find(entry => entry.target.endsWith('StockfishJob.schema.json'));
  const source = join(repository, input.source), bytes = Buffer.from((await readFile(source, 'utf8')) + '\n');
  await writeFile(source, bytes);
  const invalid = await runProbe(component);
  assert.notEqual(invalid.status, 0, 'authoritative mismatch was hidden by a previously staged schema');
  assert.match(invalid.output, /Changed immutable input/); assert.doesNotMatch(invalid.output, /input-snapshot:/);
  input.sha256 = digest(bytes); await writeFile(join(component, 'build-inputs.json'), JSON.stringify(manifest));
  successfulProbe(await runProbe(component));
  const asset = manifest.external_inputs[0]; await writeFile(join(repository, asset.source), 'changed authoritative asset');
  const badAsset = await runProbe(component); assert.notEqual(badAsset.status, 0); assert.match(badAsset.output, /Changed immutable input/);
});

test('B_test_runner_parallel_invocations_have_private_inputs_and_truthful_selection', async t => {
  const { component } = await runnerRepository(t);
  const results = await Promise.all([runProbe(component), runProbe(component)]);
  results.forEach(successfulProbe);
  const snapshots = results.map(result => JSON.parse(result.output.match(/input-snapshot:("[^\n]+")/)[1]));
  assert.notEqual(snapshots[0], snapshots[1], 'parallel invocations shared mutable staged inputs');
  const empty = await runProbe(component, 'pure', 'does-not-exist'); assert.notEqual(empty.status, 0);
  const unknown = await runProbe(component, 'unknown'); assert.notEqual(unknown.status, 0); assert.match(unknown.output, /Unknown suite/);
});

test('B_test_runner_missing_authoritative_input_cannot_reuse_staged_success', async t => {
  const { repository, component, manifest } = await runnerRepository(t);
  for (const input of [manifest.external_inputs[0], manifest.external_inputs[3]]) {
    const source = join(repository, input.source), bytes = await readFile(source);
    await rm(source);
    const result = await runProbe(component); assert.notEqual(result.status, 0);
    assert.match(result.output, /ENOENT/); assert.doesNotMatch(result.output, /input-snapshot:/);
    await writeFile(source, bytes);
  }
});

test('B_test_runner_failed_skipped_and_todo_cases_cannot_pass', async t => {
  const { component } = await runnerRepository(t);
  for (const source of ["test.skip('skipped probe', () => {});", "test.todo('todo probe');", "test('failed probe', () => { throw Error('intentional probe failure'); });"]) {
    await writeFile(join(component, 'tests/resources.test.mjs'), "import test from 'node:test';\n" + source + '\n');
    const result = await runProbe(component); assert.notEqual(result.status, 0, result.output);
    const report = JSON.parse(result.output.split('\n').findLast(line => line.startsWith('{"success"')));
    assert.equal(report.success, false); assert.equal(report.passed, 0);
  }
});
test('artifact contains only declared runtime, exact engine assets, schemas and licenses',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'stockfish-artifact-test-'));
  try { const result=await buildArtifact({outputDirectory:directory});
    assert.match(result.sha256,/^[a-f0-9]{64}$/);
    assert.ok(result.files.includes('assets/sf_19_smallnet.wasm'));
    assert.ok(result.files.includes('licenses/THIRD-PARTY.md'));
    assert.equal(result.files.some(name=>/^(?:app|backend|services|tests|node_modules)\//.test(name)),false);
  } finally { await rm(directory,{recursive:true,force:true}); }
});
