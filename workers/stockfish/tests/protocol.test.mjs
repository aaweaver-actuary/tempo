import test from 'node:test';
import assert from 'node:assert/strict';
import { once, EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import { resources, fixture, job, cancellation, send, cancel, isResult, isStatus, isAck, waitFor } from './helpers.mjs';
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
  send(fixtureState.worker,job({max_depth:5})); assert.equal(fixtureState.records.at(-1).payload.reason,'identity_payload_conflict');
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
  send(fixtureState.worker,input); assert.equal(fixtureState.records.at(-1).payload.reason,'stale_generation');
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
  cancel(fixtureState.worker,cancellation(input,{cancellation_id:'excess'}));
  assert.equal(fixtureState.records.at(-1).payload.reason,'cancellation_capacity_exhausted');
  await fixtureState.record(r=>isAck('terminated')(r)&&r.payload.cancellation_id==='id-63');
  assert.equal(new Set(fixtureState.records.filter(isAck('terminated')).map(r=>r.payload.cancellation_id)).size,64);
  cancel(fixtureState.worker,cancellation(job({job_id:'other'}),{cancellation_id:'id-0'}));
  assert.equal(fixtureState.records.at(-1).payload.reason,'cancellation_id_conflict');
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
  cancel(fixtureState.worker,cancellation(job()));
  assert.equal(fixtureState.records.at(-1).payload.reason,'session_capacity_exhausted');
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
  const worker=new StockfishWorker({resourceInspector:async()=>{await new Promise(resolve=>setTimeout(resolve,20));return resources;},childFactory:()=>{spawned++;throw Error('late child');}});
  const startup=worker.start();await worker.close({drain:false});
  await assert.rejects(startup,{code:'worker_shutting_down'});assert.equal(spawned,0);
});
