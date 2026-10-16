import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm, readFile, readdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readHostStudyActivity } from '../../scripts/host-study-activity.mjs';
import { acquireDevelopmentBudget, assertDevelopmentCapacity, composeResourceLimits, resourceBudgets,
  waitForStudyQuiet, managedBuild, reconcileDevelopmentBuilder, reconcileDevelopmentBudget } from '../../scripts/development-budget.mjs';

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
  await waitForStudyQuiet({ activityUrl:'http://test.invalid/control',hostActivity:async()=>({available:true,active:false}),legacyAbsent:false,read:async () => {
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

async function builderFixture(run) {
  const parent = await mkdtemp(join(tmpdir(), 'tempo-builder-regression-'));
  const directory = join(parent, 'lock');
  const state = { builder: false, container: false, commands: [] };
  const absent = message => { const error = new Error(message); error.stderr = message; throw error; };
  const docker = async args => {
    state.commands.push(args);
    if (args[0] === 'ps') return '';
    if (args[0] === 'buildx' && args[1] === 'create') {
      state.name = args[args.indexOf('--name') + 1];
      state.token = args.find(value => value.startsWith('env.TEMPO_RESOURCE_OWNER=')).split('=')[1];
      const journal = JSON.parse(await readFile(join(directory, 'resources', encodeURIComponent(`builder:${state.name}`)), 'utf8'));
      assert.equal(journal.token, state.token, 'creation is preceded by a durable ownership record');
      if (state.failBeforeCreate) throw new Error('create failed without effects');
      state.builder = true;
      if (state.failAfterCreate) throw new Error('create disconnected after effects');
      return state.name;
    }
    if (args[0] === 'buildx' && args[1] === 'inspect') {
      if (state.inspectFailure) throw new Error('Docker control unavailable');
      if (!state.builder) return absent(`no builder "${args[2]}" found`);
      if (args.includes('--bootstrap')) state.container = true;
      return `Driver Options: env.TEMPO_RESOURCE_OWNER="${state.foreignOwner ?? state.token}"`;
    }
    if (args[0] === 'buildx' && args[1] === 'rm') {
      if (state.failRemoval) throw new Error('builder removal failed');
      state.builder = false;
      state.container = false;
      if (state.rmDisconnect) throw new Error('remove disconnected after effects');
      return '';
    }
    if (args[0] === 'inspect' || args[0] === 'container' && args[1] === 'inspect') {
      const name = args.at(-1);
      if (!state.container) return absent(`No such container: ${name}`);
      return JSON.stringify([{ Config: { User: '1000:1000', Env: [`TEMPO_RESOURCE_OWNER=${state.foreignOwner ?? state.token}`] },
        HostConfig: { Memory: 2048 * 1024**2, CpuQuota: 200000, CpuPeriod: 100000 } }]);
    }
    throw new Error(`Unexpected Docker operation: ${JSON.stringify(args)}`);
  };
  try { await run({directory, state, docker, options: {directory, docker, quiet: async () => {}}}); }
  finally { await rm(parent, {recursive: true, force: true}); }
}

test('A1 builder normal cleanup verifies absence before releasing ownership', () => builderFixture(async ({directory, state, options}) => {
  assert.equal(await managedBuild(async () => 42, options), 42);
  assert.equal(state.builder, false);
  assert.equal(state.container, false);
  await assert.rejects(readFile(join(directory, 'owner.json')), {code: 'ENOENT'});
  assert(state.commands.filter(args => args[0] === 'buildx' && args[1] === 'inspect').length >= 4);
}));

test('A2 externally successful builder creation is reconciled after command failure', () => builderFixture(async ({directory, state, options}) => {
  state.failAfterCreate = true;
  await assert.rejects(managedBuild(async () => assert.fail('build must not start'), options), /disconnected after effects/);
  assert.equal(state.builder, false);
  assert(state.commands.some(args => args[0] === 'buildx' && args[1] === 'rm'));
  await assert.rejects(readFile(join(directory, 'owner.json')), {code: 'ENOENT'});
}));

test('A3 builder creation without external effects releases only after absence proof', () => builderFixture(async ({directory, state, options}) => {
  state.failBeforeCreate = true;
  await assert.rejects(managedBuild(async () => {}, options), /without effects/);
  assert(!state.commands.some(args => args[1] === 'rm'));
  await assert.rejects(readFile(join(directory, 'owner.json')), {code: 'ENOENT'});
}));

test('A4 failed builder cleanup retains ownership and excludes a second heavy invocation', () => builderFixture(async ({directory, state, options}) => {
  state.failRemoval = true;
  await assert.rejects(managedBuild(async () => {}, options), /retain ownership/);
  assert.equal(state.builder, true);
  assert.equal((await readdir(join(directory, 'resources'))).length, 1);
  const controller = new AbortController();
  await assert.rejects(acquireDevelopmentBudget({directory, signal: controller.signal,
    pause: async () => controller.abort(new Error('second invocation remained blocked'))}), /remained blocked/);
}));

test('A5 competing builder invocation proceeds only after the first verifies safe release', () => builderFixture(async ({directory, state, docker, options}) => {
  let finishFirst;
  const building = new Promise(resolve => { finishFirst = resolve; });
  let started;
  const entered = new Promise(resolve => { started = resolve; });
  const first = managedBuild(async () => { started(); await building; }, options);
  await entered;
  let observedWait = false;
  const waiting = acquireDevelopmentBudget({directory, pause: async () => {
    observedWait = true;
    assert.equal(state.builder, true);
    finishFirst();
    await first;
  }});
  const second = await waiting;
  assert.equal(observedWait, true);
  assert.equal(state.builder, false);
  await second.release();
  assert.equal(typeof docker, 'function');
}));

test('A6 foreign builder or lock ownership is never removed by reconciliation', () => builderFixture(async ({directory, state, docker, options}) => {
  await assert.rejects(managedBuild(async () => { state.foreignOwner = 'another-owner'; }, options), /ownership mismatch/);
  assert.equal(state.builder, true);
  assert(!state.commands.some(args => args[1] === 'rm'));
  await assert.rejects(reconcileDevelopmentBuilder(state.name, {directory, docker, token: 'wrong-token'}), /ownership changed/);
  assert.equal(JSON.parse(await readFile(join(directory, 'owner.json'))).token, state.token);
}));

test('A7 interrupted owner requires explicit resource reconciliation before budget reclamation', () => builderFixture(async ({directory, state, docker, options}) => {
  state.failRemoval = true;
  await assert.rejects(managedBuild(async () => {}, options), /cleanup incomplete/);
  const owner = JSON.parse(await readFile(join(directory, 'owner.json')));
  owner.pid = 2147483647;
  await writeFile(join(directory, 'owner.json'), JSON.stringify(owner));
  await assert.rejects(acquireDevelopmentBudget({directory}), /Interrupted development owner/);
  await assert.rejects(reconcileDevelopmentBudget({directory, token: owner.token, docker}), /remains/);
  state.failRemoval = false;
  await reconcileDevelopmentBudget({directory, token: owner.token, docker});
  assert.equal(state.builder, false);
  await assert.rejects(readFile(join(directory, 'owner.json')), {code: 'ENOENT'});
}));

test('A8 preparation failure releases ownership without attempting external creation', () => builderFixture(async ({directory, state, options}) => {
  await assert.rejects(managedBuild(async () => {}, {...options, prepare: async () => {throw new Error('config write failed');}}), /config write failed/);
  assert.equal(state.commands.length, 0);
  await assert.rejects(readFile(join(directory, 'owner.json')), {code: 'ENOENT'});
}));

test('A9 unavailable absence verification retains the builder journal and ownership', () => builderFixture(async ({directory, state, options}) => {
  await assert.rejects(managedBuild(async () => {state.inspectFailure = true;}, options), /cleanup incomplete/);
  assert.equal(state.builder, true);
  assert.equal((await readdir(join(directory, 'resources'))).length, 1);
}));

test('A10 ambiguous successful removal still requires independent absence verification', () => builderFixture(async ({directory, state, options}) => {
  state.rmDisconnect = true;
  await managedBuild(async () => {}, options);
  assert.equal(state.builder, false);
  await assert.rejects(readFile(join(directory, 'owner.json')), {code: 'ENOENT'});
}));

test('A11 inherited ownership cannot admit a second builder execution lane', () => builderFixture(async ({directory, state, options}) => {
  const lease=await acquireDevelopmentBudget({directory});
  try {
    await managedBuild(async()=>{
      const creations=state.commands.filter(args=>args[0]==='buildx'&&args[1]==='create').length;
      await assert.rejects(managedBuild(async()=>assert.fail('competing build executed'),{...options,token:lease.token}),/execution lane is occupied/);
      assert.equal(state.commands.filter(args=>args[0]==='buildx'&&args[1]==='create').length,creations);
    },{...options,token:lease.token});
    assert.equal(state.builder,false);
  } finally {await lease.release();}
}));

test('B independent host sessions expire separately while stale or malformed producers deny admission', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'tempo-host-activity-'));
  const now = 100_000_000_000n;
  const snapshot = {version:1, producer:'producer', pendingRequests:false, validUntilNs:String(now+15_000_000_000n),
    sessions:{'tab-a':String(now+1_000_000_000n),'tab-b':String(now+3_000_000_000n)}};
  const path = join(directory,'producer.json');
  try {
    await writeFile(path,JSON.stringify(snapshot));
    assert.deepEqual(await readHostStudyActivity({directory,now:now+2_000_000_000n}),{available:true,active:true});
    assert.deepEqual(await readHostStudyActivity({directory,now:now+3_000_000_000n}),{available:true,active:false});
    await writeFile(path,JSON.stringify({...snapshot,pendingRequests:true}));
    assert.deepEqual(await readHostStudyActivity({directory,now:now+10_000_000_000n}),{available:true,active:true});
    assert.equal((await readHostStudyActivity({directory,now:now+15_000_000_000n})).available,false);
    await writeFile(path,JSON.stringify({...snapshot,sessions:{'tab-b':'invalid'}}));
    assert.equal((await readHostStudyActivity({directory,now})).available,false);
  } finally { await rm(directory,{recursive:true,force:true}); }
});

test('B host control unavailable and active defer even when legacy is explicitly absent', async () => {
  let reads=0, waits=0;
  await waitForStudyQuiet({legacyAbsent:true,hostActivity:async()=>({available:++reads>1,active:reads===2}),
    read:async()=>assert.fail('Absent legacy is not contacted'),pause:async()=>{waits++;}});
  assert.equal(reads,3);assert.equal(waits,2);
});

test('B refused legacy control is not confirmed inactivity', async () => {
  const controller=new AbortController();let waits=0;
  await assert.rejects(waitForStudyQuiet({legacyAbsent:false,hostActivity:async()=>({available:true,active:false}),
    read:async()=>{throw new TypeError('fetch failed',{cause:{code:'ECONNREFUSED'}});},signal:controller.signal,
    pause:async()=>{waits++;controller.abort(new Error('control stayed unavailable'));}}),/stayed unavailable/);
  assert.equal(waits,1);
});
