import test from 'node:test';
import assert from 'node:assert/strict';
import { once, EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import { resources, fixture, trackedWorker, fakeChild, job, cancellation, send, cancel, isResult, isStatus, isAck, waitFor } from './helpers.mjs';
import { serveProtocol, MAX_QUEUE_BYTES } from '../cli.mjs';
import { StockfishWorker } from '../worker.mjs';

test('B_exact_replay', async t => {
  const fixtureState = await fixture(t); const input = job(); send(fixtureState.worker, input); const result = await fixtureState.record(isResult());
  send(fixtureState.worker, input); assert.deepEqual(fixtureState.records.filter(isResult()), [result, result]);
  assert.equal(fixtureState.diagnostics.filter(e => e.event === 'execution_started').length, 1);
  const other = job({workspace_id:'another-workspace'}); send(fixtureState.worker, other);
  await fixtureState.record(r=>r.model==='StockfishResult'&&r.payload.workspace_id===other.workspace_id);
});
test('B_payload_conflict', async t => {
  const fixtureState=await fixture(t); const input=job(); send(fixtureState.worker,input); await fixtureState.record(isResult());
  rejectsWithoutExecutionFailure(fixtureState, () => send(fixtureState.worker,job({max_depth:5})), 'identity_payload_conflict');
  send(fixtureState.worker,{...job({job_id:'invalid'}), input_digest:'bad'}); assert.equal(fixtureState.records.at(-1).payload.reason,'input_digest_mismatch');
  assert.equal(fixtureState.worker.logicalRequests.size,1);
});
test('B_stale_generation', async t => {
  const fixtureState=await fixture(t); const input=job({max_nodes:999999}); send(fixtureState.worker,input); await fixtureState.record(isStatus('running'));
  const active=fixtureState.worker.active, oldChild=fixtureState.worker.child;
  send(fixtureState.worker,{...input,execution_generation:2});
  // A fabricated late callback with the old token cannot settle the successor.
  fixtureState.worker.handleChildMessage(oldChild,{type:'result',token:active.token,variations:[]});
  await fixtureState.event(e=>e.event==='ready'&&e.pid!==oldChild.pid); await fixtureState.record(isStatus('running',2));
  assert.equal(fixtureState.records.some(isResult(1)),false);
  rejectsWithoutExecutionFailure(fixtureState, () => send(fixtureState.worker,input), 'stale_generation');
  cancel(fixtureState.worker,cancellation({...input,execution_generation:2})); await fixtureState.record(isAck('terminated',2));
});
test('B_cancel_before_job', async t => {
  const fixtureState=await fixture(t); const input=job({max_nodes:999999}); send(fixtureState.worker,input); await fixtureState.record(isStatus('running'));
  const future={...input,execution_generation:3}; cancel(fixtureState.worker,cancellation(future));
  assert.equal(fixtureState.worker.active.record.job.execution_generation,1);
  send(fixtureState.worker,job({job_id:'unrelated'})); assert.equal(fixtureState.records.at(-1).payload.reason,'worker_busy');
  send(fixtureState.worker,future); await fixtureState.record(isAck('terminated',3));
  assert.equal(fixtureState.records.some(isResult(3)),false);
});
test('B_cancel_during_search', async t => {
  const fixtureState=await fixture(t); const input=job({max_nodes:999999}); send(fixtureState.worker,input); await fixtureState.record(isStatus('running'));
  const pid=fixtureState.worker.child.pid; cancel(fixtureState.worker,cancellation(input)); await fixtureState.record(isAck('terminated'));
  assert.throws(()=>process.kill(pid,0),{code:'ESRCH'});
  await fixtureState.event(e=>e.event==='ready'&&e.pid!==pid);
  send(fixtureState.worker,job({job_id:'capacity-recovered'})); await fixtureState.record(r=>r.model==='StockfishResult'&&r.payload.job_id==='capacity-recovered');
});
test('B_cancel_complete_race', async t => {
  const fixtureState=await fixture(t); const input=job(); send(fixtureState.worker,input); await fixtureState.record(isResult());
  cancel(fixtureState.worker,cancellation(input)); await fixtureState.record(isAck('already_completed'));
  const later=job({job_id:'cancel-wins',max_nodes:2}); send(fixtureState.worker,later);
  await fixtureState.record(r=>isStatus('running')(r)&&r.payload.job_id===later.job_id);
  cancel(fixtureState.worker,cancellation(later,{cancellation_id:'second'}));
  await fixtureState.record(r=>isAck('terminated')(r)&&r.payload.job_id===later.job_id);
  assert.equal(fixtureState.records.some(r=>r.model==='StockfishResult'&&r.payload.job_id===later.job_id),false);
});
test('B_multiple_cancellation_ids', async t => {
  const fixtureState=await fixture(t); const input=job({max_nodes:999999}); send(fixtureState.worker,input); await fixtureState.record(isStatus('running'));
  for(let i=0;i<64;i++) cancel(fixtureState.worker,cancellation(input,{cancellation_id:'id-'+i}));
  rejectsWithoutExecutionFailure(fixtureState, () => cancel(fixtureState.worker,cancellation(input,{cancellation_id:'excess'})), 'cancellation_capacity_exhausted');
  await fixtureState.record(r=>isAck('terminated')(r)&&r.payload.cancellation_id==='id-63');
  assert.equal(new Set(fixtureState.records.filter(isAck('terminated')).map(r=>r.payload.cancellation_id)).size,64);
  rejectsWithoutExecutionFailure(fixtureState, () => cancel(fixtureState.worker,cancellation(job({job_id:'other'}),{cancellation_id:'id-0'})), 'cancellation_id_conflict');
});
test('B_superseded_queued_work', async t => {
  const fixtureState=await fixture(t); const input=job({max_nodes:999999}); send(fixtureState.worker,input); await fixtureState.record(isStatus('running'));
  send(fixtureState.worker,{...input,execution_generation:2});
  cancel(fixtureState.worker,cancellation({...input,execution_generation:3}));
  send(fixtureState.worker,{...input,execution_generation:3}); await fixtureState.record(isAck('terminated',3));
  await fixtureState.event(e=>e.event==='ready'&&e.pid!==fixtureState.diagnostics.find(e=>e.event==='execution_started').pid);
  assert.equal(fixtureState.records.some(isStatus('running',2)),false); assert.equal(fixtureState.worker.replacement,null);
  const future=job({job_id:'future'}); cancel(fixtureState.worker,cancellation(future,{cancellation_id:'future-a'}));
  cancel(fixtureState.worker,cancellation(future,{cancellation_id:'future-b'})); send(fixtureState.worker,{...future,execution_generation:2});
  await fixtureState.record(r=>isAck('stale_generation')(r)&&r.payload.cancellation_id==='future-b');
});
test('B_process_restart', async t => {
  const first=await fixture(t), second=await fixture(t); const input=job(); send(first.worker,input); const original=await first.record(isResult());
  await first.worker.close({drain:false});
  send(second.worker,{...input,execution_generation:2}); const replacement=await second.record(isResult(2));
  assert.equal(original.payload.execution_generation,1); assert.equal(replacement.payload.execution_generation,2);
  assert.equal(second.records.some(isResult(1)),false);
});
test('B_wall_memory_failure', async t => {
  const fixtureState=await fixture(t); const input=job({max_nodes:999999,constraints:{max_wall_time_ms:30,max_memory_mb:512}});
  send(fixtureState.worker,input); const failure=await fixtureState.record(isStatus('failed'));
  assert.equal(failure.payload.reason,'execution_timeout'); assert.equal(failure.payload.retryable,true);
  const originalPid=fixtureState.diagnostics.find(e=>e.event==='ready').pid; await fixtureState.event(e=>e.event==='ready'&&e.pid!==originalPid);
  send(fixtureState.worker,job({job_id:'too-small',constraints:{max_wall_time_ms:100,max_memory_mb:256}}));
  assert.equal(fixtureState.records.at(-1).payload.reason,'memory_budget_unsupported'); assert.equal(fixtureState.records.at(-1).payload.retryable,false);
});
test('B_slow_consumer', async () => {
  const worker=new EventEmitter(); worker.close=async()=>{}; worker.receive=()=>{};
  const input=new PassThrough(); let maximum=0;
  const output=new Writable({highWaterMark:1,write(_chunk,_encoding,_callback){}});
  const protocol=serveProtocol(worker,{input,output,outputTimeoutMs:20});
  worker.emit('record',{payload:'x'.repeat(900000)}); maximum=Math.max(maximum,output.writableLength);
  worker.emit('record',{payload:'x'.repeat(900000)}); maximum=Math.max(maximum,output.writableLength);
  worker.emit('record',{payload:'x'.repeat(900000)});
  const result=await protocol.done; assert.equal(result.success,false); assert.ok(maximum<=MAX_QUEUE_BYTES);
});
test('B_clean_shutdown', async t => {
  const fixtureState=await fixture(t); const input=job({max_nodes:999999}); send(fixtureState.worker,input); await fixtureState.record(isStatus('running'));
  const pid=fixtureState.worker.child.pid; await fixtureState.worker.close({drain:false});
  assert.throws(()=>process.kill(pid,0),{code:'ESRCH'}); assert.equal(fixtureState.worker.closed,true);
});
test('invalid or unsupported future jobs cannot advance authority; reentrant cancellation fences dispatch', async t => {
  const fixtureState=await fixture(t); const input=job({max_nodes:999999}); send(fixtureState.worker,input); await fixtureState.record(isStatus('running'));
  send(fixtureState.worker,{...input,execution_generation:100,fen:'invalid'});
  assert.equal(fixtureState.worker.active.record.identity.execution_generation,1);
  cancel(fixtureState.worker,cancellation(input)); await fixtureState.record(isAck('terminated'));
  await fixtureState.event(e=>e.event==='ready'&&e.pid!==fixtureState.diagnostics[0].pid);
  const next=job({job_id:'reentrant'});
  fixtureState.worker.on('record', record=>{if(isStatus('pending')(record)&&record.payload.job_id===next.job_id) cancel(fixtureState.worker,cancellation(next,{cancellation_id:'reentrant-cancel'}));});
  send(fixtureState.worker,next); await fixtureState.record(r=>isAck('terminated')(r)&&r.payload.job_id===next.job_id);
  assert.equal(fixtureState.diagnostics.some(e=>e.event==='execution_started'&&e.job_id===next.job_id),false);
});
test('receipt count and serialized reservation exhaustion reject without eviction or authority change', async t => {
  const fixtureState=await fixture(t,{recordLimit:1}); const input=job(); send(fixtureState.worker,input); const original=await fixtureState.record(isResult());
  send(fixtureState.worker,job({job_id:'excess'})); assert.equal(fixtureState.records.at(-1).payload.reason,'session_capacity_exhausted');
  send(fixtureState.worker,input); assert.deepEqual(fixtureState.records.filter(isResult()),[original,original]);
  const bounded=await fixture(t,{retentionLimitBytes:65536}); send(bounded.worker,input);
  assert.equal(bounded.records.at(-1).payload.reason,'session_capacity_exhausted'); assert.equal(bounded.worker.logicalRequests.size,0);
});

test('unexpected engine exit is retryable and recovery never reuses its child', async t=>{
  const fixtureState=await fixture(t);const input=job({max_nodes:666});const old=fixtureState.worker.child.pid;
  send(fixtureState.worker,input);const failure=await fixtureState.record(isStatus('failed'));
  assert.equal(failure.payload.reason,'worker_crashed');assert.equal(failure.payload.retryable,true);
  await fixtureState.event(e=>e.event==='ready'&&e.pid!==old);
  send(fixtureState.worker,input);assert.equal(fixtureState.records.at(-1).payload.reason,'worker_crashed');
});
test('unconfirmed owned termination rejects shutdown without a termination acknowledgement',async t=>{
  const fixtureState=await fixture(t,{terminationTimeoutMs:30});const input=job({max_nodes:999999});
  send(fixtureState.worker,input);await fixtureState.record(isStatus('running'));
  const child=fixtureState.worker.child,originalKill=child.kill.bind(child);child.kill=()=>false;
  cancel(fixtureState.worker,cancellation(input));
  await assert.rejects(fixtureState.worker.close({drain:false}),{code:'termination_unconfirmed'});
  assert.equal(fixtureState.records.some(isAck('terminated')),false);
  child.kill=originalKill;originalKill('SIGKILL');await once(child,'exit');
  // The deliberate failed close is preserved; cleanup has now independently proved exit.
  fixtureState.worker.closePromise=Promise.resolve();
});
test('pre-job cancellation capacity exhaustion does not create authority or stop unrelated work',async t=>{
  const fixtureState=await fixture(t,{retentionLimitBytes:65536});
  rejectsWithoutExecutionFailure(fixtureState, () => cancel(fixtureState.worker,cancellation(job())), 'session_capacity_exhausted');
  assert.equal(fixtureState.worker.logicalRequests.size,0);assert.equal(fixtureState.worker.executions.size,0);
});

test('subscriber mutations cannot rewrite completed receipts or terminal statuses',async t=>{
  const fixtureState=await fixture(t);const input=job();send(fixtureState.worker,input);const original=await fixtureState.record(isResult());
  const timestamp=original.payload.provenance.started_at;
  original.payload.provenance.started_at='caller-mutated';
  const terminal=fixtureState.records.find(isStatus('completed'));terminal.payload.status='running';
  send(fixtureState.worker,input);const replay=fixtureState.records.filter(isResult()).at(-1);
  assert.equal(replay.payload.provenance.started_at,timestamp);
  assert.equal(fixtureState.records.at(-1).payload.status,'completed');
});


test('shutdown during asynchronous resource inspection cannot spawn a late child',async()=>{
  let spawned=0;
  let inspected, releaseInspection;
  const inspectionStarted=new Promise(resolve=>{inspected=resolve;});
  const inspectionPending=new Promise(resolve=>{releaseInspection=resolve;});
  const worker=new StockfishWorker({resourceInspector:async()=>{inspected();await inspectionPending;return resources;},childFactory:()=>{spawned++;throw Error('late child');}});
  const startup=worker.start();await inspectionStarted;await worker.close({drain:false});releaseInspection();
  await assert.rejects(startup,{code:'worker_shutting_down'});assert.equal(spawned,0);
});

test('B_start_reentrant_inspection_and_factory_share_initial_authority', { timeout: 10000 }, async t => {
  let inspectionStart, factoryStart, factoryWarm;
  const state = trackedWorker(t, {
    resourceInspector: async () => { inspectionStart = state.worker.start(); return resources; },
    childFactory: () => { factoryStart = state.worker.start(); factoryWarm = state.worker.prewarm(); return fakeChild(); },
  });
  const initial = state.worker.start(); await initial; await factoryWarm;
  assert.equal(inspectionStart, initial); assert.equal(factoryStart, initial);
  assert.equal(state.children.length, 1); assert.equal(state.worker.initialized, true);
});

test('B_start_during_initial_child_warmup_cannot_resurrect_shutdown', { timeout: 10000 }, async t => {
  let announceSpawn;
  const spawned = new Promise(resolve => { announceSpawn = resolve; });
  const state = trackedWorker(t, { childFactory: () => { const child = fakeChild(100); announceSpawn(child); return child; } });
  const initial = state.worker.start(); const observedFailure = assert.rejects(initial, { code: 'engine_loading_failed' });
  const child = await spawned;
  assert.equal(state.worker.start(), initial);
  await state.worker.close({ drain: false }); await observedFailure;
  assert.equal(state.children.length, 1); assert.equal(state.worker.closed, true);
  assert.throws(() => process.kill(child.pid, 0), { code: 'ESRCH' });
});

test('B_repeated_start_preserves_active_child', async t => {
  const state = await fixture(t);
  const input = job({ max_nodes: 999999 });
  send(state.worker, input); await state.record(isStatus('running'));
  const active = state.worker.active, child = state.worker.child;
  assert.doesNotThrow(() => process.kill(child.pid, 0));
  await state.worker.start();
  assert.equal(state.children.length, 1, 'repeated startup spawned a second owned engine');
  assert.equal(state.worker.child, child); assert.equal(state.worker.active, active);
  assert.deepEqual(active.record.job, input);
  // Even the internal helper must never replace an initialized busy child.
  await state.worker.prewarm(); assert.equal(state.children.length, 1);
  cancel(state.worker, cancellation(input)); await state.record(isAck('terminated'));
  assert.throws(() => process.kill(child.pid, 0), { code: 'ESRCH' });
  await state.event(event => event.event === 'ready' && event.pid !== child.pid);
  const next = job({ job_id: 'after-repeated-start' }); send(state.worker, next);
  await state.record(record => isStatus('completed')(record) && record.payload.job_id === next.job_id);
  assert.equal(state.records.filter(record => isResult()(record) && record.payload.job_id === next.job_id).length, 1);
  await state.worker.close({ drain: false });
  for (const owned of state.children) assert.throws(() => process.kill(owned.pid, 0), { code: 'ESRCH' });
});

test('B_concurrent_initial_start_and_idle_reentry', { timeout: 10000 }, async t => {
  let inspections = 0;
  const state = trackedWorker(t, { resourceInspector: async () => { inspections++; return resources; }, childFactory: () => fakeChild(30) });
  const first = state.worker.start(), second = state.worker.start();
  assert.equal(first, second, 'initial callers must share the complete startup operation');
  const [one, two] = await Promise.all([first, second]); assert.deepEqual(one, two);
  await state.worker.start();
  assert.equal(inspections, 1); assert.equal(state.children.length, 1);
  assert.equal(state.diagnostics.filter(event => event.event === 'ready').length, 1);
});

test('B_initial_start_failure_is_shared_and_cannot_retry_after_close', async t => {
  let inspections = 0;
  const state = trackedWorker(t, { resourceInspector: async () => {
    inspections++; throw Object.assign(new Error('fixture resource failure'), { code: 'fixture_resource_failure' });
  } });
  const first = state.worker.start(), second = state.worker.start(); assert.equal(first, second);
  await Promise.all([assert.rejects(first, { code: 'fixture_resource_failure' }), assert.rejects(second, { code: 'fixture_resource_failure' })]);
  assert.equal(inspections, 1); assert.equal(state.children.length, 0); assert.equal(state.worker.closed, true);
  await assert.rejects(state.worker.start(), { code: 'worker_shutting_down' });
});

test('B_start_during_cancellation_and_replacement', { timeout: 10000 }, async t => {
  let replacementSpawned;
  const replacement = new Promise(resolve => { replacementSpawned = resolve; });
  let count = 0;
  const state = await fixture(t, { childFactory: () => {
    const child = fakeChild(count++ ? 100 : 0);
    if (count === 2) replacementSpawned(child);
    return child;
  } });
  const input = job({ max_nodes: 999999 }); send(state.worker, input); await state.record(isStatus('running'));
  const active = state.worker.active, original = state.worker.child;
  cancel(state.worker, cancellation(input));
  const startup = state.worker.start();
  assert.equal(state.worker.active, active); assert.equal(state.worker.child, original);
  await startup; await state.record(isAck('terminated'));
  const warming = await replacement;
  assert.equal(state.worker.childReady, false);
  await state.worker.start();
  assert.equal(state.worker.child, warming); assert.equal(state.children.length, 2);
  await state.event(event => event.event === 'ready' && event.pid === warming.pid);
  assert.equal(state.diagnostics.filter(event => event.event === 'ready').length, 2);
});

test('B_start_during_shutdown_and_after_closed', async t => {
  const state = await fixture(t);
  const closing = state.worker.close({ drain: false });
  await assert.rejects(state.worker.start(), { code: 'worker_shutting_down' });
  await closing;
  await assert.rejects(state.worker.start(), { code: 'worker_shutting_down' });
  assert.equal(state.children.length, 1);
});

function rejectsWithoutExecutionFailure(state, action, reason) {
  const before = state.records.length;
  let rejection;
  try { action(); } catch (error) { rejection = error; }
  const emitted = state.records.slice(before);
  assert.equal(emitted.some(record => record.model === 'JobStatus' && record.payload.status === 'failed'), false,
    'command refusal emitted a false terminal execution status: ' + JSON.stringify(emitted));
  assert.equal(rejection?.name, 'CommandRejection'); assert.equal(rejection.code, reason);
  assert.equal(emitted.length, 0, 'refused commands must not fabricate receipts');
}

test('B_conflicting_duplicate_does_not_fail_active_execution', async t => {
  const state = await fixture(t), input = job({ max_nodes: 999999 });
  send(state.worker, input); await state.record(isStatus('running'));
  const active = state.worker.active;
  rejectsWithoutExecutionFailure(state, () => send(state.worker, job({ max_nodes: 999999, max_depth: 5 })), 'identity_payload_conflict');
  assert.equal(state.worker.active, active); assert.deepEqual(active.record.job, input);
  assert.equal(active.record.status.status, 'running');
  cancel(state.worker, cancellation(input)); await state.record(isAck('terminated'));
});

test('B_conflicting_duplicate_preserves_completed_receipt', async t => {
  const state = await fixture(t), input = job(); send(state.worker, input);
  await state.record(isStatus('completed'));
  const record = [...state.worker.executions.values()][0], bytes = JSON.stringify(record.result);
  rejectsWithoutExecutionFailure(state, () => send(state.worker, job({ max_depth: 5 })), 'identity_payload_conflict');
  assert.equal(JSON.stringify(record.result), bytes); assert.equal(record.status.status, 'completed');
  send(state.worker, input);
  assert.equal(JSON.stringify(state.records.filter(isResult()).at(-1).payload), bytes);
});

test('B_cancellation_identity_conflict_does_not_fail_target_execution', async t => {
  const state = await fixture(t), input = job({ max_nodes: 999999 });
  cancel(state.worker, cancellation(job({ job_id: 'future-target' }), { cancellation_id: 'bound-id' }));
  send(state.worker, input); await state.record(isStatus('running'));
  const active = state.worker.active;
  rejectsWithoutExecutionFailure(state, () => cancel(state.worker, cancellation(input, { cancellation_id: 'bound-id' })), 'cancellation_id_conflict');
  assert.equal(state.worker.active, active); assert.equal(active.stopping, null);
  cancel(state.worker, cancellation(input)); await state.record(isAck('terminated'));
});

test('B_cancellation_capacity_refusal_is_not_execution_failure', async t => {
  const state = await fixture(t), input = job({ max_nodes: 999999 });
  send(state.worker, input); await state.record(isStatus('running'));
  const record = state.worker.active.record;
  for (let index = 0; index < 64; index++) cancel(state.worker, cancellation(input, { cancellation_id: 'retained-' + index }));
  rejectsWithoutExecutionFailure(state, () => cancel(state.worker, cancellation(input, { cancellation_id: 'unretained' })), 'cancellation_capacity_exhausted');
  assert.equal(record.cancellations.size, 64); assert.equal(record.cancellations.has('unretained'), false);
  await state.record(value => isAck('terminated')(value) && value.payload.cancellation_id === 'retained-63');
  assert.equal(state.records.some(value => value.model === 'CancellationAcknowledgement' && value.payload.cancellation_id === 'unretained'), false);
});

test('B_invalid_commands_and_shutdown_cannot_fail_accepted_receipts', async t => {
  const state = await fixture(t), input = job({ max_nodes: 999999 });
  send(state.worker, input); await state.record(isStatus('running'));
  rejectsWithoutExecutionFailure(state, () => send(state.worker, { ...input, fen: 42 }), 'invalid_request');
  rejectsWithoutExecutionFailure(state, () => cancel(state.worker, { ...cancellation(input), extra: true }), 'invalid_request');
  state.worker.retentionLimitBytes = state.worker.retainedBytes;
  rejectsWithoutExecutionFailure(state, () => cancel(state.worker, cancellation(input)), 'session_capacity_exhausted');
  assert.equal(state.worker.active.stopping, null);
  const closing = state.worker.close({ drain: false });
  rejectsWithoutExecutionFailure(state, () => send(state.worker, input), 'worker_shutting_down');
  await closing;
});

test('B_cli_command_rejection_fails_closed_without_false_receipts', async t => {
  const state = await fixture(t), input = new PassThrough(), output = new PassThrough();
  const lines = [], diagnostics = [];
  output.on('data', chunk => lines.push(...chunk.toString().trim().split('\n').filter(Boolean).map(JSON.parse)));
  const protocol = serveProtocol(state.worker, { input, output, diagnostic: event => diagnostics.push(event) });
  const original = job({ max_nodes: 999999 });
  input.write(JSON.stringify({ model: 'StockfishJob', payload: original }) + '\n');
  await state.record(isStatus('running'));
  input.write(JSON.stringify({ model: 'StockfishJob', payload: job({ max_nodes: 999999, max_depth: 5 }) }) + '\n');
  const outcome = await Promise.race([protocol.done, new Promise((_, reject) => { const timer = setTimeout(() => reject(new Error('rejection transport hung')), 3000); timer.unref(); })]);
  assert.deepEqual(outcome, { success: false, reason: 'identity_payload_conflict' });
  assert.equal(lines.some(value => value.model === 'JobStatus' && value.payload.status === 'failed'), false);
  assert.equal(lines.some(value => value.model === 'CancellationAcknowledgement'), false);
  assert.deepEqual(diagnostics, [{ event: 'protocol_error', reason: 'identity_payload_conflict' }]);
  assert.equal(state.worker.closed, true);
  for (const child of state.children) assert.throws(() => process.kill(child.pid, 0), { code: 'ESRCH' });
});
