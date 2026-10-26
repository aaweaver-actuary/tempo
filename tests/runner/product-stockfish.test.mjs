import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { Readable, Writable, PassThrough } from 'node:stream';
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { Chess } from 'chess.js';
import { createEvaluator, LIMITS } from '../../workers/repertoire-stockfish/evaluate.mjs';
import { createOwnedLauncher, runStdio } from '../../workers/repertoire-stockfish/stdio.mjs';

const START='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
function request({starting_fen=START,history_uci=[],learner_color='white',candidates=['e2e4'],...context}={}) {
  const board=new Chess(starting_fen);
  for (const uci of history_uci) board.move({from:uci.slice(0,2),to:uci.slice(2,4),promotion:uci[4]});
  return {request_id:'synthetic-497',context:{starting_fen,history_uci,target_fen:board.fen({forceEnpassantSquare:true}),
    variant:'standard',learner_color,engine_version:'Stockfish fake',max_depth:8,max_nodes:1000,max_time_ms:100,
    threads:1,hash_mb:16,...context},candidates};
}
function fake({answer,version='Stockfish fake',onGo,failClose=false}={}) {
  const state={commands:[],closed:0,alive:0};
  state.launch=({onLine,onError})=>{
    state.alive++;state.emit=onLine;state.error=onError;
    let board,search=0;
    return {send(command){
      state.commands.push(command);
      queueMicrotask(()=>{
        if(command==='uci'){onLine('id name '+version);onLine('uciok');}
        if(command==='isready')onLine('readyok');
        if(command.startsWith('position fen ')){
          const [fen,moves]=command.slice(13).split(' moves ');board=new Chess(fen);
          for(const uci of moves?.split(' ')??[])board.move({from:uci.slice(0,2),to:uci.slice(2,4),promotion:uci[4]});
        }
        if(command.startsWith('go ')){
          const candidate=command.split(' searchmoves ')[1];
          const uci=candidate??board.moves({verbose:true})[0].lan;
          const count=search++;
          if(onGo){onGo({onLine,onError,command,uci,count});return;}
          const result=answer?.({candidate,uci,count})??{score:'cp 50',depth:8};
          onLine(`info depth ${result.depth??8} nodes 1000 score ${result.score} pv ${uci}`);
          onLine(`bestmove ${uci}`);
        }
      });
    },async close(){state.closed++;state.alive--;if(failClose)throw new Error('not verified');}};
  };
  state.evaluator=createEvaluator({launch:state.launch,startupTimeoutMs:100,searchTimeoutMs:100,totalTimeoutMs:1000});
  return state;
}
const contractCheck=evidence=>execFileSync(process.execPath,['--experimental-strip-types','--input-type=module','-e',
  'const m=await import(process.argv[1]); let s=""; for await(const c of process.stdin)s+=c; m.EngineEvidenceSchema.parse(JSON.parse(s));',
  new URL('../../app/features/product-api/contracts.ts',import.meta.url).href],{input:JSON.stringify(evidence),stdio:['pipe','pipe','pipe']});

test('explicit_candidate_outside_top_five_evaluated',async()=>{
  const engine=fake();const input=request({candidates:['e2e4','d2d4','g1f3','c2c4','b1c3','g2g3','a2a3']});
  const result=await engine.evaluator.evaluate(input);
  assert.equal(result.status,'ok');assert.equal(result.evidence.candidates.length,7);
  const searches=engine.commands.filter(command=>command.startsWith('go '));
  assert.equal(searches.length,8);assert(!searches[0].includes('searchmoves'));
  assert(searches.at(-1).endsWith('searchmoves a2a3'));assert(searches.every(command=>command.includes('depth 8 nodes 1000 movetime 100')));
  assert.equal(engine.closed,1);assert.equal(engine.alive,0);
  assert(Object.isFrozen(result.evidence.context.history_uci));
  contractCheck(result.evidence);
  const fixture=JSON.parse(await readFile(new URL('../../contracts/product-v02/fixtures/engine-explicit-candidate.json',import.meta.url)));
  assert.deepEqual(Object.keys(result.evidence).sort(),Object.keys(fixture.payload).sort());
});

test('black_and_white_score_perspectives',async()=>{
  for(const history of [[],['e2e4']]) for(const color of ['white','black']){
    const engine=fake();const result=await engine.evaluator.evaluate(request({history_uci:history,learner_color:color,candidates:history.length?['e7e5']:['e2e4']}));
    const expected=((history.length===0)===(color==='white'))?50:-50;
    assert.equal(result.evidence.best.score.value,expected);assert.equal(result.evidence.candidates[0].score.value,expected);
  }
});

test('history_castling_and_ep_preserved',async()=>{
  const cases=[request({history_uci:['e2e4','a7a6','e4e5','d7d5'],candidates:['e5d6']}),
    request({starting_fen:'r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1',history_uci:['e1g1'],candidates:['e8c8'],learner_color:'black'})];
  for(const input of cases){
    const engine=fake();const result=await engine.evaluator.evaluate(input);
    assert.deepEqual(result.evidence.context.history_uci,input.context.history_uci);
    assert.equal(result.evidence.context.target_fen,input.context.target_fen);
    assert(engine.commands.includes(`position fen ${input.context.starting_fen} moves ${input.context.history_uci.join(' ')}`));
  }
});

test('illegal_candidate_rejected',async()=>{
  const engine=fake();
  for(const input of [request({candidates:['e2e5']}),request({history_uci:[],candidates:['e1g1']})]){
    await assert.rejects(engine.evaluator.evaluate(input),{code:'illegal_move'});
  }
  const illegalHistory=request();illegalHistory.context.history_uci=['e2e5'];
  await assert.rejects(engine.evaluator.evaluate(illegalHistory),{code:'illegal_move'});
  assert.equal(engine.commands.length,0);
});

test('mate_and_bound_not_cp',async()=>{
  const mate=await fake({answer:()=>({score:'mate 3'})}).evaluator.evaluate(request({learner_color:'black'}));
  assert.deepEqual(mate.evidence.best.score,{kind:'mate',value:-3});contractCheck(mate.evidence);
  for(const bound of ['lowerbound','upperbound']){
    const result=await fake({answer:()=>({score:`cp 12 ${bound}`})}).evaluator.evaluate(request({learner_color:'black'}));
    assert.equal(result.status,'inconclusive');assert.equal(result.evidence.best.score,null);
    assert.equal(result.evidence.best.unavailable_reason,bound==='lowerbound'?'score_upperbound':'score_lowerbound');
    assert.equal(result.evidence.candidates[0].unavailable_reason,result.evidence.best.unavailable_reason);
    contractCheck(result.evidence);
  }
});

test('mismatched_context_inconclusive',async()=>{
  const engine=fake();const bad=request();bad.context.target_fen=START.replace('0 1','1 1');
  await assert.rejects(engine.evaluator.evaluate(bad),{code:'context_mismatch'});assert.equal(engine.commands.length,0);
  const shallow=await fake({answer:({candidate})=>({depth:candidate?7:8,score:'cp 50'})}).evaluator.evaluate(request());
  assert.equal(shallow.evidence.candidates[0].score,null);assert.equal(shallow.evidence.candidates[0].unavailable_reason,'comparison_depth_mismatch');
  const inconsistent=await fake({answer:({candidate})=>({score:candidate?'cp 51':'cp 50'})}).evaluator.evaluate(request());
  assert.equal(inconsistent.evidence.candidates[0].unavailable_reason,'baseline_inconsistent');
  assert.equal(inconsistent.diagnostics.inconsistent_candidates[0].score.value,51);
  const version=await fake({version:'Other engine'}).evaluator.evaluate(request());
  assert.equal(version.evidence.best.unavailable_reason,'engine_version_mismatch');assert.equal(version.evidence.context.engine_version,'Other engine');
  contractCheck(version.evidence);
});

test('timeout_and_cancel_release_owned_process',async()=>{
  const timeout=fake({onGo:({onLine,uci})=>onLine(`info depth 3 nodes 20 score cp 4 pv ${uci}`)});
  const result=await timeout.evaluator.evaluate(request());
  assert.equal(result.evidence.unavailable_reason,'timeout');assert.equal(result.diagnostics.incomplete_baseline.depth,3);assert.equal(timeout.closed,1);assert.equal(timeout.alive,0);contractCheck(result.evidence);
  const controller=new AbortController();
  const cancel=fake({onGo:()=>controller.abort()});
  const cancelled=await cancel.evaluator.evaluate(request(),{signal:controller.signal});
  assert.equal(cancelled.status,'cancelled');assert.equal(cancel.closed,1);assert.equal(cancel.alive,0);
  const failing=fake({failClose:true});await assert.rejects(failing.evaluator.evaluate(request()),{code:'cleanup_unverified'});
  await assert.rejects(failing.evaluator.evaluate(request()),{code:'cleanup_unverified'});assert.equal(failing.closed,1);
  const hung=fake();const originalLaunch=hung.launch;
  const bounded=createEvaluator({launch:callbacks=>({...originalLaunch(callbacks),close:()=>new Promise(()=>{})})});
  await assert.rejects(bounded.evaluate(request()),{code:'cleanup_unverified'});
  await assert.rejects(bounded.evaluate(request()),{code:'cleanup_unverified'});
});

test('malformed_uci_and_engine_exit_are_typed',async()=>{
  for(const [code,onGo] of [['malformed_uci',({onLine})=>onLine('info depth 8 score cp potato pv e2e4')],
    ['engine_exit',({onError})=>onError()],['search_context_mismatch',({onLine,count})=>{
      onLine(`info depth 8 nodes 2 score cp 50 pv ${count?'d2d4':'a2a3'}`);onLine(`bestmove ${count?'d2d4':'a2a3'}`);
    }]]){
    const engine=fake({onGo});const result=await engine.evaluator.evaluate(request());
    assert.equal(result.error.code,code);assert.equal(engine.closed,1);
  }
});

async function stdio(raw,evaluator){
  let text='';const output=new Writable({write(chunk,_,next){text+=chunk.toString();next();}});
  const result=await runStdio({input:Readable.from([raw]),output,evaluator});
  assert.equal(text.trim().split('\n').length,1);assert(Buffer.byteLength(text)<=LIMITS.responseBytes+1);
  assert.deepEqual(JSON.parse(text),result);return result;
}

test('request_limit_and_output_bound',async()=>{
  for(const input of [request({threads:2}),request({hash_mb:65}),request({max_depth:33}),
    request({max_depth:null,max_nodes:null,max_time_ms:null}),request({max_nodes:NaN}),request({variant:'chess960'})]){
    const engine=fake();await assert.rejects(engine.evaluator.evaluate(input));assert.equal(engine.commands.length,0);
  }
  const engine=fake();const big=await stdio('x'.repeat(LIMITS.requestBytes+1),engine.evaluator);
  assert.equal(big.error.code,'request_limit');assert.equal(engine.commands.length,0);
  assert.equal((await stdio('{',engine.evaluator)).error.code,'invalid_json');
  const flood=await fake({onGo:({onLine})=>onLine('x'.repeat(LIMITS.lineBytes+1))}).evaluator.evaluate(request());
  assert.equal(flood.error.code,'engine_output_limit');
  const mismatch=await stdio(JSON.stringify(request()),{evaluate:async()=>({request_id:'other'})});
  assert.equal(mismatch.error.code,'response_identity_mismatch');
  const output=await stdio(JSON.stringify(request()),{evaluate:async()=>({request_id:'synthetic-497',text:'x'.repeat(LIMITS.responseBytes)})});
  assert.equal(output.error.code,'response_limit');
  const stalled=new PassThrough();let encoded='';
  const timed=await runStdio({input:stalled,output:{write:text=>{encoded+=text;}},evaluator:engine.evaluator,inputTimeoutMs:5});
  assert.equal(timed.error.code,'input_timeout');assert(encoded.includes('input_timeout'));
});

test('serialization_and_caller_isolation',async()=>{
  let finish;const engine=fake({onGo:({onLine,uci})=>{finish=()=>{onLine(`info depth 8 nodes 3 score cp 1 pv ${uci}`);onLine(`bestmove ${uci}`);};}});
  const input=request();const pending=engine.evaluator.evaluate(input);
  input.candidates[0]='d2d4';input.context.history_uci.push('a2a4');
  while(!finish)await new Promise(resolve=>setImmediate(resolve));
  await assert.rejects(engine.evaluator.evaluate(request()),{code:'busy'});finish();finish=null;
  while(!finish)await new Promise(resolve=>setImmediate(resolve));finish();
  assert.deepEqual((await pending).evidence.requested_candidates,['e2e4']);assert.equal(engine.closed,1);
});

test('owned_transport_kills_only_its_child_and_verifies_exit',async()=>{
  const child=new EventEmitter();child.send=(_message,callback)=>callback();
  const killed=[];child.kill=signal=>{killed.push(signal);queueMicrotask(()=>{child.emit('exit');child.emit('close');});return true;};
  const launch=createOwnedLauncher({forkProcess:()=>child,closeGraceMs:2,killWaitMs:10});
  const transport=launch({onLine:()=>{},onError:()=>{}});await transport.close();
  assert.deepEqual(killed,['SIGKILL']);
  const stuck=new EventEmitter();stuck.send=(_message,callback)=>callback();stuck.kill=()=>false;
  const denied=createOwnedLauncher({forkProcess:()=>stuck,closeGraceMs:1,killWaitMs:2})({onLine:()=>{},onError:()=>{}});
  await assert.rejects(denied.close(),{code:'cleanup_unverified'});
});

test('no_database_or_study_dependency',async()=>{
  const sources=await Promise.all(['evaluate.mjs','stdio.mjs'].map(name=>readFile(new URL('../../workers/repertoire-stockfish/'+name,import.meta.url),'utf8')));
  const imports=sources.join('\n').match(/(?:from\s+|import\s*)['"][^'"]+['"]/g)??[];
  assert(imports.every(line=>!/database|services|study|games\/|engine-search|main\.py|https?:/.test(line)));
  assert(!sources.join('\n').includes('fetch('));
});
