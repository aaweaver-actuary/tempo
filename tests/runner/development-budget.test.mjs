import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { acquireDevelopmentBudget, assertDevelopmentCapacity, composeResourceLimits, resourceBudgets,
  waitForStudyQuiet } from '../../scripts/development-budget.mjs';

test('study budget serializes independent development owners and never removes another owner', async () => {
  const parent = await mkdtemp(join(tmpdir(),'tempo-budget-test-'));
  const directory = join(parent,'lock');
  let first, second;
  try {
    first = await acquireDevelopmentBudget({ directory });
    const controller = new AbortController();
    let waited = false;
    await assert.rejects(acquireDevelopmentBudget({ directory, signal:controller.signal,
      pause: async () => { waited = true; controller.abort(new Error('cancelled waiter')); } }), /cancelled waiter/);
    assert.equal(waited,true);
    assert.equal(JSON.parse(await readFile(join(directory,'owner.json'))).token, first.token);
    await first.release(); first = undefined;
    second = await acquireDevelopmentBudget({ directory });
    assert.equal(typeof second.token,'string');
  } finally { await first?.release(); await second?.release(); await rm(parent,{recursive:true,force:true}); }
});

test('study budget treats active and unavailable control as deferral and admits a quiet snapshot', async () => {
  let calls = 0, waits = 0;
  await waitForStudyQuiet({ activityUrl:'http://test.invalid/control',read:async () => {
    calls++;
    if (calls === 1) return new Response('unavailable',{status:503});
    return Response.json({active:calls === 2});
  },pause:async () => { waits++; } });
  assert.equal(calls,3); assert.equal(waits,2);
});

test('aggregate development limits include all services and reject an additional concurrent stack', () => {
  const limits = composeResourceLimits(Object.keys(resourceBudgets.developmentServices),'owner');
  assert.equal(Object.keys(limits.services).length,10);
  const existing = [{HostConfig:{NanoCpus:2e9,Memory:2*1024**3},Config:{Labels:{
    'com.docker.compose.project':'another-project','com.docker.compose.service':'postgres'}}}];
  assert.throws(() => assertDevelopmentCapacity(['postgres'],'this-project',args =>
    args[0] === 'ps' ? 'owned-container' : JSON.stringify(existing)), /exceed the aggregate/);
  assert.doesNotThrow(() => assertDevelopmentCapacity([], 'another-project', args =>
    args[0] === 'ps' ? 'owned-container' : JSON.stringify(existing)));
});

test('optional application services share the one CPU and two GiB policy', () => {
  const limits = Object.values(resourceBudgets.optionalServices);
  assert.equal(limits.reduce((sum,item)=>sum+item.memoryMiB,0),2048);
  assert.ok(Math.abs(limits.reduce((sum,item)=>sum+item.cpus,0)-1)<1e-9);
});


test('rootless builder and one execution lane stay inside the aggregate development policy', () => {
  assert.equal(resourceBudgets.development.buildParallelism,1);
  const source = readFileSync(new URL('../../scripts/development-budget.mjs', import.meta.url),'utf8');
  assert(source.includes('image=moby/buildkit:rootless'));
  assert(source.includes('max-parallelism = 1'));
  const profiles = [resourceBudgets.developmentServices,resourceBudgets.legacyDevelopmentServices];
  for(const profile of profiles) {
    assert(Object.values(profile).reduce((sum,item)=>sum+item.cpus,0)<=2.000001);
    assert(Object.values(profile).reduce((sum,item)=>sum+item.memoryMiB,0)<=2048);
  }
});


test('component PostgreSQL selection acquires the shared budget while lightweight and list checks remain available', async()=>{
  const {componentSelectionNeedsBudget}=await import('../../tools/component-build/component-tests.mjs');
  assert.equal(componentSelectionNeedsBudget(['--component','scheduling']),false);
  assert.equal(componentSelectionNeedsBudget(['--component','learning','--list']),false);
  assert.equal(componentSelectionNeedsBudget(['--component','learning']),true);
});

test('managed component Docker preserves explicit caps and adds bounded defaults and owner labels',async()=>{
  const {managedContainerArguments}=await import('../../scripts/development-docker.mjs');
  const requested=managedContainerArguments(['run','--cpus','1','--memory','256m','--name','owned','image'],'token');
  assert.deepEqual(requested.limits,{cpus:1,memoryMiB:256});
  assert(requested.args.includes('tempo.resource-owner=token'));
  assert(requested.args.includes('tempo.resource-class=development'));
  const bounded=managedContainerArguments(['run','image'],'token');
  assert.deepEqual(bounded.limits,{cpus:1,memoryMiB:512});
  assert.throws(()=>managedContainerArguments(['run','--cpus','0','image'],'token'),/finite/);
  assert.throws(()=>managedContainerArguments(['run','--memory','unlimited','image'],'token'),/finite/);
});


test('independent development processes serialize one shared owner under concurrent invocation',async()=>{
  const parent=await mkdtemp(join(tmpdir(),'tempo-concurrent-budget-'));
  const directory=join(parent,'owner');const log=join(parent,'events.jsonl');
  const source=String.raw`import {acquireDevelopmentBudget} from ${JSON.stringify(new URL('../../scripts/development-budget.mjs',import.meta.url).href)};
    import {appendFileSync} from 'node:fs';
    const lease=await acquireDevelopmentBudget({directory:${JSON.stringify(directory)}});
    appendFileSync(${JSON.stringify(log)},JSON.stringify({event:'start',pid:process.pid})+'\n');
    await new Promise(resolve=>setTimeout(resolve,80));
    appendFileSync(${JSON.stringify(log)},JSON.stringify({event:'end',pid:process.pid})+'\n');
    await lease.release();`;
  const run=()=>new Promise((resolve,reject)=>{const child=spawn(process.execPath,['--input-type=module','-e',source],{stdio:['ignore','ignore','pipe']});let errors='';child.stderr.on('data',part=>{errors+=part;});child.on('error',reject);child.on('exit',code=>code===0?resolve():reject(new Error(errors)));});
  try {
    await Promise.all([run(),run()]);
    const events=(await readFile(log,'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    assert.deepEqual(events.map(event=>event.event),['start','end','start','end']);
    assert.notEqual(events[0].pid,events[2].pid);
  }finally{await rm(parent,{recursive:true,force:true});}
});
