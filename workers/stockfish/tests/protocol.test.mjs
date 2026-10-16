import test from 'node:test';
import assert from 'node:assert/strict';
import { once, EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import { fixture, job, cancellation, send, cancel, isResult, isStatus, isAck, waitFor } from './helpers.mjs';
import { serveProtocol, MAX_QUEUE_BYTES } from '../cli.mjs';

test('B_exact_replay', async t => {
  const f = await fixture(t); const input = job(); send(f.worker, input); const result = await f.record(isResult());
  send(f.worker, input); assert.deepEqual(f.records.filter(isResult()), [result, result]);
  assert.equal(f.diagnostics.filter(e => e.event === 'execution_started').length, 1);
  const other = job({workspace_id:'another-workspace'}); send(f.worker, other);
  await f.record(r=>r.model==='StockfishResult'&&r.payload.workspace_id===other.workspace_id);
});
test('B_payload_conflict', async t => {
  const f=await fixture(t); const input=job(); send(f.worker,input); await f.record(isResult());
  send(f.worker,job({max_depth:5})); assert.equal(f.records.at(-1).payload.reason,'identity_payload_conflict');
  send(f.worker,{...job({job_id:'invalid'}), input_digest:'bad'}); assert.equal(f.records.at(-1).payload.reason,'input_digest_mismatch');
  assert.equal(f.worker.logicalRequests.size,1);
});
test('B_stale_generation', async t => {
  const f=await fixture(t); const input=job({max_nodes:999999}); send(f.worker,input); await f.record(isStatus('running'));
  const active=f.worker.active, oldChild=f.worker.child;
  send(f.worker,{...input,execution_generation:2});
  // A fabricated late callback with the old token cannot settle the successor.
  f.worker.handleChildMessage(oldChild,{type:'result',token:active.token,variations:[]});
  await f.event(e=>e.event==='ready'&&e.pid!==oldChild.pid); await f.record(isStatus('running',2));
  assert.equal(f.records.some(isResult(1)),false);
  send(f.worker,input); assert.equal(f.records.at(-1).payload.reason,'stale_generation');
  cancel(f.worker,cancellation({...input,execution_generation:2})); await f.record(isAck('terminated',2));
});
test('B_cancel_before_job', async t => {
  const f=await fixture(t); const input=job({max_nodes:999999}); send(f.worker,input); await f.record(isStatus('running'));
  const future={...input,execution_generation:3}; cancel(f.worker,cancellation(future));
  assert.equal(f.worker.active.record.job.execution_generation,1);
  send(f.worker,job({job_id:'unrelated'})); assert.equal(f.records.at(-1).payload.reason,'worker_busy');
  send(f.worker,future); await f.record(isAck('terminated',3));
  assert.equal(f.records.some(isResult(3)),false);
});
test('B_cancel_during_search', async t => {
  const f=await fixture(t); const input=job({max_nodes:999999}); send(f.worker,input); await f.record(isStatus('running'));
  const pid=f.worker.child.pid; cancel(f.worker,cancellation(input)); await f.record(isAck('terminated'));
  assert.throws(()=>process.kill(pid,0),{code:'ESRCH'});
  await f.event(e=>e.event==='ready'&&e.pid!==pid);
  send(f.worker,job({job_id:'capacity-recovered'})); await f.record(r=>r.model==='StockfishResult'&&r.payload.job_id==='capacity-recovered');
});
test('B_cancel_complete_race', async t => {
  const f=await fixture(t); const input=job(); send(f.worker,input); await f.record(isResult());
  cancel(f.worker,cancellation(input)); await f.record(isAck('already_completed'));
  const later=job({job_id:'cancel-wins',max_nodes:2}); send(f.worker,later);
  await f.record(r=>isStatus('running')(r)&&r.payload.job_id===later.job_id);
  cancel(f.worker,cancellation(later,{cancellation_id:'second'}));
  await f.record(r=>isAck('terminated')(r)&&r.payload.job_id===later.job_id);
  assert.equal(f.records.some(r=>r.model==='StockfishResult'&&r.payload.job_id===later.job_id),false);
});
test('B_multiple_cancellation_ids', async t => {
  const f=await fixture(t); const input=job({max_nodes:999999}); send(f.worker,input); await f.record(isStatus('running'));
  for(let i=0;i<64;i++) cancel(f.worker,cancellation(input,{cancellation_id:'id-'+i}));
  cancel(f.worker,cancellation(input,{cancellation_id:'excess'}));
  assert.equal(f.records.at(-1).payload.reason,'cancellation_capacity_exhausted');
  await f.record(r=>isAck('terminated')(r)&&r.payload.cancellation_id==='id-63');
  assert.equal(new Set(f.records.filter(isAck('terminated')).map(r=>r.payload.cancellation_id)).size,64);
  cancel(f.worker,cancellation(job({job_id:'other'}),{cancellation_id:'id-0'}));
  assert.equal(f.records.at(-1).payload.reason,'cancellation_id_conflict');
});
test('B_superseded_queued_work', async t => {
  const f=await fixture(t); const input=job({max_nodes:999999}); send(f.worker,input); await f.record(isStatus('running'));
  send(f.worker,{...input,execution_generation:2});
  cancel(f.worker,cancellation({...input,execution_generation:3}));
  send(f.worker,{...input,execution_generation:3}); await f.record(isAck('terminated',3));
  await f.event(e=>e.event==='ready'&&e.pid!==f.diagnostics.find(e=>e.event==='execution_started').pid);
  assert.equal(f.records.some(isStatus('running',2)),false); assert.equal(f.worker.replacement,null);
  const future=job({job_id:'future'}); cancel(f.worker,cancellation(future,{cancellation_id:'future-a'}));
  cancel(f.worker,cancellation(future,{cancellation_id:'future-b'})); send(f.worker,{...future,execution_generation:2});
  await f.record(r=>isAck('stale_generation')(r)&&r.payload.cancellation_id==='future-b');
});
test('B_process_restart', async t => {
  const first=await fixture(t), second=await fixture(t); const input=job(); send(first.worker,input); const original=await first.record(isResult());
  await first.worker.close({drain:false});
  send(second.worker,{...input,execution_generation:2}); const replacement=await second.record(isResult(2));
  assert.equal(original.payload.execution_generation,1); assert.equal(replacement.payload.execution_generation,2);
  assert.equal(second.records.some(isResult(1)),false);
});
test('B_wall_memory_failure', async t => {
  const f=await fixture(t); const input=job({max_nodes:999999,constraints:{max_wall_time_ms:30,max_memory_mb:512}});
  send(f.worker,input); const failure=await f.record(isStatus('failed'));
  assert.equal(failure.payload.reason,'execution_timeout'); assert.equal(failure.payload.retryable,true);
  const originalPid=f.diagnostics.find(e=>e.event==='ready').pid; await f.event(e=>e.event==='ready'&&e.pid!==originalPid);
  send(f.worker,job({job_id:'too-small',constraints:{max_wall_time_ms:100,max_memory_mb:256}}));
  assert.equal(f.records.at(-1).payload.reason,'memory_budget_unsupported'); assert.equal(f.records.at(-1).payload.retryable,false);
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
  const f=await fixture(t); const input=job({max_nodes:999999}); send(f.worker,input); await f.record(isStatus('running'));
  const pid=f.worker.child.pid; await f.worker.close({drain:false});
  assert.throws(()=>process.kill(pid,0),{code:'ESRCH'}); assert.equal(f.worker.closed,true);
});
test('invalid or unsupported future jobs cannot advance authority; reentrant cancellation fences dispatch', async t => {
  const f=await fixture(t); const input=job({max_nodes:999999}); send(f.worker,input); await f.record(isStatus('running'));
  send(f.worker,{...input,execution_generation:100,fen:'invalid'});
  assert.equal(f.worker.active.record.identity.execution_generation,1);
  cancel(f.worker,cancellation(input)); await f.record(isAck('terminated'));
  await f.event(e=>e.event==='ready'&&e.pid!==f.diagnostics[0].pid);
  const next=job({job_id:'reentrant'});
  f.worker.on('record', record=>{if(isStatus('pending')(record)&&record.payload.job_id===next.job_id) cancel(f.worker,cancellation(next,{cancellation_id:'reentrant-cancel'}));});
  send(f.worker,next); await f.record(r=>isAck('terminated')(r)&&r.payload.job_id===next.job_id);
  assert.equal(f.diagnostics.some(e=>e.event==='execution_started'&&e.job_id===next.job_id),false);
});
test('receipt count and serialized reservation exhaustion reject without eviction or authority change', async t => {
  const f=await fixture(t,{recordLimit:1}); const input=job(); send(f.worker,input); const original=await f.record(isResult());
  send(f.worker,job({job_id:'excess'})); assert.equal(f.records.at(-1).payload.reason,'session_capacity_exhausted');
  send(f.worker,input); assert.deepEqual(f.records.filter(isResult()),[original,original]);
  const bounded=await fixture(t,{retentionLimitBytes:65536}); send(bounded.worker,input);
  assert.equal(bounded.records.at(-1).payload.reason,'session_capacity_exhausted'); assert.equal(bounded.worker.logicalRequests.size,0);
});

test('unexpected engine exit is retryable and recovery never reuses its child', async t=>{
  const f=await fixture(t);const input=job({max_nodes:666});const old=f.worker.child.pid;
  send(f.worker,input);const failure=await f.record(isStatus('failed'));
  assert.equal(failure.payload.reason,'worker_crashed');assert.equal(failure.payload.retryable,true);
  await f.event(e=>e.event==='ready'&&e.pid!==old);
  send(f.worker,input);assert.equal(f.records.at(-1).payload.reason,'worker_crashed');
});
test('unconfirmed owned termination rejects shutdown without a termination acknowledgement',async t=>{
  const f=await fixture(t,{terminationTimeoutMs:30});const input=job({max_nodes:999999});
  send(f.worker,input);await f.record(isStatus('running'));
  const child=f.worker.child,originalKill=child.kill.bind(child);child.kill=()=>false;
  cancel(f.worker,cancellation(input));
  await assert.rejects(f.worker.close({drain:false}),{code:'termination_unconfirmed'});
  assert.equal(f.records.some(isAck('terminated')),false);
  child.kill=originalKill;originalKill('SIGKILL');await once(child,'exit');
  // The deliberate failed close is preserved; cleanup has now independently proved exit.
  f.worker.closePromise=Promise.resolve();
});
test('pre-job cancellation capacity exhaustion does not create authority or stop unrelated work',async t=>{
  const f=await fixture(t,{retentionLimitBytes:65536});
  cancel(f.worker,cancellation(job()));
  assert.equal(f.records.at(-1).payload.reason,'session_capacity_exhausted');
  assert.equal(f.worker.logicalRequests.size,0);assert.equal(f.worker.executions.size,0);
});
