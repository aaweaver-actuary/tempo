import test from 'node:test';
import assert from 'node:assert/strict';
import {fork} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createCancellableMaia} from '../../scripts/cancellable-maia.mjs';
import {createStockfishProcess} from '../../scripts/stockfish-process.mjs';
import {createEngineSearch} from '../../scripts/engine-search.mjs';
import {EventEmitter} from 'node:events';
import {createMaiaRelease} from '../../scripts/maia-coverage-client.mjs';
const fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

function fakeMaia({exitDelay=0, missingExit=false, killFails=false, killThrows=false}={}) {
  const child=new EventEmitter(); child.alive=true;
  child.started=new Promise(resolve=>{child.send=(message,callback)=>{child.request=message;callback();resolve();};});
  child.kill=()=>{
    if(killThrows)throw new Error('kill unavailable');
    if(killFails)return false;
    child.alive=false;
    if(!missingExit)setTimeout(()=>child.emit('exit',null,'SIGKILL'),exitDelay);
    return true;
  };
  child.reply=moves=>child.emit('message',{id:child.request.id,moves});
  return child;
}
const observe=promise=>promise.then(value=>({value}),error=>({error}));
const fakeOwner=child=>createCancellableMaia({launch:()=>child,isProcessAlive:owned=>owned.alive});

test('C1 normal Maia completion preserves the requested model rating and moves',async()=>{
  const child=fakeMaia(),inference=fakeOwner(child);
  const pending=inference.analyze(fen,1750,async()=>true);await child.started;
  child.reply([{move_uci:'e2e4',probability:1}]);
  assert.deepEqual(await pending,[{move_uci:'e2e4',probability:1}]);
  assert.equal(child.request.elo,1750);assert.equal(child.request.fen,fen);inference.close();
});

for(const [name,configuration]of[['C2 ordinary exit',{}],['C3 delayed exit',{exitDelay:35}],['C4 missing exit',{missingExit:true}]]) {
  test(`${name} settles Maia preemption after actual termination`,async()=>{
    const child=fakeMaia(configuration),inference=fakeOwner(child);
    const pending=observe(inference.analyze(fen,1500,async()=>true));await child.started;
    const start=performance.now();inference.close();
    assert.equal((await pending).error.code,'STUDY_PREEMPTED');
    assert(performance.now()-start<250);assert.equal(child.alive,false);
    assert.equal(inference.terminationVerified(),true);
  });
}

for(const [name,configuration]of[['C5 failed kill',{killFails:true}],['C6 throwing kill',{killThrows:true}]]) {
  test(`${name} bounds the caller and quarantines an unverified child`,async()=>{
    const child=fakeMaia(configuration),inference=fakeOwner(child);
    const pending=observe(inference.analyze(fen,1500,async()=>true));await child.started;inference.close();
    assert.equal((await pending).error.code,'MAIA_TERMINATION_UNVERIFIED');
    await assert.rejects(inference.analyze(fen,1500,async()=>true),{code:'MAIA_TERMINATION_UNVERIFIED'});
    assert.equal(inference.terminationVerified(),false);
    child.alive=false;assert.equal(inference.terminationVerified(),true);
  });
}

test('C7 late Maia result after preemption is fenced and retry launches a fresh owner',async()=>{
  const first=fakeMaia({missingExit:true}),second=fakeMaia();let launches=0;
  const inference=createCancellableMaia({launch:()=>++launches===1?first:second,isProcessAlive:child=>child.alive});
  const cancelled=observe(inference.analyze(fen,1500,async()=>true));await first.started;inference.close();
  first.reply(['obsolete']);assert.equal((await cancelled).error.code,'STUDY_PREEMPTED');
  const retry=inference.analyze(fen,1500,async()=>true);await second.started;
  first.reply(['obsolete']);second.reply(['current']);assert.deepEqual(await retry,['current']);
  assert.equal(launches,2);assert.equal(second.request.elo,1500);inference.close();
});

test('C8 control failure after completed inference denies publication',async()=>{
  const child=fakeMaia(),inference=fakeOwner(child);let checks=0;
  const pending=observe(inference.analyze(fen,1500,async()=>{if(++checks>1)throw new Error('control unavailable');return true;}));
  await child.started;child.reply(['must not publish']);
  assert.equal((await pending).error.code,'STUDY_PREEMPTED');assert.equal(child.alive,false);
});

test('C9 genuine child failure remains a failure after verified termination',async()=>{
  const child=fakeMaia(),inference=fakeOwner(child);
  const pending=observe(inference.analyze(fen,1500,async()=>true));await child.started;
  child.emit('error',new Error('model process failed'));
  assert.match((await pending).error.message,/model process failed/);
});

test('C10 concurrent initial control checks cannot launch two Maia requests',async()=>{
  const child=fakeMaia(),inference=fakeOwner(child);let allow;
  const first=observe(inference.analyze(fen,1500,()=>new Promise(resolve=>{allow=resolve;})));
  await Promise.resolve();await assert.rejects(inference.analyze(fen,1500,async()=>true),/active inference/);
  allow(false);assert.equal((await first).error.code,'STUDY_PREEMPTED');assert.equal(child.request,undefined);
});

test('C11 unavailable control is bounded even without a control notification',async()=>{
  const child=fakeMaia(),inference=fakeOwner(child);let checks=0;
  const pending=observe(inference.analyze(fen,1500,()=>++checks===1?true:new Promise(()=>{})));
  await child.started;child.reply(['obsolete']);
  assert.equal((await pending).error.code,'STUDY_PREEMPTED');assert.equal(child.alive,false);
});

test('C12 failed release is visible and retry preserves one immutable operation identity',async()=>{
  const calls=[];
  const release=createMaiaRelease(async(path,options)=>{calls.push({path,options});if(calls.length===1)throw new Error('release unavailable');return {released:true};},
    {node_id:'node',lease_id:'lease'});
  await assert.rejects(release(),/release unavailable/);assert.deepEqual(await release(),{released:true});
  assert.equal(calls[0].options.operationId,calls[1].options.operationId);
  assert.equal(calls[0].options.body,calls[1].options.body);
  assert.equal(calls[0].path,'/api/repertoire-coverage/maia/release');
});

test('C13 a completed current result cannot be killed by an older concurrent control probe',async()=>{
  const child=fakeMaia(),inference=fakeOwner(child);let checks=0,finishProbe,startedProbe;
  const probing=new Promise(resolve=>{startedProbe=resolve;});
  const pending=inference.analyze(fen,1500,()=>{
    if(++checks===2)return new Promise(resolve=>{finishProbe=resolve;startedProbe();});
    return true;
  });
  await child.started;await probing;child.reply(['current']);assert.deepEqual(await pending,['current']);
  finishProbe(false);await new Promise(resolve=>setImmediate(resolve));
  assert.equal(child.alive,true);inference.close();
});

test('C14 an idle child exit launches a fresh Maia process for the next job',async()=>{
  const first=fakeMaia(),second=fakeMaia();let launches=0,retry;
  const inference=createCancellableMaia({launch:()=>++launches===1?first:second,isProcessAlive:child=>child.alive});
  try {
    const completed=inference.analyze(fen,1500,async()=>true);await first.started;first.reply(['first']);await completed;
    first.alive=false;first.emit('exit',1,null);
    retry=observe(inference.analyze(fen,1500,async()=>true));
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(launches,2,'An exited idle process must never receive a new job');
    second.reply(['second']);assert.deepEqual((await retry).value,['second']);
  } finally {inference.close();await retry;}
});

test('C15 a late IPC failure cannot replace the original Maia stop reason',async()=>{
  for(const originalFailure of [undefined,new Error('original model process failure')]) {
    const child=fakeMaia({missingExit:true}),inference=fakeOwner(child);
    const pending=observe(inference.analyze(fen,1500,async()=>true));await child.started;
    if(originalFailure)child.emit('error',originalFailure);else inference.close();
    child.emit('error',new Error('late IPC disconnect during termination'));
    const {error}=await pending;
    if(originalFailure)assert.equal(error,originalFailure);
    else assert.equal(error.code,'STUDY_PREEMPTED');
    assert.equal(inference.terminationVerified(),true);
  }
});

test('real Maia inference yields to study and resumes the same full-quality legal distribution', {timeout:60_000}, async()=>{
  let priority=false, startedAt, child;
  const inference=createCancellableMaia({launch:()=>{
    child=fork(fileURLToPath(new URL('../../scripts/maia-inference-child.mjs',import.meta.url)),[],{execArgv:[],stdio:['ignore','inherit','inherit','ipc']});
    child.on('message',message=>{if(message.started && startedAt===undefined){startedAt=performance.now();priority=true;}});
    return child;
  }});
  try {
    const cancelled=await inference.analyze(fen,1500,async()=>!priority).catch(error=>error);
    assert.equal(cancelled.code,'STUDY_PREEMPTED');
    assert.notEqual(startedAt,undefined);
    const yieldMs=performance.now()-startedAt;
    assert(yieldMs<=250,`Maia yield ${yieldMs} ms`);
    assert.equal(child.signalCode,'SIGKILL');
    priority=false;
    const moves=await inference.analyze(fen,1500,async()=>!priority);
    assert.equal(moves.length,20);
    assert(Math.abs(moves.reduce((total,move)=>total+move.probability,0)-1)<.0001);
    console.log(JSON.stringify({case:'real-maia-priority',yield_ms:yieldMs,legal_moves:moves.length}));
  } finally {inference.close();}
});

test('real Stockfish search stops for study and a quiet search still reaches its requested depth', {timeout:30_000}, async()=>{
  const engine=await createStockfishProcess();
  let priority=false,startedAt;
  const searches=createEngineSearch(engine,async()=>({active:priority,available:true,study_only_mode:false}));
  const job={request:{position_start_fen:fen,position_prefix_uci:[],depth:99,multipv:1}};
  try {
    engine.uci('uci');engine.uci('setoption name Threads value 1');engine.uci('setoption name Hash value 32');engine.uci('isready');
    const cancelled=searches.evaluate(job,'engine_game').catch(error=>error);
    const listener=engine.listen;
    engine.listen=text=>{listener(text);if(text.startsWith('info ') && text.includes(' pv ') && startedAt===undefined){startedAt=performance.now();priority=true;}};
    const error=await cancelled;
    assert.equal(error.diagnostics.outcome,'preempted');assert.notEqual(startedAt,undefined);
    const yieldMs=performance.now()-startedAt;assert(yieldMs<=250,`Stockfish yield ${yieldMs} ms`);
    assert.equal(searches.fatalEngineError,false);
    priority=false;
    const result=await searches.evaluate({...job,request:{...job.request,depth:4}},'engine_game');
    assert(result.report.complete && result.report.lines.every(line=>line.depth>=4));
    console.log(JSON.stringify({case:'real-stockfish-priority',yield_ms:yieldMs,depth:4}));
  } finally {engine.terminate();}
});

for (const kind of ['maia','stockfish']) {
  test(`C18 ${kind} verified preemption acknowledges a delayed input marker while contact remains held`, {timeout:30_000}, async()=>{
    const {createInferenceYieldReceipt}=await import('../../scripts/study-priority-qualification/inference-yield.mjs');
    let held=false, inputAt, marker, engine, inference, launches=0;
    const startedAt=Date.now();
    try {
      let proof;
      if(kind==='maia'){
        inference=createCancellableMaia({launch:()=>{
          launches++;
          const child=fork(fileURLToPath(new URL('../fixtures/study-priority/busy-maia.mjs',import.meta.url)),[],{execArgv:[],stdio:['ignore','ignore','inherit','ipc']});
          child.once('message',()=>{inputAt=Date.now();held=true;});
          return child;
        }});
        const error=await inference.analyze(fen,1500,async()=>!held).catch(error=>error);
        assert.equal(error.code,'STUDY_PREEMPTED');assert.equal(inference.terminationVerified(),true);
        proof={verified:true,kind:'maia-termination-verified'};
      }else{
        launches++;engine=await createStockfishProcess();
        engine.uci('uci');engine.uci('setoption name Threads value 1');engine.uci('setoption name Hash value 32');engine.uci('isready');
        const searches=createEngineSearch(engine,async()=>({active:held,available:true,study_only_mode:false}));
        const pending=searches.evaluate({request:{position_start_fen:fen,position_prefix_uci:[],depth:99,multipv:1}},'engine_game').catch(error=>error);
        const listener=engine.listen;
        engine.listen=text=>{listener(text);if(inputAt===undefined&&text.startsWith('info ')&&text.includes(' pv ')){inputAt=Date.now();held=true;}};
        assert.equal((await pending).diagnostics.outcome,'preempted');assert.equal(searches.fatalEngineError,false);
        proof={verified:true,kind:'stockfish-protocol-stop'};
      }
      const stoppedAt=Date.now();assert.notEqual(inputAt,undefined);assert(stoppedAt-inputAt<=250);
      const receipts=[];let logicalTime=0;
      const receipt=createInferenceYieldReceipt({kind,readInteraction:async()=>marker,publish:async value=>receipts.push(value)});
      receipt.record({generation:1,startedAt,stoppedAt,outcome:'preempted',proof});
      assert.equal(await receipt.confirm(),false,'The marker is still delayed after verified preemption');
      const admitted=await receipt.waitForTurn({allowed:async()=>!held,now:()=>logicalTime,deadline:50,pause:async()=>{
        logicalTime+=25;marker={started:true,at:inputAt,generations:{[kind]:1}};
      }});
      assert.equal(admitted,false,'Acknowledge the stopped generation without another optional admission');
      assert.equal(held,true);assert.equal(launches,1);assert.equal(receipts.length,1);
      assert.equal(receipts[0].at,stoppedAt,'Receipt delivery latency cannot replace actual verified stop time');
      assert.equal(receipts[0].generation,1);assert.deepEqual(receipts[0].proof,proof);
      assert.equal(await receipt.confirm(),true);assert.equal(receipts.length,1);
    }finally{inference?.close();engine?.terminate();}
  });
}

test('C19 yield receipt rejects pre-input cancellation, stale generation and unverified running false during unavailable control',async()=>{
  const {createInferenceYieldReceipt}=await import('../../scripts/study-priority-qualification/inference-yield.mjs');
  let marker={started:true,at:21,generations:{maia:1}},published=0,logicalTime=0;
  const receipt=createInferenceYieldReceipt({kind:'maia',readInteraction:async()=>marker,publish:async()=>published++});
  assert.throws(()=>receipt.record({generation:1,startedAt:1,stoppedAt:20,proof:{verified:false}}),/not verified cancellation/);
  receipt.record({generation:1,startedAt:1,stoppedAt:20,proof:{verified:true,kind:'maia-termination-verified'}});
  assert.equal(await receipt.confirm(),false,'A genuine pre-input control deferral must stay deferred');
  marker={started:true,at:10,generations:{maia:2}};assert.equal(await receipt.confirm(),false,'Old generations cannot acknowledge new input');
  marker=undefined;
  await assert.rejects(receipt.waitForTurn({allowed:async()=>false,deadline:50,now:()=>logicalTime,pause:async()=>{logicalTime+=25;}}),/bounded rehearsal/);
  assert.equal(published,0);
});
