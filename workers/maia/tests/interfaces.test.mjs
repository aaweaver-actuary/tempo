import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as publicApi from '../interfaces/index.mjs';
import { MaiaError } from '../errors.mjs';
import { SUPPORTED_RATINGS } from '../core.mjs';
import { resolveRatingInputs } from '../model.mjs';
import { createProbabilityProvider, createProcessExecution } from '../probability-provider.mjs';
import { probabilityStdio } from '../probability-stdio.mjs';
const names=['MaiaError','SUPPORTED_RATINGS','createProbabilityProvider','createProcessExecution','probabilityStdio','resolveRatingInputs'];
const root=fileURLToPath(new URL('../../../',import.meta.url));
const url=new URL('../interfaces/index.mjs',import.meta.url);

test('public Maia exports preserve identity without wildcard helper leakage',()=>{
  assert.deepEqual(Object.keys(publicApi).sort(),names.toSorted());
  assert.deepEqual({...publicApi},{MaiaError,SUPPORTED_RATINGS,resolveRatingInputs,createProbabilityProvider,createProcessExecution,probabilityStdio});
  assert.equal(publicApi.resolveRatingInputs({ratings:{elo_self:1300,elo_oppo:1800}}).elo_oppo,1800);
  assert(!('validateProbabilityRequest' in publicApi));
});

test('cold Maia interface import and factories start no process, model or network',()=>{
  const script=`
    import {createRequire,syncBuiltinESMExports} from 'node:module';
    const require=createRequire(import.meta.url),deny=()=>{throw Error('startup forbidden');};
    const cp=require('node:child_process');for(const k of ['spawn','fork','exec','execFile'])cp[k]=deny;
    require('node:net').connect=deny;require('node:net').createConnection=deny;
    require('node:fs/promises').readFile=deny;syncBuiltinESMExports();globalThis.fetch=deny;
    const pub=await import(${JSON.stringify(url.href)});
    const provider=pub.createProbabilityProvider();pub.createProcessExecution();await provider.close();
    process.stdout.write('inert');
  `;
  assert.equal(execFileSync(process.execPath,['--input-type=module','-e',script],{encoding:'utf8',timeout:5000}),'inert');
});

test('invalid public request is rejected before any execution capability starts',async()=>{
  let calls=0;
  const provider=publicApi.createProbabilityProvider({createExecution:()=>{calls++;throw Error('forbidden');}});
  await assert.rejects(provider.evidence({}),error=>error instanceof publicApi.MaiaError&&error.code==='invalid_request');
  await provider.close();assert.equal(calls,0);
});

test('Maia declaration positive and negative caller checks use canonical evidence types',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'tempo-maia-interface-'));
  try{
    const source=`
      import {createProbabilityProvider,MaiaError,resolveRatingInputs,SUPPORTED_RATINGS} from ${JSON.stringify(url.pathname)};
      import type {ProbabilityRequest,ProbabilityResult,Execution} from ${JSON.stringify(fileURLToPath(new URL('../interfaces/index',import.meta.url)))};
      declare const request:ProbabilityRequest;declare const execution:Execution;
      const provider=createProbabilityProvider({createExecution:()=>execution});
      const result:Promise<ProbabilityResult>=provider.evidence(request,{signal:new AbortController().signal});
      provider.diagnostic(request,['e2e4']);provider.close();
      resolveRatingInputs({ratings:{elo_self:1300,elo_oppo:1800}});new MaiaError('code','message');
      // @ts-expect-error execution lifecycle cannot be replaced by an unbounded arbitrary object
      createProbabilityProvider({createExecution:()=>({})});
      // @ts-expect-error ordered rating tensors are required
      resolveRatingInputs({ratings:{elo_self:1300}});
      // @ts-expect-error canonical population cannot be absent
      request.population=null;
      // @ts-expect-error requests are immutable
      request.ratings.elo_self=1700;
      // @ts-expect-error shared legal-move inventory is immutable
      request.position.legal_moves.push('e2e4');
      // @ts-expect-error public ratings are immutable
      SUPPORTED_RATINGS.push(2000);
      // @ts-expect-error error code is a string
      new MaiaError(1,'message');
      void result;
    `;
    const target=join(directory,'consumer.mts');await writeFile(target,source);
    execFileSync(process.execPath,[join(root,'node_modules/typescript/bin/tsc'),'--noEmit','--strict','--skipLibCheck','--allowJs',
      '--moduleResolution','bundler','--module','esnext','--target','es2022','--types','node','--typeRoots',join(root,'node_modules/@types'),target],
      {cwd:root,encoding:'utf8',timeout:10000,stdio:['ignore','pipe','pipe']});
  }finally{await rm(directory,{recursive:true,force:true});}
});
