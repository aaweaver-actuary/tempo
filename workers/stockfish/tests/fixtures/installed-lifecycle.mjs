// Actual engine and installed runtime inside the qualified read-only cgroup.
import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { StockfishWorker } from '/opt/stockfish/worker.mjs';
import { stockfishInputDigest } from '/opt/stockfish/wire.mjs';
const children = [], records = [], diagnostics = [];
const worker = new StockfishWorker({ childFactory: () => {
  const child = fork('/opt/stockfish/child.mjs', [], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'], execArgv: [] });
  children.push(child); return child;
} });
worker.on('record', value => records.push(value)); worker.on('diagnostic', value => diagnostics.push(value));
async function until(predicate) {
  const deadline = performance.now() + 35000;
  while (!predicate()) {
    if (performance.now() > deadline) throw new Error('Installed lifecycle deadline');
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}
try {
  const first = worker.start(), joined = worker.start(); assert.equal(first, joined);
  const health = await first; assert.equal(children.length, 1);
  const input = { ...JSON.parse(process.argv[1]), engine_version: health.identity.engine_version,
    max_depth: 128, max_nodes: null, multi_pv: 1, search_moves: [], constraints: { max_wall_time_ms: 10000, max_memory_mb: 512 } };
  input.input_digest = stockfishInputDigest(input);
  records.push({ model: 'StockfishJob', payload: input });
  worker.receive({ model: 'StockfishJob', payload: input });
  await until(() => records.some(value => value.model === 'JobStatus' && value.payload.status === 'running'));
  const active = worker.active, original = worker.child;
  const ticks = () => { const text = readFileSync('/proc/' + original.pid + '/stat', 'utf8'); const fields = text.slice(text.lastIndexOf(')') + 2).split(' '); return Number(fields[11]) + Number(fields[12]); };
  const before = ticks(); await new Promise(resolve => setTimeout(resolve, 120)); const after = ticks();
  assert.ok(after > before, 'real engine CPU must advance before repeated startup');
  await worker.start(); await worker.prewarm();
  assert.equal(children.length, 1); assert.equal(worker.child, original); assert.equal(worker.active, active);
  const cancellation = { contract_version: 1, workspace_id: input.workspace_id, job_id: input.job_id,
    request_revision: input.request_revision, execution_generation: input.execution_generation, cancellation_id: 'installed-cancel' };
  records.push({ model: 'CancellationRequest', payload: cancellation }); worker.receive({ model: 'CancellationRequest', payload: cancellation });
  await worker.start();
  await until(() => records.some(value => value.model === 'CancellationAcknowledgement' && value.payload.status === 'terminated'));
  assert.throws(() => process.kill(original.pid, 0), { code: 'ESRCH' });
  await until(() => worker.childReady);
  const next = { ...input, job_id: 'installed-after-start', max_depth: 4 }; next.input_digest = stockfishInputDigest(next);
  records.push({ model: 'StockfishJob', payload: next });
  worker.receive({ model: 'StockfishJob', payload: next });
  await until(() => records.some(value => value.model === 'JobStatus' && value.payload.job_id === next.job_id && value.payload.status === 'completed'));
  assert.equal(records.filter(value => value.model === 'StockfishResult' && value.payload.job_id === next.job_id).length, 1);
  await worker.close({ drain: false });
  for (const child of children) assert.throws(() => process.kill(child.pid, 0), { code: 'ESRCH' });
  console.log(JSON.stringify({ success: true, children: children.map(child => child.pid),
    ready_events: diagnostics.filter(value => value.event === 'ready').length, identity: health.identity,
    cpu_ticks_before: before, cpu_ticks_after: after, records }));
} finally {
  await worker.close({ drain: false });
  for (const child of children) if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
}
