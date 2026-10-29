import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {once} from 'node:events';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm, readFile, readdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readHostStudyActivity } from '../../scripts/host-study-activity.mjs';
import { acquireDevelopmentBudget, assertDevelopmentCapacity, composeResourceLimits, resourceBudgets,
  waitForStudyQuiet, managedBuild, managedComposeBuild, runWithDevelopmentBudget, reconcileDevelopmentBuilder, reconcileDevelopmentBudget } from '../../scripts/development-budget.mjs';

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


test('rootless builder and one execution lane preserve individual limits and aggregate memory', () => {
  assert.equal(resourceBudgets.development.concurrentInvocations,1);
  const source = readFileSync(new URL('../../scripts/development-budget.mjs', import.meta.url),'utf8');
  assert(source.includes('image=moby/buildkit:rootless'));
  assert.equal(Object.hasOwn(resourceBudgets.development, 'buildParallelism'), false, 'No internal worker fanout policy');
  assert.equal(Object.hasOwn(resourceBudgets.development, 'cpus'), false, 'No aggregate CPU ceiling');
  const profiles = [resourceBudgets.developmentServices,resourceBudgets.legacyDevelopmentServices];
  for(const profile of profiles) {
    assert(Object.values(profile).every(item=>Number.isFinite(item.cpus)&&item.cpus>0));
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
  const bounded=managedContainerArguments(['run','image'],'token',8);
  assert.deepEqual(bounded.limits,{cpus:8,memoryMiB:512});
  assert.throws(()=>managedContainerArguments(['run','image'],'token'),/finite/);
  assert.throws(()=>managedContainerArguments(['run','--cpus','0','image'],'token'),/finite/);
  assert.throws(()=>managedContainerArguments(['run','--cpus','Infinity','image'],'token'),/finite/);
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
  const state = { builder: false, container: false, commands: [], peers: [], daemonCpus:8 };
  const absent = message => { const error = new Error(message); error.stderr = message; throw error; };
  const docker = async args => {
    state.commands.push(args);
    if (args[0] === 'info') return JSON.stringify(state.daemonCpus);
    if (args[0] === 'ps') return state.peers.map(container => container.Id).join('\n');
    if (args[0] === 'inspect' && state.peers.some(container => args.includes(container.Id))) {
      assert.deepEqual(args.slice(1), state.peers.map(container => container.Id));
      return JSON.stringify(state.peers);
    }
    if (args[0] === 'buildx' && args[1] === 'create') {
      state.name = args[args.indexOf('--name') + 1];
      state.token = args.find(value => value.startsWith('env.TEMPO_RESOURCE_OWNER=')).split('=')[1];
      state.buildConfig = await readFile(args[args.indexOf('--buildkitd-config') + 1], 'utf8');
      state.requestedHost = {
        Memory: Number(args.find(value => value.startsWith('memory=')).slice(7, -1)) * 1024**2,
        CpuQuota: Number(args.find(value => value.startsWith('cpu-quota=')).split('=')[1]),
        CpuPeriod: Number(args.find(value => value.startsWith('cpu-period=')).split('=')[1]),
      };
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
        HostConfig: { ...state.requestedHost, ...state.kernelLimits } }]);
    }
    throw new Error(`Unexpected Docker operation: ${JSON.stringify(args)}`);
  };
  try { await run({directory, state, docker, options: {directory, docker, token:null, quiet: async () => {}}}); }
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

test('A14 daemon CPU defaults and explicit build limits bind creation and exact readback', async t => {
  for (const limits of [undefined, {cpus:1, memoryMiB:1536}, {cpus:0.5, memoryMiB:512}, {cpus:4, memoryMiB:1536}, {cpus:8, memoryMiB:512}]) {
    await t.test(limits ? JSON.stringify(limits) : 'daemon CPU allocation', () => builderFixture(async ({directory, state, options}) => {
      let builds = 0;
      assert.equal(await managedBuild(async () => { builds++; return 'built'; }, {...options, limits}), 'built');
      assert.equal(builds, 1);
      assert.equal(state.buildConfig, '[worker.oci]\n', 'BuildKit owns worker fanout within the verified container limits');
      assert.deepEqual(state.requestedHost, {
        Memory:(limits?.memoryMiB ?? 2048) * 1024**2,
        CpuQuota:(limits?.cpus ?? 8) * 100000, CpuPeriod:100000,
      });
      assert.equal(state.builder, false);
      assert.equal(state.container, false);
      await assert.rejects(readFile(join(directory, 'owner.json')), {code:'ENOENT'});
    }));
  }
});

test('A15 invalid or over-budget build limits fail before ownership, hooks or Docker', async t => {
  const invalid = [null, {}, {cpus:1}, {memoryMiB:1536},
    ...[0, -1, NaN, Infinity, '1', true, 0.000001].map(cpus => ({cpus, memoryMiB:1536})),
    ...[0, -1, NaN, Infinity, '1536', true, 1536.5].map(memoryMiB => ({cpus:1, memoryMiB})),
    {cpus:Number.MAX_SAFE_INTEGER, memoryMiB:1536}, {cpus:1, memoryMiB:2049}];
  for (const [index, limits] of invalid.entries()) {
    await t.test(String(index), () => builderFixture(async ({directory, state, options}) => {
      await assert.rejects(managedBuild(async () => assert.fail('invalid build executed'), {
        ...options, limits, prepare:async () => assert.fail('invalid preparation executed'),
        quiet:async () => assert.fail('invalid request waited for quiet'),
      }), /Managed build limits/);
      assert.deepEqual(state.commands, []);
      await assert.rejects(readFile(join(directory, 'owner.json')), {code:'ENOENT'});
    }));
  }
});

function builderPeer(cpus = 1, memoryMiB = 512) {
  return {Id:'peer-owned-container', HostConfig:{NanoCpus:cpus * 1e9, Memory:memoryMiB * 1024**2},
    Config:{Labels:{'tempo.resource-owner':'peer-owner', 'com.docker.compose.project':'peer-project',
      'com.docker.compose.service':'postgres'}}};
}

test('A16 lower build snapshot admits existing peers at the ceiling and preserves them through cleanup', () => builderFixture(async ({directory, state, options}) => {
  const limits = {cpus:1, memoryMiB:1536};
  state.peers = [builderPeer()];
  const peerSnapshot = structuredClone(state.peers);
  await managedBuild(async () => {}, {...options, limits, prepare:async path => {
    // Neither a caller hook nor an await may change the admitted allocation.
    limits.cpus = 2; limits.memoryMiB = 2048;
    await writeFile(path, '[worker.oci]\n');
  }});
  assert.deepEqual(state.requestedHost, {Memory:1536 * 1024**2, CpuQuota:100000, CpuPeriod:100000});
  assert.deepEqual(state.peers, peerSnapshot);
  assert(!state.commands.some(args => args.includes('rm') && args.includes('peer-owned-container')));
  await assert.rejects(readFile(join(directory, 'owner.json')), {code:'ENOENT'});
}));

test('A17 aggregate build admission rejects memory overflow without touching peers', async t => {
  for (const [name, peer, limits] of [
    ['memory overflow', builderPeer(1, 513), {cpus:1, memoryMiB:1536}],
    ['default still reserves full budget', builderPeer(0.1, 1), undefined],
  ]) {
    await t.test(name, () => builderFixture(async ({directory, state, options}) => {
      state.peers = [peer];
      const peerSnapshot = structuredClone(state.peers);
      await assert.rejects(managedBuild(async () => assert.fail('overflow build executed'), {...options, limits}), /exceed the aggregate/);
      assert.deepEqual(state.peers, peerSnapshot);
      assert.deepEqual(state.commands.map(args => args[0]), limits ? ['ps', 'inspect'] : ['info', 'ps', 'inspect']);
      await assert.rejects(readFile(join(directory, 'owner.json')), {code:'ENOENT'});
    }));
  }
});

test('A18 builder cgroup mismatch fails without fallback and verifies cleanup before release', async t => {
  for (const kernelLimits of [
    {Memory:2048 * 1024**2}, {Memory:1024 * 1024**2}, {Memory:0},
    {CpuQuota:200000}, {CpuQuota:50000}, {CpuQuota:-1},
    {CpuPeriod:200000}, {CpuQuota:50000, CpuPeriod:50000}, {NanoCpus:2e9},
  ]) {
    await t.test(JSON.stringify(kernelLimits), () => builderFixture(async ({directory, state, options}) => {
      state.kernelLimits = kernelLimits;
      state.peers = [builderPeer()];
      const peerSnapshot = structuredClone(state.peers);
      await assert.rejects(managedBuild(async () => assert.fail('mismatched build executed'), {
        ...options, limits:{cpus:1, memoryMiB:1536},
      }), /kernel limits do not match/);
      assert.equal(state.commands.filter(args => args[0] === 'buildx' && args[1] === 'create').length, 1);
      assert.equal(state.builder, false);
      assert.equal(state.container, false);
      assert.deepEqual(state.peers, peerSnapshot);
      await assert.rejects(readFile(join(directory, 'owner.json')), {code:'ENOENT'});
    }));
  }
});

test('A19 lower build retains journal and lane on failed cleanup and excludes inherited builders', () => builderFixture(async ({directory, state, options}) => {
  const lease = await acquireDevelopmentBudget({directory});
  state.peers = [builderPeer()];
  const peerSnapshot = structuredClone(state.peers);
  const lowerOptions = {...options, token:lease.token, limits:{cpus:1, memoryMiB:1536}};
  await assert.rejects(managedBuild(async () => {
    await assert.rejects(managedBuild(async () => assert.fail('concurrent lower build executed'), lowerOptions), /execution lane is occupied/);
    state.failRemoval = true;
    throw new Error('original lower build failed');
  }, lowerOptions), error => {
    assert(error instanceof AggregateError);
    assert.match(error.message, /cleanup incomplete/);
    assert(error.errors.some(cause => /original lower build failed/.test(cause.message)));
    return true;
  });
  assert.equal(state.builder, true);
  assert.equal((await readdir(join(directory, 'resources'))).length, 1);
  assert((await readdir(directory)).includes('builder-lane'));
  await assert.rejects(lease.release(), /verified cleanup/);
  await assert.rejects(managedBuild(async () => assert.fail('retained lane reused'), lowerOptions), /execution lane is occupied/);
  assert.equal(state.commands.filter(args => args[0] === 'buildx' && args[1] === 'create').length, 1);
  assert.deepEqual(state.peers, peerSnapshot);
}));

test('A21 explicit builder CPUs above two and peer CPU totals do not override memory or isolation', () => builderFixture(async ({directory, state, options}) => {
  state.peers = [builderPeer(8, 512)];
  const peerSnapshot = structuredClone(state.peers);
  await managedBuild(async () => {}, {...options, limits:{cpus:4, memoryMiB:1536}});
  assert.deepEqual(state.requestedHost, {Memory:1536 * 1024**2, CpuQuota:400000, CpuPeriod:100000});
  assert.deepEqual(state.peers, peerSnapshot);
  assert.equal(state.builder, false);
  await assert.rejects(readFile(join(directory, 'owner.json')), {code:'ENOENT'});
}));

test('A22 higher CPU requests retain finite container limits and the separate aggregate memory ceiling', () => {
  const allocations = {first:{cpus:8,memoryMiB:512}, second:{cpus:4,memoryMiB:512}};
  const limits = composeResourceLimits(['first','second'], 'owner', allocations);
  assert.equal(limits.services.first.cpus, 8);
  assert.equal(limits.services.second.cpus, 4);
  assert.equal(limits.services.first.labels['tempo.resource-owner'], 'owner');
  const peer = builderPeer(8, 1024);
  const docker = args => args[0] === 'ps' ? peer.Id : JSON.stringify([peer]);
  assert.doesNotThrow(() => assertDevelopmentCapacity(['first','second'], 'new-project', docker, allocations));
  assert.throws(() => composeResourceLimits(['first','second'], 'owner', {...allocations,second:{cpus:4,memoryMiB:1537}}), /aggregate.*memory budget/);
  for (const cpus of [0, -1, NaN, Infinity, '4']) {
    const invalid = {first:{cpus,memoryMiB:512}};
    assert.throws(() => composeResourceLimits(['first'], 'owner', invalid), /positive finite/);
    assert.throws(() => assertDevelopmentCapacity(['first'], 'new-project', docker, invalid), /positive finite/);
  }
  for (const HostConfig of [{NanoCpus:Infinity,Memory:512*1024**2}, {NanoCpus:1e9,Memory:0}])
    assert.throws(() => assertDevelopmentCapacity([], 'project', args => args[0] === 'ps' ? 'peer' : JSON.stringify([{HostConfig}]), allocations), /unbounded resources/);
});

test('A23 higher CPU builders still defer on active or unavailable Study and release only their own lane', async t => {
  for (const host of [{available:true,active:true}, {available:false,active:false}]) {
    await t.test(JSON.stringify(host), () => builderFixture(async ({directory, state, options}) => {
      const controller = new AbortController();
      let waits = 0;
      await assert.rejects(managedBuild(async () => assert.fail('Study protection was bypassed'), {
        ...options, limits:{cpus:4,memoryMiB:1536}, quiet:() => waitForStudyQuiet({
          hostActivity:async () => host, legacyAbsent:true, signal:controller.signal,
          pause:async () => { waits++; controller.abort(new Error('Study remained protected')); },
        }),
      }), /Study remained protected/);
      assert.equal(waits, 1);
      assert.deepEqual(state.commands, []);
      await assert.rejects(readFile(join(directory, 'owner.json')), {code:'ENOENT'});
    }));
  }
});

test('A24 retained unlabelled Learning and PostgreSQL still block a 1536 MiB builder on memory', () => {
  const peers = [builderPeer(), {...builderPeer(),Id:'second-retained-peer'}];
  const snapshot = structuredClone(peers);
  const docker = args => args[0] === 'ps' ? peers.map(peer=>peer.Id).join('\n') : JSON.stringify(peers);
  assert.throws(() => assertDevelopmentCapacity([], '__builder', docker, undefined, {cpus:1,memoryMiB:1536}), /aggregate.*memory budget/);
  assert.doesNotThrow(() => assertDevelopmentCapacity([], '__builder', docker, undefined, {cpus:8,memoryMiB:1024}));
  assert.deepEqual(peers, snapshot);
});

test('A25 development command preserves caller compiler fanout and otherwise uses standard runtime defaults', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'tempo-compiler-fanout-'));
  const names = ['CARGO_BUILD_JOBS','CMAKE_BUILD_PARALLEL_LEVEL','RAYON_NUM_THREADS','UV_CONCURRENT_BUILDS'];
  const saved = Object.fromEntries([...names,'TEMPO_DEVELOPMENT_BUDGET_TOKEN'].map(name=>[name,process.env[name]]));
  let acquisitions = 0, releases = 0;
  try {
    delete process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN;
    for (const values of [undefined, ['4','3','6','2']]) {
      names.forEach((name,index) => { if(values)process.env[name]=values[index];else delete process.env[name]; });
      const output = join(directory, 'environment.json');
      const source = `require('node:fs').writeFileSync(${JSON.stringify(output)},JSON.stringify(Object.fromEntries(${JSON.stringify(names)}.map(name=>[name,process.env[name]??null]))));`;
      const code = await runWithDevelopmentBudget(process.execPath, ['-e', source], {
        quiet:async () => {}, acquire:async () => {
          acquisitions++;
          return {token:'synthetic-test-owner',directory,release:async () => { releases++; }};
        },
      });
      assert.equal(code, 0);
      assert.deepEqual(JSON.parse(await readFile(output,'utf8')), Object.fromEntries(names.map((name,index)=>[name,values?.[index]??null])));
    }
    assert.equal(acquisitions, 2);
    assert.equal(releases, 2);
  } finally {
    for (const [name,value] of Object.entries(saved)) { if(value===undefined)delete process.env[name];else process.env[name]=value; }
    await rm(directory,{recursive:true,force:true});
  }
});

test('A26 missing or invalid daemon CPU allocation fails without a fallback builder', async t => {
  for(const daemonCpus of [0, -1, null, '8', true]) {
    await t.test(JSON.stringify(daemonCpus), () => builderFixture(async ({directory,state,options}) => {
      state.daemonCpus = daemonCpus;
      await assert.rejects(managedBuild(async () => assert.fail('invalid daemon allocation executed'), options), /Managed build limits/);
      assert.deepEqual(state.commands.map(args=>args[0]), ['info']);
      await assert.rejects(readFile(join(directory,'owner.json')), {code:'ENOENT'});
    }));
  }
});

test('A27 production web image leaves compiler fanout to standard runtime defaults', () => {
  const dockerfile = readFileSync(new URL('../../Dockerfile.web',import.meta.url),'utf8');
  const environment = dockerfile.split('\n').filter(line=>/^ENV\s/.test(line)).join('\n');
  assert.doesNotMatch(environment, /\b(?:CARGO_BUILD_JOBS|RAYON_NUM_THREADS|CMAKE_BUILD_PARALLEL_LEVEL)\b/);
});

test('A20 Compose build forwards explicit limits and preserves build failures after verified cleanup', () => builderFixture(async ({directory, state, options}) => {
  state.peers = [builderPeer()];
  const peerSnapshot = structuredClone(state.peers);
  const failure = new Error('lower-memory build failed');
  let builds = 0;
  await assert.rejects(managedComposeBuild(async () => JSON.stringify({target:{app:{context:'.'}}}), async args => {
    builds++;
    assert.deepEqual(args.slice(0, 4), ['buildx', 'bake', '--builder', state.name]);
    throw failure;
  }, {...options, limits:{cpus:1, memoryMiB:1536}}), error => error === failure);
  assert.equal(builds, 1);
  assert.deepEqual(state.requestedHost, {Memory:1536 * 1024**2, CpuQuota:100000, CpuPeriod:100000});
  assert.equal(state.builder, false);
  assert.equal(state.container, false);
  assert.deepEqual(state.peers, peerSnapshot);
  await assert.rejects(readFile(join(directory, 'owner.json')), {code:'ENOENT'});
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

test('registered Stockfish container qualification acquires Docker admission while listing remains lightweight', async () => {
  const {componentSelectionNeedsBudget}=await import('../../tools/component-build/component-tests.mjs');
  assert.equal(componentSelectionNeedsBudget(['--component','stockfish']),true);
  assert.equal(componentSelectionNeedsBudget(['--component','stockfish','--list']),false);
});

test('expected independent control cannot be mistaken for absence before its first successful publication', async () => {
  const directory=await mkdtemp(join(tmpdir(),'tempo-expected-activity-'));
  const now=100_000_000_000n;
  try {
    assert.equal((await readHostStudyActivity({directory,now,expectedSources:['learning-a']})).available,false);
    await writeFile(join(directory,'owner.json'),JSON.stringify({version:1,producer:'owner',source:'learning-a',pendingRequests:false,validUntilNs:String(now+15_000_000_000n),sessions:{}}));
    assert.deepEqual(await readHostStudyActivity({directory,now,expectedSources:['learning-a']}),{available:true,active:false});
    assert.equal((await readHostStudyActivity({directory,now,expectedSources:['learning-a','learning-b']})).available,false);
    assert.equal((await readHostStudyActivity({directory,now,expectedSources:'malformed'})).available,false);
  } finally {await rm(directory,{recursive:true,force:true});}
});

test('managed Docker capability survives a sanitized worker environment and cannot outlive its owner', async () => {
  const {managedDockerEnvironment}=await import('../../scripts/development-docker.mjs');
  const {spawnSync}=await import('node:child_process');
  const {mkdir}=await import('node:fs/promises');
  const directory=await mkdtemp(join(tmpdir(),'tempo-worker-capability-'));
  const lock=join(directory,'lease');let lease;
  try {
    lease=await acquireDevelopmentBudget({directory:lock});
    const executable=join(directory,'real-docker'),log=join(directory,'calls.jsonl');
    await writeFile(executable,`#!${process.execPath}\nimport {appendFileSync}from'node:fs';const args=process.argv.slice(2);appendFileSync(${JSON.stringify(log)},JSON.stringify(args)+'\\n');if(args[0]==='container'&&args[1]==='inspect'){console.error('No such container: '+args.at(-1));process.exit(1);}if(args[0]==='info')console.log('8');if(args[0]==='run')console.log(JSON.stringify(args));\n`,{mode:0o700});
    const adapter=join(directory,'adapter');await mkdir(adapter);
    const environment=managedDockerEnvironment({...process.env,TEMPO_REAL_DOCKER:executable,
      TEMPO_DEVELOPMENT_BUDGET_TOKEN:lease.token,TEMPO_DEVELOPMENT_BUDGET_DIRECTORY:lock,
      TEMPO_STUDY_ACTIVITY_LEGACY:'absent',TEMPO_STUDY_ACTIVITY_DIRECTORY:join(directory,'activity'),TEMPO_STUDY_ACTIVITY_EXPECTED_SOURCES:'[]'},adapter);
    // The installed Stockfish client intentionally forwards no domain or runner environment.
    const sanitized={PATH:environment.PATH,HOME:process.env.HOME};
    const wrapper=join(adapter,'managed-bin/docker');
    const result=spawnSync(wrapper,['run','--name','owned','image'],{env:sanitized,encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);
    assert.equal(result.stderr,'','Expected absence probes must not corrupt the worker diagnostic stream');
    const args=JSON.parse(result.stdout);assert(args.includes('tempo.resource-owner='+lease.token));assert(args.includes('--cpus'));assert(args.includes('--memory'));assert.equal(args[args.indexOf('--cpus')+1],'8');
    const before=await readFile(log,'utf8');await lease.release();lease=undefined;
    const stale=spawnSync(wrapper,['run','image'],{env:sanitized,encoding:'utf8'});
    assert.notEqual(stale.status,0);assert.equal(await readFile(log,'utf8'),before,'A released capability invoked Docker');
  }finally{await lease?.release();await rm(directory,{recursive:true,force:true});}
});


test('A12 an inherited managed container cannot consume the active builder allocation', () => builderFixture(async ({directory,state,options}) => {
  const {managedDockerEnvironment}=await import('../../scripts/development-docker.mjs');
  const {mkdir}=await import('node:fs/promises');
  const {spawnSync}=await import('node:child_process');
  const lease=await acquireDevelopmentBudget({directory});
  const parent=join(directory,'adapter');await mkdir(parent);
  const executable=join(parent,'docker'),log=join(parent,'calls');
  await writeFile(executable,`#!${process.execPath}\nimport{appendFileSync}from'node:fs';appendFileSync(${JSON.stringify(log)},'unexpected Docker call');\n`,{mode:0o700});
  const environment=managedDockerEnvironment({...process.env,TEMPO_REAL_DOCKER:executable,TEMPO_DEVELOPMENT_BUDGET_TOKEN:lease.token,TEMPO_DEVELOPMENT_BUDGET_DIRECTORY:directory,TEMPO_STUDY_ACTIVITY_LEGACY:'absent',TEMPO_STUDY_ACTIVITY_DIRECTORY:join(parent,'activity'),TEMPO_STUDY_ACTIVITY_EXPECTED_SOURCES:'[]'},parent);
  try{
    await managedBuild(async()=>{
      const result=spawnSync(join(parent,'managed-bin/docker'),['run','image'],{env:environment,encoding:'utf8'});
      assert.notEqual(result.status,0);assert.match(result.stderr,/builder execution lane is occupied/);
      await assert.rejects(readFile(log),{code:'ENOENT'});
    },{...options,token:lease.token});
    assert.equal(state.builder,false);
  }finally{await lease.release();}
}));

test('A13 separate inherited Docker processes serialize admission until the allocation is visible',async()=>{
  const {managedDockerEnvironment}=await import('../../scripts/development-docker.mjs');
  const {mkdir}=await import('node:fs/promises');
  const parent=await mkdtemp(join(tmpdir(),'tempo-admission-race-'));
  const directory=join(parent,'lease');const lease=await acquireDevelopmentBudget({directory});
  try{
    const executable=join(parent,'real-docker'),state=join(parent,'container.json'),events=join(parent,'events.jsonl');
    await writeFile(executable,`#!${process.execPath}
import{readFileSync,writeFileSync,appendFileSync,rmSync}from'node:fs';
const args=process.argv.slice(2),state=${JSON.stringify(state)},events=${JSON.stringify(events)};
const get=()=>{try{return JSON.parse(readFileSync(state));}catch{return undefined;}};
if(args[0]==='ps'){if(get())console.log('owned');}
else if(args[0]==='inspect')console.log(JSON.stringify([get()]));
else if(args[0]==='container'&&args[1]==='inspect'){const value=get();if(value&&value.Name===args.at(-1))console.log(JSON.stringify([value]));else{console.error('No such container: '+args.at(-1));process.exitCode=1;}}
else if(args[0]==='run'){
 const name=args[args.indexOf('--name')+1],owner=args.find(value=>value.startsWith('tempo.resource-owner=')).split('=')[1];
 appendFileSync(events,JSON.stringify({event:'start',name})+'\\n');
 await new Promise(resolve=>setTimeout(resolve,150));
 writeFileSync(state,JSON.stringify({Name:name,State:{Running:true},Config:{Labels:{'tempo.resource-owner':owner}},HostConfig:{NanoCpus:1.5e9,Memory:1536*1024**2}}));
 await new Promise(resolve=>setTimeout(resolve,1200));rmSync(state,{force:true});appendFileSync(events,JSON.stringify({event:'end',name})+'\\n');
}else throw new Error(JSON.stringify(args));`,{mode:0o700});
    const adapter=join(parent,'adapter');await mkdir(adapter);
    const environment=managedDockerEnvironment({...process.env,TEMPO_REAL_DOCKER:executable,TEMPO_DEVELOPMENT_BUDGET_TOKEN:lease.token,TEMPO_DEVELOPMENT_BUDGET_DIRECTORY:directory,TEMPO_STUDY_ACTIVITY_LEGACY:'absent',TEMPO_STUDY_ACTIVITY_DIRECTORY:join(parent,'activity'),TEMPO_STUDY_ACTIVITY_EXPECTED_SOURCES:'[]'},adapter);
    const run=name=>new Promise((resolve,reject)=>{const child=spawn(join(adapter,'managed-bin/docker'),['run','--rm','--cpus','1.5','--memory','1536m','--name',name,'image'],{env:environment,stdio:['ignore','pipe','pipe']});let stderr='';child.stderr.on('data',part=>stderr+=part);child.once('error',reject);child.once('exit',code=>resolve({code,stderr}));});
    const results=await Promise.all([run('first'),run('second')]);
    assert.deepEqual(results.map(result=>result.code).sort(),[0,1]);
    assert.match(results.find(result=>result.code!==0).stderr,/exceed the aggregate/);
    const records=(await readFile(events,'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    assert.deepEqual(records.map(record=>record.event),['start','end']);
    assert.deepEqual(await readdir(join(directory,'resources')),[]);
  }finally{await lease.release();await rm(parent,{recursive:true,force:true});}
});

test('saturation qualification listing and invalid destinations remain lightweight during another owner',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'tempo-saturation-list-'));
  try{
    const {spawnSync}=await import('node:child_process');
    const executable=join(directory,'docker'),calls=join(directory,'calls');
    await writeFile(executable,`#!${process.execPath}\nimport{writeFileSync}from'node:fs';writeFileSync(${JSON.stringify(calls)},'unexpected external effect');process.exit(1);\n`,{mode:0o700});
    const environment={...process.env,PATH:directory+':'+process.env.PATH};delete environment.TEMPO_DEVELOPMENT_BUDGET_TOKEN;
    const entry=new URL('../../scripts/study-priority-qualification/index.mjs',import.meta.url);
    const listed=spawnSync(process.execPath,[entry.pathname,'--list'],{env:environment,encoding:'utf8',timeout:3000});
    assert.equal(listed.status,0,listed.stderr);assert.equal(JSON.parse(listed.stdout).scenarios.length,7);
    const invalid=spawnSync(process.execPath,[entry.pathname,'--evidence','relative'],{env:environment,encoding:'utf8',timeout:3000});
    assert.notEqual(invalid.status,0);assert.match(invalid.stderr,/absolute private evidence directory/);
    await assert.rejects(readFile(calls),{code:'ENOENT'});
  }finally{await rm(directory,{recursive:true,force:true});}
});

test('qualification cleanup reaps a recorded owned process and refuses a foreign replacement',async()=>{
  const {reconcileRecordedHostProcesses}=await import('../../scripts/study-priority-qualification/process-lifecycle.mjs');
  const {execFileSync}=await import('node:child_process');
  const {randomUUID}=await import('node:crypto');
  const directory=await mkdtemp(join(tmpdir(),'tempo-owned-process-proof-'));
  const token=randomUUID();const children=[];
  async function launch(owner,nonce){
    const child=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});console.log('ready');setInterval(()=>{},1000);"],{
      cwd:directory,env:{...process.env,TEMPO_SATURATION_DIRECTORY:directory,TEMPO_DEVELOPMENT_BUDGET_TOKEN:owner,TEMPO_QUALIFICATION_PROCESS_NONCE:nonce},stdio:['ignore','pipe','pipe']});
    children.push(child);child.completion=new Promise(resolve=>child.once('exit',resolve));
    await new Promise((resolve,reject)=>{child.once('error',reject);child.stdout.once('data',()=>resolve());});
    return {child,record:{pid:child.pid,nonce,token,directory,start_time:execFileSync('ps',['-p',String(child.pid),'-o','lstart='],{encoding:'utf8',env:{...process.env,TZ:'UTC',LC_ALL:'C'}}).trim()}};
  }
  try{
    const nonce=randomUUID().replaceAll('-','');
    const owned=await launch(token,nonce);
    const result=await reconcileRecordedHostProcesses([owned.record],{directory,token},{graceMilliseconds:75,killMilliseconds:1000});
    assert.deepEqual(result[0].signals,['SIGTERM','SIGKILL']);assert.equal(result[0].absent,true);await owned.child.completion;
    const foreign=await launch('another-owner',randomUUID().replaceAll('-',''));
    await assert.rejects(reconcileRecordedHostProcesses([{...foreign.record,nonce}],{directory,token},{graceMilliseconds:75,killMilliseconds:1000}),/ownership changed/);
    await assert.rejects(reconcileRecordedHostProcesses([{...foreign.record,nonce,exited:true}],{directory,token}),/ownership changed/);
    assert.doesNotThrow(()=>process.kill(foreign.child.pid,0),'A foreign process was signalled');
    await assert.rejects(reconcileRecordedHostProcesses([{...owned.record,pid:null}],{directory,token}),/creation is unverified/);
  }finally{
    for(const child of children){if(child.exitCode===null&&child.signalCode===null)child.kill('SIGKILL');await child.completion;}
    await rm(directory,{recursive:true,force:true});
  }
});

test('failed startup journal reconciles only an explicitly removed absent container',async()=>{
  const {managedDockerEnvironment}=await import('../../scripts/development-docker.mjs');
  const {recordBudgetResource}=await import('../../scripts/development-budget.mjs');
  const {spawnSync}=await import('node:child_process');
  const directory=await mkdtemp(join(tmpdir(),'tempo-absent-container-cleanup-'));
  const lock=join(directory,'lease');let lease;
  try{
    lease=await acquireDevelopmentBudget({directory:lock});
    const executable=join(directory,'fake-docker');
    await writeFile(executable,`#!${process.execPath}\nconst args=process.argv.slice(2);if(args[0]==='container'&&args[1]==='inspect'){console.error('error: no such object: '+args.at(-1));process.exit(1);}if(args[0]==='rm'){console.error('No such container: '+args.at(-1));process.exit(1);}throw new Error('unexpected fake Docker call');\n`,{mode:0o700});
    const environment=managedDockerEnvironment({...process.env,TEMPO_REAL_DOCKER:executable,
      TEMPO_DEVELOPMENT_BUDGET_TOKEN:lease.token,TEMPO_DEVELOPMENT_BUDGET_DIRECTORY:lock},directory);
    for(const name of ['absent-owned','other-owned'])await recordBudgetResource('container:'+name,true,lease.token,lock);
    const wrapper=join(directory,'managed-bin/docker');
    const result=spawnSync(wrapper,['rm','--force','absent-owned'],{env:environment,encoding:'utf8',timeout:3000});
    assert.equal(result.status,1,'The raw failed rm remains visible to its caller');
    assert.match(result.stderr,/No such container: absent-owned/);
    const remaining=await readdir(join(lock,'resources'));
    assert.deepEqual(remaining,[encodeURIComponent('container:other-owned')]);
    const other=spawnSync(wrapper,['rm','--force','other-owned'],{env:environment,encoding:'utf8',timeout:3000});
    assert.equal(other.status,1);assert.deepEqual(await readdir(join(lock,'resources')),[]);
    await lease.release();lease=undefined;
  }finally{await lease?.release();await rm(directory,{recursive:true,force:true});}
});

test('stalled or removed expected host publisher cannot turn stale evidence into admitted inactivity', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'tempo-stalled-activity-'));
  const now = 100_000_000_000n;
  const path = join(directory, 'owner.json');
  const expectedSources = ['learning-a'];
  try {
    await writeFile(path, JSON.stringify({version:1, producer:'owner', source:'learning-a', pendingRequests:false,
      validUntilNs:String(now + 15_000_000_000n), sessions:{}}));
    assert.deepEqual(await readHostStudyActivity({directory, now, expectedSources}), {available:true, active:false});
    assert.equal((await readHostStudyActivity({directory, now:now + 15_000_000_000n, expectedSources})).available, false);
    await rm(path);
    assert.equal((await readHostStudyActivity({directory, now, expectedSources})).available, false);
    let waits = 0;
    const controller = new AbortController();
    await assert.rejects(waitForStudyQuiet({legacyAbsent:true, signal:controller.signal,
      hostActivity:() => readHostStudyActivity({directory, now, expectedSources}),
      pause:async () => { waits++; controller.abort(new Error('expected publisher unavailable')); }}), /expected publisher unavailable/);
    assert.equal(waits, 1);
  } finally { await rm(directory, {recursive:true, force:true}); }
});


test('healthy idle snapshot with stalled publisher allows a new essential request before the 15-second admission expiry', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'tempo-idle-stall-boundary-'));
  const child = spawn(process.env.TEMPO_COMPONENT_PYTHON ?? 'python3',
    [fileURLToPath(new URL('./host-activity-idle-stall.py', import.meta.url)), directory],
    {stdio:['pipe', 'pipe', 'pipe']});
  const exited = once(child, 'close').catch(error => [null, String(error)]);
  const lines = createInterface({input:child.stdout});
  let errors = '';
  child.stderr.on('data', data => { errors += data; });
  try {
    const [line] = await once(lines, 'line', {signal:AbortSignal.timeout(3000)});
    const status = JSON.parse(line);
    assert.equal(status.essentialCompleted, true);
    assert.equal(status.publisherBlocked, true);
    const source = fileURLToPath(new URL('../../services/learning/runtime/src/tempo_learning_runtime/host_activity.py', import.meta.url));
    assert.equal(status.sourcePath, source);
    assert.equal(status.sourceSHA256, createHash('sha256').update(await readFile(source)).digest('hex'));
    const now = BigInt(status.now);
    const expectedSources = ['learning-idle-stall'];
    const retained = JSON.parse(await readFile(join(directory, status.producer + '.json'), 'utf8'));
    assert.equal(retained.pendingRequests, false, 'The completed essential request has not reached disk');
    assert.deepEqual(retained.sessions, {});
    assert.deepEqual(await readHostStudyActivity({directory, now, expectedSources}), {available:true, active:false});
    assert.deepEqual(await readHostStudyActivity({directory, now:now + 15_000_000_000n - 1n, expectedSources}),
      {available:true, active:false}, 'Async publication does not immediately exclude foreground overlap');
    assert.equal((await readHostStudyActivity({directory, now:now + 15_000_000_000n, expectedSources})).available, false);
    child.stdin.end('close\n');
    const [code, signal] = await exited;
    assert.equal(code, 0, errors || String(signal));
    assert.equal((await readHostStudyActivity({directory, now, expectedSources})).available, false);
    assert.deepEqual(await readHostStudyActivity({directory, now, expectedSources:[]}), {available:true, active:false},
      'Absent-source protection requires configured expected identities; default empty identities allow absence');
  } finally {
    lines.close();
    if (child.exitCode === null) child.kill('SIGKILL');
    await exited;
    await rm(directory, {recursive:true, force:true});
  }
});
