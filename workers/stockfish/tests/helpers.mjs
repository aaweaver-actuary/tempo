import { readFileSync } from 'node:fs';
import { fork } from 'node:child_process';
import { StockfishWorker } from '../worker.mjs';
import { stockfishInputDigest } from '../wire.mjs';
export const sample = JSON.parse(readFileSync(new URL('../fixtures/synthetic/job.json', import.meta.url))).payload;
export const resources = { memory_enforced: true, memory_limit_bytes: 512 * 1024 * 1024, memory_limit_mb: 512, cpu_limit: 1, swap_limit_bytes: 0, source: 'test-cgroup' };
export function job(changes = {}) { const value = { ...structuredClone(sample), search_moves: [], max_depth: 4, max_nodes: 100000, ...changes }; value.input_digest = stockfishInputDigest(value); return value; }
export function cancellation(input, changes = {}) { return { contract_version: 1, workspace_id: input.workspace_id, job_id: input.job_id, request_revision: input.request_revision, execution_generation: input.execution_generation, cancellation_id: 'cancel-a', ...changes }; }
export function send(worker, value) { worker.receive({ model: 'StockfishJob', payload: value }); }
export function cancel(worker, value) { worker.receive({ model: 'CancellationRequest', payload: value }); }
export const isStatus = (status, generation = 1) => record => record.model === 'JobStatus' && record.payload.status === status && record.payload.execution_generation === generation;
export const isResult = (generation = 1) => record => record.model === 'StockfishResult' && record.payload.execution_generation === generation;
export const isAck = (status, generation = 1) => record => record.model === 'CancellationAcknowledgement' && record.payload.status === status && record.payload.execution_generation === generation;
export function waitFor(worker, collection, predicate, event = 'record', timeout = 5000) {
  const existing = collection.find(predicate); if (existing) return Promise.resolve(existing);
  return new Promise((resolve, reject) => {
    const listener = value => { if (predicate(value)) { clearTimeout(timer); worker.off(event, listener); resolve(value); } };
    const timer = setTimeout(() => { worker.off(event, listener); reject(new Error('Expected ' + event + ': ' + JSON.stringify(collection))); }, timeout);
    worker.on(event, listener);
  });
}
export async function fixture(t, options = {}) {
  const worker = new StockfishWorker({ childFactory: () => fork(new URL('./fixtures/fake-child.mjs', import.meta.url), [], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'] }), resourceInspector: async () => resources, ...options });
  const records = [], diagnostics = [];
  worker.on('record', record => records.push(record)); worker.on('diagnostic', value => diagnostics.push(value));
  t.after(() => worker.close({ drain: false })); await worker.start();
  return { worker, records, diagnostics, record: predicate => waitFor(worker, records, predicate), event: predicate => waitFor(worker, diagnostics, predicate, 'diagnostic') };
}
