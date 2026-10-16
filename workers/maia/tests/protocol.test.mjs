import test from 'node:test';
import assert from 'node:assert/strict';
import { fork, spawnSync } from 'node:child_process';
import { PassThrough, Writable } from 'node:stream';
import { mkdtemp, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { MaiaWorker } from '../worker.mjs';
import { serveProtocol, MAX_LINE_BYTES } from '../cli.mjs';
import { validatePayload, validateRecord, fingerprintJob } from '../wire.mjs';

const resources = { memory_enforced: true, memory_limit_bytes: 1024 * 1024 * 1024, memory_limit_mb: 1024, source: 'test-cgroup', minimum_memory_mb: 1024 };
function job(changes = {}) {
  return { contract_version: 1, workspace_id: 'workspace-a', job_id: 'job-a', request_revision: 1, execution_generation: 1, input_digest: 'sha256:test', constraints: { max_wall_time_ms: 2000, max_memory_mb: 1024 }, worker: 'maia', fen: 'fast', rating: 1500, model_id: 'test-maia', model_version: 'fixture-v1', legal_moves: ['e2e4', 'd2d4'], ...changes };
}
function cancellation(input, changes = {}) { return { contract_version: 1, workspace_id: input.workspace_id, job_id: input.job_id, request_revision: input.request_revision, execution_generation: input.execution_generation, cancellation_id: 'cancel-a', ...changes }; }
function send(worker, payload) { worker.receive({ model: 'MaiaJob', payload }); }
function cancel(worker, payload) { worker.receive({ model: 'CancellationRequest', payload }); }
function waitFor(worker, collection, predicate, event = 'record', timeout = 5000) {
  const existing = collection.find(predicate);
  if (existing) return Promise.resolve(existing);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { worker.off(event, listener); reject(new Error(`Expected ${event} did not arrive; history: ${JSON.stringify(collection)}`)); }, timeout);
    const listener = (item) => { if (predicate(item)) { clearTimeout(timer); worker.off(event, listener); resolve(item); } };
    worker.on(event, listener);
  });
}
async function fixture(t, options = {}) {
  const worker = new MaiaWorker({ childFactory: () => fork(new URL('./fixtures/fake-child.mjs', import.meta.url), [], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'] }), resourceInspector: async () => resources, ...options });
  const records = [], diagnostics = [];
  worker.on('record', (record) => records.push(record)); worker.on('diagnostic', (event) => diagnostics.push(event));
  t.after(() => worker.close({ drain: false }));
  await worker.start();
  return { worker, records, diagnostics, record: (predicate) => waitFor(worker, records, predicate), event: (predicate) => waitFor(worker, diagnostics, predicate, 'diagnostic') };
}
const isStatus = (status, generation = 1) => (record) => record.model === 'JobStatus' && record.payload.status === status && record.payload.execution_generation === generation;
const isResult = (generation = 1) => (record) => record.model === 'MaiaResult' && record.payload.execution_generation === generation;
const isAck = (status, generation = 1) => (record) => record.model === 'CancellationAcknowledgement' && record.payload.status === status && record.payload.execution_generation === generation;

test('strict v1 schemas and independent semantic invariants reject malformed envelopes', () => {
  for (const changes of [{ contract_version: true }, { contract_version: '1' }, { internal: true }, { execution_generation: 2 ** 53 }, { rating: '1500' }]) assert.throws(() => validatePayload('MaiaJob', job(changes)));
  assert.throws(() => validatePayload('MaiaJob', job({ legal_moves: ['e2e4', 'e2e4'] })), { code: 'duplicate_legal_move' });
  assert.throws(() => validatePayload('MaiaJob', job({ legal_moves: ['e2e4\n'] })));
  assert.throws(() => validateRecord({ model: 'MaiaJob', payload: job(), case: 'unexpected' }));
  assert.throws(() => validatePayload('JobStatus', { contract_version: 1, workspace_id: 'w', job_id: 'j', request_revision: 1, execution_generation: 1, status: 'completed', retryable: true, reason: null }));
  assert.equal(fingerprintJob(job()), fingerprintJob(job({ execution_generation: 2 })));
  assert.notEqual(fingerprintJob(job()), fingerprintJob(job({ legal_moves: ['d2d4', 'e2e4'] })));
});

test('CLI executes through a symlinked artifact directory and imports without starting', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'tempo-maia-cli-symlink-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const linkedArtifact = join(directory, 'artifact');
  await symlink(fileURLToPath(new URL('../', import.meta.url)), linkedArtifact, 'dir');
  const executed = spawnSync(process.execPath, [join(linkedArtifact, 'cli.mjs'), 'identity'], { encoding: 'utf8' });
  assert.equal(executed.status, 0, executed.stderr);
  assert.equal(JSON.parse(executed.stdout).model_id, 'maia3-onnx');
  const imported = spawnSync(process.execPath, ['--input-type=module', '-e', `await import(${JSON.stringify(new URL('../cli.mjs', import.meta.url).href)}); process.stdout.write("imported");`], { encoding: 'utf8' });
  assert.equal(imported.status, 0, imported.stderr);
  assert.equal(imported.stdout, 'imported');
});

test('independent timestamp validation preserves exact microseconds and rejects malformed complete forms', () => {
  const result = { contract_version: 1, workspace_id: 'w', job_id: 'j', request_revision: 1, execution_generation: 1, worker: 'maia', model_id: 'm', model_version: 'v', predictions: [{ move: 'e2e4', probability: 1 }], provenance: { input_digest: 'd', runtime_name: 'r', runtime_version: 'v', implementation_version: 'v', started_at: '2026-10-14T12:00:00.000001Z', finished_at: '2026-10-14T12:00:00.000001Z' } };
  for (const timestamp of ['2026-10-14T12:00:00Z\n', '2025-02-29T12:00:00Z', '2026-10-14T12:00:00+00:60', '2026-10-14T24:00:00Z', '2026-10-14T12:00:60Z']) assert.throws(() => validatePayload('MaiaResult', { ...result, provenance: { ...result.provenance, finished_at: timestamp } }));
  assert.throws(() => validatePayload('MaiaResult', { ...result, provenance: { ...result.provenance, finished_at: '2026-10-14T12:00:00.000000Z' } }));
  const equivalent = { ...result, provenance: { ...result.provenance, finished_at: '2026-10-14T14:00:00.000001+02:00' } };
  assert.equal(validatePayload('MaiaResult', equivalent), equivalent);
});

test('response loss exact replay preserves the original result and produces one accepted effect', async (t) => {
  const f = await fixture(t); const input = job(); send(f.worker, input);
  const original = await f.record(isResult());
  send(f.worker, input);
  const results = f.records.filter(isResult());
  assert.equal(results.length, 2); assert.deepEqual(results[0], results[1]);
  const accepted = new Map();
  for (const record of results) {
    const result = record.payload;
    assert.equal(result.provenance.input_digest, input.input_digest);
    assert.equal(result.execution_generation, input.execution_generation);
    const key = JSON.stringify([result.workspace_id, result.job_id, result.request_revision, result.execution_generation]);
    if (!accepted.has(key)) accepted.set(key, record);
    else assert.deepEqual(accepted.get(key), record);
  }
  assert.equal(accepted.size, 1); assert.equal(original.payload.predictions.length, 2);
  assert.equal(f.diagnostics.filter((event) => event.event === 'execution_started').length, 1);
});

test('same active generation reports status without scheduling duplicate computation', async (t) => {
  const f = await fixture(t); const input = job({ fen: 'delay' }); send(f.worker, input);
  await f.event((event) => event.event === 'execution_started'); send(f.worker, input);
  await f.record(isResult());
  assert.equal(f.diagnostics.filter((event) => event.event === 'execution_started').length, 1);
});

test('logical identity binds digest, constraints and ordered legal move semantics', async (t) => {
  const f = await fixture(t); send(f.worker, job()); await f.record(isResult());
  for (const changes of [{ input_digest: 'different' }, { legal_moves: ['d2d4', 'e2e4'] }, { constraints: { max_wall_time_ms: 2001, max_memory_mb: 1024 } }]) send(f.worker, job(changes));
  assert.equal(f.records.filter((record) => record.payload.reason === 'identity_payload_conflict').length, 3);
});

test('new generation supersedes the active predecessor and runs after reinitialization', async (t) => {
  const f = await fixture(t); const input = job({ fen: 'fenced' }); send(f.worker, input);
  await f.event((event) => event.event === 'execution_started'); send(f.worker, { ...input, execution_generation: 2 });
  await f.record(isStatus('failed', 1)); const result = await f.record(isResult(2));
  assert.equal(result.payload.execution_generation, 2);
  assert.equal(f.records.some(isResult(1)), false);
  send(f.worker, input); assert.equal(f.records.at(-1).payload.reason, 'stale_generation');
  cancel(f.worker, cancellation(input)); assert.equal(f.records.at(-1).payload.status, 'stale_generation');
});

test('completed generation replay is explicitly stale after a newer execution', async (t) => {
  const f = await fixture(t); const input = job(); send(f.worker, input); await f.record(isResult());
  send(f.worker, { ...input, execution_generation: 2 }); await f.record(isResult(2));
  const previousResults = f.records.filter(isResult()).length;
  send(f.worker, input); assert.equal(f.records.at(-1).payload.reason, 'stale_generation');
  assert.equal(f.records.filter(isResult()).length, previousResults);
});

test('only the newest pending replacement runs after predecessor termination', async (t) => {
  const f = await fixture(t); const input = job({ fen: 'fenced' }); send(f.worker, input);
  await f.event((event) => event.event === 'execution_started');
  send(f.worker, { ...input, execution_generation: 2 }); send(f.worker, { ...input, execution_generation: 3 });
  await f.record(isResult(3));
  assert.equal(f.records.some(isResult(2)), false);
  assert.equal(f.records.find(isStatus('failed', 2)).payload.reason, 'stale_generation');
});

test('pending replacement cancellation does not terminate the replacement model session', async (t) => {
  const f = await fixture(t); const input = job({ fen: 'fenced' }); send(f.worker, input);
  const started = await f.event((event) => event.event === 'execution_started');
  send(f.worker, { ...input, execution_generation: 2 }); cancel(f.worker, cancellation(input, { execution_generation: 2 }));
  await f.record(isAck('terminated', 2)); await f.record(isStatus('cancelled', 2));
  const ready = await f.event((event) => event.event === 'ready' && event.pid !== started.pid);
  assert.equal(f.worker.child.pid, ready.pid); assert.equal(f.records.some(isResult(2)), false);
});

test('replacement deadline includes termination and cold reinitialization', async (t) => {
  let children = 0;
  const f = await fixture(t, { childFactory: () => fork(new URL('./fixtures/fake-child.mjs', import.meta.url), children++ === 0 ? [] : ['--startup-delay=250'], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'] }) });
  const input = job({ fen: 'fenced', constraints: { max_wall_time_ms: 100, max_memory_mb: 1024 } }); send(f.worker, input);
  await f.event((event) => event.event === 'execution_started'); send(f.worker, { ...input, execution_generation: 2 });
  const failure = await f.record(isStatus('failed', 2)); assert.equal(failure.payload.reason, 'execution_timeout');
  assert.equal(f.diagnostics.some((event) => event.event === 'execution_started' && event.execution_generation === 2), false);
});

test('competing jobs are rejected instead of entering an unbounded queue', async (t) => {
  const f = await fixture(t); send(f.worker, job({ fen: 'hang' })); await f.event((event) => event.event === 'execution_started');
  send(f.worker, job({ job_id: 'other' }));
  assert.equal(f.records.at(-1).payload.reason, 'worker_busy'); assert.equal(f.records.at(-1).payload.retryable, true);
});

test('unknown and future cancellation tombstones never terminate another generation', async (t) => {
  const f = await fixture(t); const input = job({ fen: 'delay' }); send(f.worker, input); await f.event((event) => event.event === 'execution_started');
  cancel(f.worker, cancellation(input, { execution_generation: 2 })); await f.record(isAck('requested', 2));
  await f.record(isResult(1)); assert.equal(f.records.some(isAck('terminated', 2)), false);
  send(f.worker, { ...input, execution_generation: 2 });
  await f.record(isStatus('cancelled', 2)); await f.record(isAck('terminated', 2));
  assert.equal(f.diagnostics.filter((event) => event.event === 'execution_started').length, 1);
  cancel(f.worker, cancellation(input, { execution_generation: 2 })); assert.equal(f.records.at(-1).payload.status, 'terminated');
});

test('cancellation before job delivery prevents execution and confirms termination after receipt', async (t) => {
  const f = await fixture(t); const input = job(); cancel(f.worker, cancellation(input)); await f.record(isAck('requested'));
  send(f.worker, input); await f.record(isAck('terminated')); await f.record(isStatus('cancelled'));
  assert.equal(f.diagnostics.some((event) => event.event === 'execution_started'), false);
});

test('synchronous cancellation of pending work prevents dispatch without killing the ready model', async (t) => {
  const f = await fixture(t); const input = job(); const initialPid = f.worker.child.pid;
  f.worker.on('record', (record) => { if (isStatus('pending')(record)) cancel(f.worker, cancellation(input)); });
  send(f.worker, input); await f.record(isAck('terminated'));
  assert.equal(f.worker.child.pid, initialPid); assert.equal(f.worker.childReady, true);
  assert.equal(f.diagnostics.some((event) => event.event === 'execution_started'), false);
});

test('every admitted cancellation ID receives final termination acknowledgement', async (t) => {
  const f = await fixture(t); const input = job({ fen: 'hang' }); send(f.worker, input);
  await f.event((event) => event.event === 'execution_started');
  cancel(f.worker, cancellation(input, { cancellation_id: 'first' }));
  cancel(f.worker, cancellation(input, { cancellation_id: 'second' }));
  cancel(f.worker, cancellation(input, { cancellation_id: 'second' }));
  await f.record((record) => isAck('terminated')(record) && record.payload.cancellation_id === 'second');
  assert.equal(f.records.filter((record) => isAck('terminated')(record) && record.payload.cancellation_id === 'first').length, 1);
  assert.equal(f.records.filter((record) => isAck('terminated')(record) && record.payload.cancellation_id === 'second').length, 1);
});

test('cancellation identity storage is bounded without promising unrecorded termination', async (t) => {
  const f = await fixture(t); const input = job();
  for (let index = 0; index < 64; index++) cancel(f.worker, cancellation(input, { cancellation_id: `cancel-${index}` }));
  cancel(f.worker, cancellation(input, { cancellation_id: 'overflow' }));
  assert.equal(f.records.at(-1).payload.reason, 'cancellation_capacity_exhausted'); assert.equal(f.records.at(-1).payload.retryable, true);
  assert.equal(f.records.some((record) => record.model === 'CancellationAcknowledgement' && record.payload.cancellation_id === 'overflow'), false);
  send(f.worker, input);
  assert.equal(f.records.filter(isAck('terminated')).length, 64);
  cancel(f.worker, cancellation(input, { cancellation_id: 'after-settlement' })); assert.equal(f.records.at(-1).payload.status, 'terminated');
});

test('inflight CPU computation is killed and capacity returns with a different process', async (t) => {
  const f = await fixture(t); const input = job({ fen: 'hang' }); send(f.worker, input);
  const started = await f.event((event) => event.event === 'execution_started');
  cancel(f.worker, cancellation(input)); await f.record(isAck('requested')); await f.record(isAck('terminated'));
  assert.throws(() => process.kill(started.pid, 0), { code: 'ESRCH' });
  await f.event((event) => event.event === 'ready' && event.pid !== started.pid);
  send(f.worker, job({ job_id: 'next' })); await f.record((record) => record.model === 'MaiaResult' && record.payload.job_id === 'next');
  assert.equal(f.records.some((record) => isResult()(record) && record.payload.job_id === input.job_id), false);
});

test('completion wins late cancellation without killing a warm model session', async (t) => {
  const f = await fixture(t); const input = job(); send(f.worker, input); await f.record(isResult());
  const originalPid = f.worker.child.pid; cancel(f.worker, cancellation(input)); await f.record(isAck('already_completed'));
  assert.equal(f.worker.child.pid, originalPid); assert.equal(f.worker.childReady, true);
});

test('deadline kills computation and suppresses stale results before reporting failure', async (t) => {
  const f = await fixture(t); const input = job({ fen: 'hang', constraints: { max_wall_time_ms: 60, max_memory_mb: 1024 } }); send(f.worker, input);
  const started = await f.event((event) => event.event === 'execution_started'); const failed = await f.record(isStatus('failed'));
  assert.equal(failed.payload.reason, 'execution_timeout'); assert.equal(failed.payload.retryable, true);
  assert.throws(() => process.kill(started.pid, 0), { code: 'ESRCH' });
  assert.equal(f.records.some(isResult()), false);
});

test('late result is fenced even when the event loop delays the deadline timer', async (t) => {
  const f = await fixture(t); const input = job({ fen: 'hang', constraints: { max_wall_time_ms: 300, max_memory_mb: 1024 } }); send(f.worker, input);
  await f.event((event) => event.event === 'execution_started');
  f.worker.active.deadlineAt = performance.now() - 1;
  f.worker.handleChildMessage(f.worker.child, { type: 'result', token: f.worker.active.token, predictions: [{ move: 'e2e4', probability: 0.5 }, { move: 'd2d4', probability: 0.5 }] });
  const failure = await f.record(isStatus('failed')); assert.equal(failure.payload.reason, 'execution_timeout'); assert.equal(f.records.some(isResult()), false);
});

test('worker crash is retryable and same-generation retries do not recompute', async (t) => {
  const f = await fixture(t); const input = job({ fen: 'crash' }); send(f.worker, input); const failed = await f.record(isStatus('failed'));
  assert.equal(failed.payload.reason, 'worker_crashed'); send(f.worker, input);
  assert.deepEqual(f.records.at(-1), failed); cancel(f.worker, cancellation(input)); await f.record(isAck('terminated'));
});

for (const fen of ['wrong_moves', 'invalid_mass', 'invalid_number']) test(`invalid child output ${fen} cannot become a successful result`, async (t) => {
  const f = await fixture(t); send(f.worker, job({ fen })); const failed = await f.record(isStatus('failed'));
  assert.equal(failed.payload.reason, 'invalid_model_output'); assert.equal(f.records.some(isResult()), false);
});

test('bounded receipts reject unseen generation records but preserve known replay and cancellation', async (t) => {
  const f = await fixture(t, { recordLimit: 2 }); const first = job(); send(f.worker, first); await f.record(isResult());
  cancel(f.worker, cancellation(job({ job_id: 'unknown' }))); await f.record((record) => record.model === 'CancellationAcknowledgement' && record.payload.job_id === 'unknown');
  send(f.worker, job({ execution_generation: 2 })); assert.equal(f.records.at(-1).payload.reason, 'session_capacity_exhausted');
  cancel(f.worker, cancellation(job({ job_id: 'third' }))); assert.equal(f.records.at(-1).payload.reason, 'session_capacity_exhausted');
  send(f.worker, first); assert.equal(f.records.at(-2).model, 'MaiaResult');
  cancel(f.worker, cancellation(first)); assert.equal(f.records.at(-1).payload.status, 'already_completed');
});

test('memory budget and unsupported model fail explicitly before execution', async (t) => {
  const f = await fixture(t); send(f.worker, job({ constraints: { max_wall_time_ms: 2000, max_memory_mb: 768 } }));
  assert.equal(f.records.at(-1).payload.reason, 'memory_budget_unsupported');
  send(f.worker, job({ job_id: 'unsupported', model_id: 'wrong' })); assert.equal(f.records.at(-1).payload.reason, 'unsupported_model');
  assert.equal(f.diagnostics.some((event) => event.event === 'execution_started'), false);
});

test('invalid records without usable identity throw instead of silently losing receipt', async (t) => {
  const f = await fixture(t);
  assert.throws(() => f.worker.receive({ model: 'MaiaJob', payload: {} }), { code: 'invalid_request' });
  assert.equal(f.diagnostics.at(-1).event, 'protocol_error');
  assert.equal(f.records.length, 0);
  f.worker.receive({ model: 'MaiaJob', payload: job({ unexpected: true }) });
  assert.equal(f.records.at(-1).model, 'JobStatus'); assert.equal(f.records.at(-1).payload.reason, 'invalid_request');
});

test('startup model failure rejects readiness and shuts down its child', async () => {
  const worker = new MaiaWorker({ childFactory: () => fork(new URL('./fixtures/fake-child.mjs', import.meta.url), ['--startup-fail'], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'] }), resourceInspector: async () => resources });
  await assert.rejects(worker.start(), { code: 'model_asset_missing' }); assert.equal(worker.child, null); assert.equal(worker.closed, true);
});

test('EOF-style graceful close drains admitted computation then releases process', async (t) => {
  const f = await fixture(t); send(f.worker, job({ fen: 'delay' })); await f.worker.close({ drain: true });
  assert.equal(f.records.filter(isResult()).length, 1); assert.equal(f.worker.child, null);
});

test('new session requires caller generation advancement and preserves requesting-owner acceptance fencing', async (t) => {
  const first = await fixture(t); const input = job(); send(first.worker, input); const original = (await first.record(isResult())).payload;
  await first.worker.close({ drain: true });
  const second = await fixture(t); const resumed = { ...input, execution_generation: 2 }; send(second.worker, resumed); const replacement = (await second.record(isResult(2))).payload;
  assert.equal(original.execution_generation < resumed.execution_generation, true);
  assert.deepEqual(original.predictions, replacement.predictions);
  assert.notEqual(original.provenance.started_at, replacement.provenance.started_at);
  const accepted = [original, replacement].filter((result) => result.execution_generation === resumed.execution_generation);
  assert.equal(accepted.length, 1);
});

test('NDJSON replay backpressure pauses parsing and keeps output memory bounded', async (t) => {
  const f = await fixture(t); const inputJob = job(); send(f.worker, inputJob); await f.record(isResult());
  const input = new PassThrough(); let maximumBuffered = 0; const chunks = [];
  const output = new Writable({ highWaterMark: 1, write(chunk, _encoding, callback) { chunks.push(chunk.toString()); setTimeout(callback, 1); } });
  const originalWrite = output.write.bind(output);
  output.write = (...args) => { const accepted = originalWrite(...args); maximumBuffered = Math.max(maximumBuffered, output.writableLength); return accepted; };
  const protocol = serveProtocol(f.worker, { input, output });
  const line = `${JSON.stringify({ model: 'MaiaJob', payload: inputJob })}\n`;
  input.end(line.repeat(100));
  await protocol.done;
  await new Promise((resolve) => output.end(resolve));
  assert.equal(chunks.length, 200); assert.ok(maximumBuffered < 4096, `Output buffering exceeded one bounded response burst: ${maximumBuffered}`);
});

for (const [name, payload, reason] of [
  ['malformed JSON', Buffer.from('{broken}\n'), 'invalid_json'],
  ['unaddressable record', Buffer.from('{"model":"MaiaJob","payload":{}}\n'), 'invalid_request'],
  ['unterminated record', Buffer.from('{"model":"MaiaJob"}'), 'unterminated_record'],
  ['oversized line', Buffer.from(`${'x'.repeat(MAX_LINE_BYTES + 1)}\n`), 'line_too_large'],
  ['invalid UTF8', Buffer.from([0xc3, 0x28, 10]), 'invalid_utf8'],
]) test(`NDJSON ${name} fails and closes its execution session`, async (t) => {
  const f = await fixture(t); const input = new PassThrough(); const output = new PassThrough(); output.resume();
  const protocol = serveProtocol(f.worker, { input, output }); input.end(payload);
  const result = await protocol.done;
  assert.equal(result.success, false); assert.equal(result.reason, reason); assert.equal(f.worker.child, null);
});

test('broken output interrupts an executing job even after EOF began draining', async (t) => {
  const f = await fixture(t); const input = new PassThrough();
  const output = new Writable({ write(_chunk, _encoding, callback) { callback(Object.assign(new Error('Closed output'), { code: 'EPIPE' })); } });
  const protocol = serveProtocol(f.worker, { input, output });
  input.end(`${JSON.stringify({ model: 'MaiaJob', payload: job({ fen: 'hang' }) })}\n`);
  const result = await protocol.done;
  assert.equal(result.success, false); assert.equal(f.worker.child, null); assert.equal(f.records.some(isResult()), false);
});

// Explicit child gates make predecessor-exit and model-warmup windows reproducible.
async function lifecycleFixture(t, { holdExit = false, gateWarmup = false } = {}) {
  let childCount = 0, releaseExit = () => {}, releaseReady = () => {};
  let signalInitializing;
  const initializing = new Promise(resolve => { signalInitializing = resolve; });
  t.after(() => releaseExit());
  const f = await fixture(t, { childFactory: () => {
    const childIndex = childCount++;
    const child = fork(new URL('./fixtures/fake-child.mjs', import.meta.url), childIndex > 0 && gateWarmup ? ['--startup-gated'] : [], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'] });
    if (childIndex === 0 && holdExit) {
      const kill = child.kill.bind(child);
      let requestedSignal;
      child.kill = signal => { requestedSignal = signal; return true; };
      releaseExit = () => { child.kill = kill; if (requestedSignal) { const signal = requestedSignal; requestedSignal = null; kill(signal); } };
    }
    if (childIndex > 0 && gateWarmup) {
      child.on('message', message => { if (message.type === 'initializing') signalInitializing(); });
      releaseReady = () => child.send({ type: 'release_ready' });
    }
    return child;
  } });
  return { ...f, initializing, releaseExit: () => releaseExit(), releaseReady: () => releaseReady() };
}

for (const duringWarmup of [false, true]) test(`repair A${duringWarmup ? 2 : 1}: pre-cancelled newest generation fences replacement ${duringWarmup ? 'during warmup' : 'before predecessor exit'}`, { timeout: 10000 }, async t => {
  const f = await lifecycleFixture(t, { holdExit: !duringWarmup, gateWarmup: duringWarmup });
  const input = job({ fen: 'fenced' }); send(f.worker, input);
  const started = await f.event(event => event.event === 'execution_started');
  send(f.worker, { ...input, execution_generation: 2 });
  const obsolete = f.worker.replacement, timer = obsolete.timer;
  if (duringWarmup) await f.initializing;
  cancel(f.worker, cancellation(input, { execution_generation: 3 }));
  send(f.worker, { ...input, execution_generation: 3 });
  assert.equal(f.worker.logicalRequests.get(JSON.stringify(['workspace-a', 'job-a', 1])).highestGeneration, 3);
  f.releaseExit(); if (duringWarmup) f.releaseReady();
  await f.event(event => event.event === 'ready' && event.pid !== started.pid);
  if (f.worker.active?.record === obsolete) await f.record(isResult(2));
  assert.equal(f.records.some(isResult(2)), false, 'obsolete replacement published a result');
  assert.equal(f.records.some(isResult(1)), false);
  assert.equal(f.diagnostics.some(event => event.event === 'execution_started' && event.execution_generation === 2), false);
  assert.equal(f.records.find(isStatus('failed', 2))?.payload.reason, 'stale_generation');
  assert.equal(f.records.filter(isStatus('failed', 2)).length, 1);
  assert.equal(f.records.find(isStatus('cancelled', 3))?.payload.reason, 'cancelled_before_execution');
  assert.equal(f.records.filter(isAck('terminated', 3)).length, 1);
  assert.equal(f.worker.replacement, null); assert.equal(timer._destroyed, true);
  send(f.worker, job({ job_id: 'recovered' }));
  await f.record(record => record.model === 'MaiaResult' && record.payload.job_id === 'recovered');
});

test('repair B1: every superseded pre-job cancellation settles without resend', { timeout: 10000 }, async t => {
  const f = await fixture(t), input = job();
  for (const cancellation_id of ['first', 'second']) cancel(f.worker, cancellation(input, { cancellation_id }));
  assert.equal(f.records.filter(isAck('requested')).length, 2);
  send(f.worker, { ...input, execution_generation: 2 });
  assert.deepEqual(f.records.filter(isAck('stale_generation')).map(record => record.payload.cancellation_id).sort(), ['first', 'second'], 'retained cancellation IDs remain unresolved');
  for (const record of f.records) validateRecord(record);
  await f.record(isResult(2));
  send(f.worker, input); assert.equal(f.records.at(-1).payload.reason, 'stale_generation');
  for (const cancellation_id of ['first', 'second']) {
    cancel(f.worker, cancellation(input, { cancellation_id }));
    assert.equal(f.records.at(-1).payload.status, 'stale_generation');
  }
  assert.equal(f.records.filter(isAck('requested')).length, 2);
  assert.equal(f.diagnostics.filter(event => event.event === 'execution_started').length, 1);
});

test('repair A3: future cancellation alone leaves the pending generation eligible', { timeout: 10000 }, async t => {
  const f = await lifecycleFixture(t, { holdExit: true }), input = job({ fen: 'fenced' });
  send(f.worker, input); await f.event(event => event.event === 'execution_started');
  send(f.worker, { ...input, execution_generation: 2 });
  cancel(f.worker, cancellation(input, { execution_generation: 3 }));
  assert.equal(f.worker.replacement.identity.execution_generation, 2);
  f.releaseExit(); await f.record(isResult(2));
  assert.deepEqual(f.records.filter(record => record.model === 'CancellationAcknowledgement').map(record => record.payload.status), ['requested']);
});

test('repair A4: result handler independently fences stale active state and preserves terminal receipts', { timeout: 10000 }, async t => {
  const f = await fixture(t), input = job({ fen: 'hang' });
  send(f.worker, input); await f.event(event => event.event === 'execution_started');
  const active = f.worker.active;
  // Independently exercise the defensive publication boundary, without queue cleanup
  // or stopActive having hidden the stale active state from the result handler.
  f.worker.logicalRequests.get(JSON.stringify(['workspace-a', 'job-a', 1])).highestGeneration = 2;
  f.worker.handleChildMessage(active.child, { type: 'result', token: active.token, predictions: input.legal_moves.map(move => ({ move, probability: 0.5 })) });
  const failed = await f.record(isStatus('failed'));
  assert.equal(failed.payload.reason, 'stale_generation');
  f.worker.handleChildMessage(active.child, { type: 'result', token: active.token, predictions: [] });
  assert.equal(f.records.some(isResult()), false); assert.equal(f.records.some(isStatus('completed')), false);
  assert.deepEqual(active.record.status, failed.payload);
  await f.event(event => event.event === 'ready' && event.pid !== active.child.pid);
  send(f.worker, job({ job_id: 'fresh' })); await f.record(record => record.model === 'MaiaResult' && record.payload.job_id === 'fresh');
});

for (const changes of [{ workspace_id: 'workspace-other' }, { job_id: 'job-other' }, { request_revision: 2 }]) test(`repair B3: logical identity isolation ${JSON.stringify(changes)}`, { timeout: 10000 }, async t => {
  const f = await fixture(t), input = job(), unrelated = job(changes);
  cancel(f.worker, cancellation(input)); cancel(f.worker, cancellation(unrelated, { cancellation_id: 'unrelated' }));
  cancel(f.worker, cancellation(input, { execution_generation: 3, cancellation_id: 'future' }));
  send(f.worker, { ...input, execution_generation: 2 }); await f.record(isResult(2));
  assert.deepEqual(f.records.filter(isAck('stale_generation')).map(record => record.payload.cancellation_id), ['cancel-a']);
  for (const cancellation_id of ['unrelated', 'future']) assert.deepEqual(f.records.filter(record => record.model === 'CancellationAcknowledgement' && record.payload.cancellation_id === cancellation_id).map(record => record.payload.status), ['requested']);
  send(f.worker, unrelated); await f.record(record => record.model === 'JobStatus' && record.payload.reason === 'cancelled_before_execution' && Object.entries(changes).every(([key, value]) => record.payload[key] === value));
});

test('repair B4: supersession finalizes exactly the 64 retained cancellation IDs', { timeout: 10000 }, async t => {
  const f = await fixture(t), input = job();
  for (let index = 0; index < 64; index++) cancel(f.worker, cancellation(input, { cancellation_id: `bounded-${index}` }));
  cancel(f.worker, cancellation(input, { cancellation_id: 'overflow' }));
  assert.equal(f.records.at(-1).payload.reason, 'cancellation_capacity_exhausted');
  send(f.worker, { ...input, execution_generation: 2 });
  const final = f.records.filter(isAck('stale_generation'));
  assert.equal(final.length, 64); assert.equal(new Set(final.map(record => record.payload.cancellation_id)).size, 64);
  assert.equal(f.records.some(record => record.model === 'CancellationAcknowledgement' && record.payload.cancellation_id === 'overflow'), false);
  const tombstone = f.worker.executions.get(JSON.stringify(['workspace-a', 'job-a', 1, 1]));
  assert.equal(tombstone.cancellations.size, 64); assert.equal(tombstone.cancellationOutcomes.size, 64);
  await f.record(isResult(2));
});

test('repair C1: newest ordinary replacement retains its slot throughout gated warmup', { timeout: 10000 }, async t => {
  const f = await lifecycleFixture(t, { gateWarmup: true }), input = job({ fen: 'fenced' });
  send(f.worker, input); await f.event(event => event.event === 'execution_started');
  send(f.worker, { ...input, execution_generation: 2 }); await f.initializing;
  send(f.worker, { ...input, execution_generation: 3 });
  assert.equal(f.worker.replacement.identity.execution_generation, 3);
  f.releaseReady(); await f.record(isResult(3));
  assert.equal(f.records.find(isStatus('failed', 2)).payload.reason, 'stale_generation');
  assert.deepEqual(f.diagnostics.filter(event => event.event === 'execution_started').map(event => event.execution_generation), [1, 3]);
});

test('repair C2: supersession preserves requested cancellation until confirmed child exit', { timeout: 10000 }, async t => {
  const f = await lifecycleFixture(t, { holdExit: true }), input = job({ fen: 'fenced' });
  send(f.worker, input); const started = await f.event(event => event.event === 'execution_started');
  cancel(f.worker, cancellation(input)); send(f.worker, { ...input, execution_generation: 2 });
  assert.equal(f.records.filter(isAck('requested')).length, 1);
  assert.equal(f.records.some(isAck('terminated')), false);
  assert.doesNotThrow(() => process.kill(started.pid, 0));
  const active = f.worker.active;
  f.worker.handleChildMessage(active.child, { type: 'result', token: active.token, predictions: [] });
  f.worker.on('record', record => { if (isAck('terminated')(record)) assert.throws(() => process.kill(started.pid, 0), { code: 'ESRCH' }); });
  f.releaseExit(); await f.record(isAck('terminated')); await f.record(isResult(2));
  assert.equal(f.records.find(isStatus('cancelled')).payload.reason, 'cancelled_by_request');
  assert.equal(f.records.some(isResult(1)), false);
});

for (const changes of [{ input_digest: 'changed' }, { legal_moves: ['d2d4', 'e2e4'] }, { constraints: { max_wall_time_ms: 2001, max_memory_mb: 1024 } }, { model_id: 'different-model' }]) test(`repair C4: conflicting newer payload cannot cancel valid execution ${Object.keys(changes)[0]}`, { timeout: 10000 }, async t => {
  const f = await fixture(t), input = job({ fen: 'hang' });
  send(f.worker, input); await f.event(event => event.event === 'execution_started');
  const active = f.worker.active;
  send(f.worker, { ...input, ...changes, execution_generation: 2 });
  assert.equal(f.records.at(-1).payload.reason, 'identity_payload_conflict');
  assert.equal(f.worker.active, active); assert.equal(active.stopping, null); assert.equal(f.worker.isCurrent(active.record), true);
  f.worker.receive({ model: 'MaiaJob', payload: { ...input, execution_generation: 2, unexpected: true } });
  assert.equal(f.records.at(-1).payload.reason, 'invalid_request'); assert.equal(active.stopping, null);
});

test('repair C4: receipt capacity rejection does not supersede existing work', { timeout: 10000 }, async t => {
  const f = await fixture(t, { recordLimit: 1 }), input = job({ fen: 'hang' });
  send(f.worker, input); await f.event(event => event.event === 'execution_started');
  send(f.worker, { ...input, execution_generation: 2 });
  assert.equal(f.records.at(-1).payload.reason, 'session_capacity_exhausted');
  assert.equal(f.worker.active.stopping, null); assert.equal(f.worker.isCurrent(f.worker.active.record), true);
});

for (const phase of ['terminating', 'initializing', 'superseded']) for (const drain of [true, false]) test(`repair C5: close drain=${drain} during ${phase} replacement lifecycle`, { timeout: 10000 }, async t => {
  const f = await lifecycleFixture(t, { holdExit: phase !== 'initializing', gateWarmup: phase === 'initializing' }), input = job({ fen: 'fenced' });
  send(f.worker, input); await f.event(event => event.event === 'execution_started');
  send(f.worker, { ...input, execution_generation: 2 }); const replacementTimer = f.worker.replacement.timer;
  if (phase === 'initializing') await f.initializing;
  if (phase === 'superseded') {
    cancel(f.worker, cancellation(input, { execution_generation: 3 })); send(f.worker, { ...input, execution_generation: 3 });
  }
  const closing = f.worker.close({ drain });
  if (phase === 'initializing' && drain) f.releaseReady();
  f.releaseExit(); await closing;
  assert.equal(f.worker.closed, true); assert.equal(f.worker.child, null); assert.equal(f.worker.active, null); assert.equal(f.worker.replacement, null);
  assert.equal(replacementTimer._destroyed, true);
  if (!drain || phase === 'superseded') assert.equal(f.records.some(isResult(2)), false);
  else assert.equal(f.records.filter(isResult(2)).length, 1);
  assert.equal(f.records.filter(record => record.model === 'JobStatus' && record.payload.execution_generation === 2 && ['failed', 'completed', 'cancelled'].includes(record.payload.status)).length, 1);
});

test('repair C5: interrupting an existing drain wakes a removed warmup replacement', { timeout: 10000 }, async t => {
  const f = await lifecycleFixture(t, { gateWarmup: true }), input = job({ fen: 'fenced' });
  send(f.worker, input); await f.event(event => event.event === 'execution_started');
  send(f.worker, { ...input, execution_generation: 2 }); await f.initializing;
  const draining = f.worker.close({ drain: true });
  await Promise.resolve();
  assert.equal(f.worker.close({ drain: false }), draining);
  await draining; assert.equal(f.worker.child, null); assert.equal(f.records.some(isResult(2)), false);
});

test('repair C6: reentrant generation advance observes all tombstones settled and prevents intermediate dispatch', { timeout: 10000 }, async t => {
  const f = await fixture(t), input = job();
  for (const cancellation_id of ['first', 'second']) cancel(f.worker, cancellation(input, { cancellation_id }));
  let reentered = false;
  f.worker.on('record', record => {
    if (!isAck('stale_generation')(record) || reentered) return;
    reentered = true;
    const tombstone = f.worker.executions.get(JSON.stringify(['workspace-a', 'job-a', 1, 1]));
    assert.equal(tombstone.cancellationOutcomes.size, 2);
    send(f.worker, { ...input, execution_generation: 3 });
  });
  send(f.worker, { ...input, execution_generation: 2 }); await f.record(isResult(3));
  assert.equal(f.records.some(isResult(2)), false);
  assert.equal(f.records.filter(isStatus('failed', 2)).length, 1);
  assert.equal(f.records.filter(isAck('stale_generation')).length, 2);
});

test('repair C6: reentrant newer job during pending status prevents old dispatch on the warm child', { timeout: 10000 }, async t => {
  const f = await fixture(t), input = job(), pid = f.worker.child.pid;
  f.worker.on('record', record => { if (isStatus('pending', 1)(record)) send(f.worker, { ...input, execution_generation: 2 }); });
  send(f.worker, input); await f.record(isResult(2));
  assert.equal(f.worker.child.pid, pid); assert.equal(f.records.some(isResult(1)), false);
  assert.deepEqual(f.diagnostics.filter(event => event.event === 'execution_started').map(event => event.execution_generation), [2]);
});

for (const operation of ['cancel', 'close']) test(`repair C6: reentrant ${operation} during generation settlement prevents admission from resuming`, { timeout: 10000 }, async t => {
  const f = await fixture(t), input = job();
  cancel(f.worker, cancellation(input));
  f.worker.on('record', record => {
    if (!isAck('stale_generation')(record)) return;
    if (operation === 'cancel') cancel(f.worker, cancellation(input, { execution_generation: 2 }));
    else void f.worker.close({ drain: false });
  });
  send(f.worker, { ...input, execution_generation: 2 });
  assert.equal(f.records.filter(isStatus(operation === 'cancel' ? 'cancelled' : 'failed', 2)).length, 1);
  if (operation === 'cancel') assert.equal(f.records.filter(isAck('terminated', 2)).length, 1);
  await f.worker.close({ drain: false });
  assert.equal(f.records.some(isResult(2)), false); assert.equal(f.diagnostics.some(event => event.event === 'execution_started'), false);
});

test('repair C5: synchronous interrupt on replacement readiness prevents dispatch', { timeout: 10000 }, async t => {
  const f = await lifecycleFixture(t, { gateWarmup: true }), input = job({ fen: 'fenced' });
  send(f.worker, input); const started = await f.event(event => event.event === 'execution_started');
  send(f.worker, { ...input, execution_generation: 2 }); await f.initializing;
  f.worker.on('diagnostic', event => { if (event.event === 'ready' && event.pid !== started.pid) void f.worker.close({ drain: false }); });
  f.releaseReady(); await f.event(event => event.event === 'ready' && event.pid !== started.pid);
  await f.worker.close({ drain: false });
  assert.equal(f.records.some(isResult(2)), false); assert.equal(f.records.find(isStatus('failed', 2)).payload.reason, 'execution_interrupted');
  assert.equal(f.worker.child, null);
});

test('repair C7: admitted early model failure fences and clears older queued execution', { timeout: 10000 }, async t => {
  const f = await lifecycleFixture(t, { holdExit: true }), input = job({ fen: 'fenced' });
  send(f.worker, input); await f.event(event => event.event === 'execution_started');
  send(f.worker, { ...input, execution_generation: 2 }); const obsoleteTimer = f.worker.replacement.timer;
  f.worker.identity = { ...f.worker.identity, model_id: 'unavailable-fixture-model' };
  send(f.worker, { ...input, execution_generation: 3 });
  assert.equal(f.records.find(isStatus('failed', 3)).payload.reason, 'unsupported_model');
  assert.equal(f.records.find(isStatus('failed', 2)).payload.reason, 'stale_generation');
  assert.equal(f.worker.replacement, null); assert.equal(obsoleteTimer._destroyed, true);
  f.releaseExit(); await f.record(isStatus('failed', 1));
  assert.equal(f.records.some(isResult()), false);
});

test('repair C5: synchronous interrupt during pending status fences dispatch immediately', { timeout: 10000 }, async t => {
  const f = await fixture(t), input = job();
  f.worker.on('record', record => { if (isStatus('pending')(record)) void f.worker.close({ drain: false }); });
  send(f.worker, input); await f.worker.close({ drain: false });
  assert.equal(f.records.some(isResult()), false); assert.equal(f.diagnostics.some(event => event.event === 'execution_started'), false);
  assert.equal(f.records.find(isStatus('failed')).payload.reason, 'execution_interrupted'); assert.equal(f.worker.child, null);
});

test('repair C5: synchronous interrupt fences child result before deferred close cleanup', { timeout: 10000 }, async t => {
  const f = await fixture(t), input = job({ fen: 'hang' });
  send(f.worker, input); await f.event(event => event.event === 'execution_started');
  const active = f.worker.active, closing = f.worker.close({ drain: false });
  f.worker.handleChildMessage(active.child, { type: 'result', token: active.token, predictions: input.legal_moves.map(move => ({ move, probability: 0.5 })) });
  await closing;
  assert.equal(f.records.some(isResult()), false); assert.equal(f.records.find(isStatus('failed')).payload.reason, 'execution_interrupted');
});
