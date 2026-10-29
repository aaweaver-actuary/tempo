import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { Chess } from 'chess.js';
import { createEvaluator } from '../../workers/repertoire-stockfish/evaluate.mjs';
import { createOwnedLauncher } from '../../workers/repertoire-stockfish/stdio.mjs';

const executable=process.env.TEMPO_STOCKFISH_SMOKE_EXECUTABLE;
const version=process.env.TEMPO_STOCKFISH_SMOKE_VERSION;
// Explicit version avoids quietly accepting a different installed engine.
test('explicit existing Stockfish tiny legal context and cleanup smoke',{
  skip:!executable||!version?'Explicit existing executable and engine version required; real engine unverified':false,
  timeout:20000,
},async()=>{
  const children=[];
  const launch=createOwnedLauncher({executable,spawnProcess:(...args)=>{const child=spawn(...args);children.push(child);return child;}});
  const evaluator=createEvaluator({launch,startupTimeoutMs:3000,searchTimeoutMs:1000,totalTimeoutMs:5000});
  const cases=[
    {starting_fen:'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',history_uci:[],learner_color:'white',candidates:['e2e4','a2a3']},
    {starting_fen:'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',history_uci:['e2e4'],learner_color:'black',candidates:['e7e5','c7c5']},
    {starting_fen:'r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1',history_uci:['e1g1'],learner_color:'black',candidates:['e8c8','h8h7']},
  ];
  for(const [i,item] of cases.entries()){
    const board=new Chess(item.starting_fen);
    for(const uci of item.history_uci) board.move({from:uci.slice(0,2),to:uci.slice(2,4),promotion:uci[4]});
    const {candidates,...position}=item;
    const context={...position,target_fen:board.fen({forceEnpassantSquare:true}),variant:'standard',engine_version:version,
      max_depth:4,max_nodes:2000,max_time_ms:100,threads:1,hash_mb:16};
    const result=await evaluator.evaluate({request_id:'smoke-'+i,context,candidates});
    assert(['ok','inconclusive'].includes(result.status),JSON.stringify(result));
    assert.equal(result.evidence.context.engine_version,version);
    assert.deepEqual(result.evidence.context,context);assert.deepEqual(result.evidence.requested_candidates,candidates);
    assert(result.evidence.best?.score,'baseline must have actual engine evidence');
    assert.equal(result.evidence.best.depth,4);
    assert(board.moves({verbose:true}).some(move=>move.lan===result.evidence.best.move_uci));
    assert.deepEqual(result.evidence.candidates.map(value=>value.move_uci),candidates);
    for(const candidate of result.evidence.candidates){
      assert.equal(candidate.depth,4);assert(candidate.nodes>0);
      if(!candidate.score)assert.equal(candidate.unavailable_reason,'baseline_inconsistent');
      else assert(['centipawns','mate'].includes(candidate.score.kind));
    }
    assert(children.at(-1).exitCode!==null||children.at(-1).signalCode!==null,'owned engine exited before return');
  }
  assert.equal(children.length,3);
});
