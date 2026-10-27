import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as publicApi from '../../workers/repertoire-stockfish/interfaces/index.mjs';
import * as existingEvaluation from '../../workers/repertoire-stockfish/evaluate.mjs';
import * as existingStdio from '../../workers/repertoire-stockfish/stdio.mjs';

const publicUrl=new URL('../../workers/repertoire-stockfish/interfaces/index.mjs',import.meta.url);
const declarations=fileURLToPath(new URL('../../workers/repertoire-stockfish/interfaces/index',import.meta.url));
const repository=fileURLToPath(new URL('../../',import.meta.url));
const expected=['EvaluationError','LIMITS','createEvaluator','createOwnedLauncher','createStockfishEvaluator','runStdio'];

test('public names preserve existing operation and error identities, excluding implementation helpers',()=>{
  assert.deepEqual(Object.keys(publicApi).sort(),expected.toSorted());
  for(const name of ['EvaluationError','LIMITS','createEvaluator']) assert.equal(publicApi[name],existingEvaluation[name]);
  for(const name of ['createOwnedLauncher','createStockfishEvaluator','runStdio']) assert.equal(publicApi[name],existingStdio[name]);
  const error=new publicApi.EvaluationError('synthetic_reason');
  assert(error instanceof existingEvaluation.EvaluationError);assert.equal(error.code,'synthetic_reason');
  assert(Object.isFrozen(publicApi.LIMITS));
  assert(!('validateRequest' in publicApi));assert(!('parseInfo' in publicApi));
});

test('cold public import and factory creation do not launch an engine, provider, CLI or network',()=>{
  const source=`
    import assert from 'node:assert/strict';
    import {createRequire,syncBuiltinESMExports} from 'node:module';
    const require=createRequire(import.meta.url);
    const deny=()=>{throw new Error('forbidden startup');};
    const child=require('node:child_process');
    for(const key of ['spawn','fork','exec','execFile'])child[key]=deny;
    require('node:net').connect=deny;require('node:net').createConnection=deny;
    require('node:http').request=deny;require('node:https').request=deny;
    require('node:worker_threads').Worker=class{constructor(){deny();}};
    syncBuiltinESMExports();globalThis.fetch=deny;
    process.env=new Proxy(process.env,{get(target,key){
      if(key==='TEMPO_STOCKFISH_EXECUTABLE')deny();return Reflect.get(target,key);
    }});
    const api=await import(${JSON.stringify(publicUrl.href)});
    assert.deepEqual(Object.keys(api).sort(),${JSON.stringify(expected.toSorted())});
    api.createStockfishEvaluator();api.createOwnedLauncher();
    api.createEvaluator({launch:deny});
    process.stdout.write('cold-import-ok');
  `;
  assert.equal(execFileSync(process.execPath,['--input-type=module','-e',source],{encoding:'utf8',cwd:repository,timeout:5000}),'cold-import-ok');
});

test('public evaluator retains shared seven-candidate evidence and fake-owned cleanup semantics',async()=>{
  const fixture=JSON.parse(await readFile(new URL('../../contracts/product-v02/fixtures/engine-explicit-candidate.json',import.meta.url),'utf8')).payload;
  const searches=[];let closed=0;
  const evaluator=publicApi.createEvaluator({launch:({onLine})=>({send(command){
    queueMicrotask(()=>{
      if(command==='uci'){onLine('id name '+fixture.context.engine_version);onLine('uciok');}
      if(command==='isready')onLine('readyok');
      if(command.startsWith('go ')){
        searches.push(command);
        const candidate=command.split(' searchmoves ')[1]??fixture.best.move_uci;
        const facts=fixture.candidates.find(item=>item.move_uci===candidate);
        onLine(`info depth ${facts.depth} nodes ${facts.nodes} score cp ${facts.score.value} pv ${candidate}`);
        onLine('bestmove '+candidate);
      }
    });
  },async close(){closed++;}})});
  const result=await evaluator.evaluate({request_id:fixture.evidence_id,context:fixture.context,candidates:fixture.requested_candidates});
  assert.equal(result.status,'ok');assert.deepEqual(result.evidence,fixture);
  assert.equal(searches.length,8);assert(searches.at(-1).endsWith('searchmoves a2a3'));
  assert.equal(closed,1);
});

test('public surface preserves typed validation failure before a transport launches',async()=>{
  let launches=0;
  const evaluator=publicApi.createEvaluator({launch:()=>{launches++;throw new Error('unexpected');}});
  await assert.rejects(evaluator.evaluate({request_id:'bad'}),error=>error instanceof publicApi.EvaluationError&&error.code==='invalid_request_identity');
  assert.equal(launches,0);
});

test('public declarations type supported callers and reject malformed or mutable capabilities',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'tempo-stockfish-surface-'));
  try{
    const source=`
      import {createEvaluator,createOwnedLauncher,createStockfishEvaluator,EvaluationError,LIMITS,runStdio} from ${JSON.stringify(publicUrl.pathname)};
      import type {EvaluationRequest,Evaluator,OwnedLauncher,EvaluationResult,EngineEvidence} from ${JSON.stringify(declarations)};
      declare const launch:OwnedLauncher;
      declare const request:EvaluationRequest;
      const evaluator:Evaluator=createEvaluator({launch,startupTimeoutMs:100});
      const result:Promise<EvaluationResult>=evaluator.evaluate(request,{signal:new AbortController().signal});
      const concrete:Evaluator=createStockfishEvaluator({executable:'/explicit/existing/stockfish'});
      createOwnedLauncher({closeGraceMs:75,killWaitMs:500});
      runStdio({evaluator,output:{write(chunk:string){return chunk.length;}}});
      new EvaluationError('synthetic_reason');
      // @ts-expect-error transport capability is required
      createEvaluator();
      // @ts-expect-error factory must return an owned transport synchronously
      createEvaluator({launch:async()=>({send(){},async close(){}})});
      // @ts-expect-error executable configuration is a string
      createOwnedLauncher({executable:4});
      // @ts-expect-error errors retain their string code contract
      new EvaluationError(4);
      // @ts-expect-error immutable request candidate list
      request.candidates.push('e2e4');
      // @ts-expect-error shared learner color is not an arbitrary string
      request.context.learner_color='purple';
      // @ts-expect-error resource limits are immutable
      LIMITS.threads=2;
      // @ts-expect-error caller must supply the actual request envelope
      evaluator.evaluate(request.context);
      declare const evidence:EngineEvidence;
      if(evidence.best?.score?.kind==='mate'){
        // @ts-expect-error mate evidence does not become centipawn evidence
        const cp:'centipawns'=evidence.best.score.kind;
      }
      void result;void concrete;
    `;
    const fixture=join(directory,'consumer.mts');await writeFile(fixture,source);
    execFileSync(process.execPath,[join(repository,'node_modules/typescript/bin/tsc'),
      '--noEmit','--strict','--skipLibCheck','--allowJs','--moduleResolution','bundler',
      '--module','esnext','--target','es2022','--types','node',
      '--typeRoots',join(repository,'node_modules/@types'),fixture],{cwd:repository,encoding:'utf8',timeout:10000,stdio:['ignore','pipe','pipe']});
  }finally{await rm(directory,{recursive:true,force:true});}
});

test('public source permits only explicit local owner exports and shared contract type dependencies',async()=>{
  const entry=await readFile(publicUrl,'utf8');
  const types=await readFile(new URL('../../workers/repertoire-stockfish/interfaces/index.d.ts',import.meta.url),'utf8');
  assert(!/export\s*\*/.test(entry));assert(!/export\s*\*/.test(types));
  assert.deepEqual([...entry.matchAll(/from '([^']+)'/g)].map(match=>match[1]).sort(),['../evaluate.mjs','../stdio.mjs']);
  assert(!/\b(fetch|spawn|fork|runStdio)\s*\(/.test(entry));
  assert.deepEqual([...types.matchAll(/from '([^']+)'/g)].map(match=>match[1]).sort(),[
    '../../../app/features/product-api/contracts','node:child_process','node:stream',
  ]);
});
