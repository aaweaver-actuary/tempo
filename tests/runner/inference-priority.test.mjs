import test from 'node:test';
import assert from 'node:assert/strict';
import {fork} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createCancellableMaia} from '../../scripts/cancellable-maia.mjs';
import {createStockfishProcess} from '../../scripts/stockfish-process.mjs';
import {createEngineSearch} from '../../scripts/engine-search.mjs';
const fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

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
