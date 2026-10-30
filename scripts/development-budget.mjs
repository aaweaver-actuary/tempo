// One host-wide owner for newly managed heavy work. Never stop another task's resources.
import { mkdir, mkdtemp, readFile, writeFile, rm, readdir } from 'node:fs/promises';
import { readFileSync, writeFileSync, rmSync, mkdirSync, existsSync } from 'node:fs';
import { tmpdir, setPriority } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { execFile, execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { readHostStudyActivity } from './host-study-activity.mjs';

/** @typedef {{cpus?: number, memoryMiB: number, pidsLimit?: number}} DevelopmentAllocation */
export const resourceBudgets = JSON.parse(readFileSync(new URL('../config/resource-budgets.json', import.meta.url), 'utf8'));
const defaultLockDirectory = join(tmpdir(), 'tempo-development-resource-budget-v1.lock');
const budgetDirectory = () => process.env.TEMPO_DEVELOPMENT_BUDGET_DIRECTORY ?? defaultLockDirectory;
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const execute = promisify(execFile);

export function recordBudgetResourceSync(key, present, token = process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN, directory = budgetDirectory()) {
  if (!token) throw new Error('A resource must have a development budget owner');
  const owner = JSON.parse(readFileSync(join(directory, 'owner.json'), 'utf8'));
  if (owner.token !== token) throw new Error('Development resource ownership changed');
  const file = join(directory, 'resources', encodeURIComponent(key));
  if (present) writeFileSync(file, JSON.stringify({ token, pid: process.pid, key }), { mode: 0o600 });
  else rmSync(file, { force: true });
}
export async function recordBudgetResource(key,present,token,directory) {recordBudgetResourceSync(key,present,token,directory);}

async function requireBudgetOwner(directory, token) {
  const owner = JSON.parse(await readFile(join(directory, 'owner.json'), 'utf8'));
  if (!token || owner.token !== token) throw new Error('Development resource ownership changed; retaining its lock');
  return owner;
}

export async function validateDevelopmentBudgetOwner({ directory = budgetDirectory(), token = process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN } = {}) {
  return requireBudgetOwner(directory, token);
}

// Serialize admission until Docker exposes the new allocation. Execution may
// overlap after this short section; it must still fit the aggregate allocation.
export async function acquireDevelopmentAdmission({ directory = budgetDirectory(), token = process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN } = {}) {
  const lane = join(directory, 'admission-lane');
  for (;;) {
    await requireBudgetOwner(directory, token);
    try { await mkdir(lane, { mode: 0o700 }); break; }
    catch (error) { if (error.code !== 'EEXIST') throw error; await wait(20); }
  }
  return { release: async () => { await requireBudgetOwner(directory, token); await rm(lane, { recursive: true }); } };
}

const defaultDocker = async args => (await execute('docker', args, { maxBuffer: 1024 * 1024 })).stdout;

/** Omission means unrestricted; explicit caps retain exact quota precision. */
export function validateDevelopmentCpuLimit(cpus) {
  if (cpus !== undefined && (!Number.isFinite(cpus) || !(cpus > 0) || !Number.isSafeInteger(cpus * 100000)))
    throw new Error('CPU limits require positive finite CPUs with an exact CPU quota');
  return cpus;
}

function unrestrictedCpu(host) {
  return (host.NanoCpus === undefined || host.NanoCpus === 0) &&
    (host.CpuQuota === undefined || [0, -1].includes(host.CpuQuota));
}

/** Explicit or selected daemon allocation; never an idle CPU or Study safety claim. */
export async function readDevelopmentCpuAllocation({ cpus, docker = defaultDocker } = {}) {
  if (cpus === undefined) cpus = JSON.parse(await docker(['info', '--format', '{{json .NCPU}}']));
  if (!Number.isFinite(cpus) || !(cpus > 0) || !Number.isSafeInteger(cpus * 100000))
    throw new Error('Managed build limits require a positive finite daemon CPU allocation with an exact CPU quota');
  return cpus;
}

async function inspectBuilder(name, docker) {
  try { return await docker(['buildx', 'inspect', name, '--timeout', '5s']); }
  catch (error) {
    const diagnostic = String(error.stderr ?? error.message);
    if (diagnostic.includes(name) && /no builder|no such builder|failed to find instance/i.test(diagnostic)) return undefined;
    throw error;
  }
}

async function inspectBuilderContainer(name, docker) {
  try { return JSON.parse(await docker(['container', 'inspect', name]))[0]; }
  catch (error) {
    if (String(error.stderr ?? error.message).includes(name) && /No such (?:object|container)/i.test(String(error.stderr ?? error.message))) return undefined;
    throw error;
  }
}

/** Reconcile only a journaled identity; a successful removal command is not absence proof. */
export async function reconcileDevelopmentBuilder(name, { directory = budgetDirectory(), token = process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN, docker = defaultDocker } = {}) {
  await requireBudgetOwner(directory, token);
  const key = `builder:${name}`;
  const recorded = JSON.parse(await readFile(join(directory, 'resources', encodeURIComponent(key)), 'utf8'));
  if (recorded.token !== token || recorded.key !== key) throw new Error('Builder journal ownership mismatch');
  const containerName = `buildx_buildkit_${name}0`;
  const checkContainerOwner = container => {
    if (container && !container.Config?.Env?.includes(`TEMPO_RESOURCE_OWNER=${token}`))
      throw new Error(`Builder container ownership mismatch: ${containerName}`);
  };
  const builder = await inspectBuilder(name, docker);
  checkContainerOwner(await inspectBuilderContainer(containerName, docker));
  if (builder !== undefined) {
    if (!builder.includes('env.TEMPO_RESOURCE_OWNER') || !builder.includes(token))
      throw new Error(`Builder ownership mismatch: ${name}`);
    // A disconnected rm may already have succeeded. Reconcile its result too.
    try { await docker(['buildx', 'rm', name]); } catch { /* Absence probes below own the decision. */ }
  }
  if (await inspectBuilder(name, docker) !== undefined)
    throw new Error(`Builder ${name} remains; retain ${directory} and reconcile this exact identity`);
  const container = await inspectBuilderContainer(containerName, docker);
  checkContainerOwner(container);
  if (container) {
    try { await docker(['container', 'rm', '--force', '--volumes', containerName]); } catch { /* Verify independently. */ }
  }
  if (await inspectBuilder(name, docker) !== undefined || await inspectBuilderContainer(containerName, docker) !== undefined)
    throw new Error(`Builder resource absence is unverified: ${name}; retain ${directory}`);
  await requireBudgetOwner(directory, token);
  await recordBudgetResource(key, false, token, directory);
}

function snapshotBuildLimits(limits) {
  const { cpus, memoryMiB } = limits ?? {};
  validateDevelopmentCpuLimit(cpus);
  if (!Number.isSafeInteger(memoryMiB) || !(memoryMiB > 0))
    throw new Error('Managed build limits require positive integer memoryMiB');
  if (memoryMiB > resourceBudgets.development.memoryMiB)
    throw new Error('Managed build limits exceed the aggregate development memory budget');
  return Object.freeze({ cpus, memoryMiB });
}

export function assertDevelopmentCapacity(requestedServices, project, docker = args =>
  execFileSync('docker', args, { encoding: 'utf8' }), allocations = resourceBudgets.developmentServices,
  builderLimits) {
  const build = project === '__builder' && builderLimits !== undefined ? snapshotBuildLimits(builderLimits) : undefined;
  const ids = docker(['ps', '--filter', 'label=tempo.resource-class=development', '--format', '{{.ID}}']).trim().split(/\s+/).filter(Boolean);
  const existing = ids.length ? JSON.parse(docker(['inspect', ...ids])) : [];
  let memory = project === '__builder' ? build?.memoryMiB ?? resourceBudgets.development.memoryMiB : 0;
  for (const container of existing) {
    const host = container.HostConfig;
    const limit = host.NanoCpus / 1e9 || host.CpuQuota / host.CpuPeriod;
    const validCpu = unrestrictedCpu(host) || Number.isFinite(limit) && limit > 0;
    if (!validCpu || !Number.isFinite(host.Memory) || !(host.Memory > 0))
      throw new Error('An existing managed development container has unbounded resources');
    memory += host.Memory / 1024 / 1024;
  }
  for (const name of new Set(requestedServices)) {
    if (existing.some(container => container.Config.Labels['com.docker.compose.project'] === project &&
      container.Config.Labels['com.docker.compose.service'] === name)) continue;
    const limit = allocations[name];
    if (!limit) throw new Error(`No development resource allocation for ${name}`);
    validateDevelopmentAllocation(limit);
    memory += limit.memoryMiB;
  }
  if (memory > resourceBudgets.development.memoryMiB)
    throw new Error('New development work would exceed the aggregate study-protection memory budget; existing resources were left untouched');
}

function validateDevelopmentAllocation(limit) {
  validateDevelopmentCpuLimit(limit.cpus);
  if (!Number.isFinite(limit.memoryMiB) || !(limit.memoryMiB > 0))
    throw new Error('Development allocations require positive finite memory limits');
  if (limit.pidsLimit !== undefined && (!Number.isSafeInteger(limit.pidsLimit) || limit.pidsLimit <= 0))
    throw new Error('Development PID limits require a positive integer');
}

const synchronousDocker = args => execFileSync('docker', args, {encoding:'utf8',timeout:5000});
function requireSynchronousBudgetOwner(directory, token) {
  const owner=JSON.parse(readFileSync(join(directory,'owner.json'),'utf8'));
  if(!token||owner.token!==token)throw new Error('Development resource ownership changed; retaining its lock');
}
function startupPaths(project,directory) {
  if(!/^[a-z0-9][a-z0-9_-]*$/.test(project))throw new Error('Invalid owned Compose project');
  return {key:'startup:'+project,journal:join(directory,'resources',encodeURIComponent('startup:'+project)),lane:join(directory,'admission-lane')};
}
export function assertDevelopmentProjectOwned(project,{directory=budgetDirectory(),token=process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN,docker=synchronousDocker}={}) {
  requireSynchronousBudgetOwner(directory,token);
  const ids=docker(['ps','-aq','--filter','label=com.docker.compose.project='+project]).trim().split(/\s+/).filter(Boolean);
  const containers=ids.length?JSON.parse(docker(['inspect',...ids])):[];
  if(containers.length!==ids.length)throw new Error('Owned Compose resource inspection is incomplete');
  for(const container of containers)if(container.Config?.Labels?.['tempo.resource-owner']!==token||container.Config?.Labels?.['com.docker.compose.project']!==project)
    throw new Error('Compose cleanup ownership mismatch; retain its journal');
  return containers;
}
function requireStartupClaim(record,project,token) {
  if(record.token!==token||record.project!==project||record.pid!==process.pid)
    throw new Error('Compose startup ownership mismatch; retain admission and journal');
}
/** Same short admission lane as builders/managed containers; callers stay synchronous. */
export function withDevelopmentStartup(services,project,start,{directory=budgetDirectory(),token=process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN,docker=synchronousDocker,allocations=resourceBudgets.developmentServices,oneOff=false}={}) {
  requireSynchronousBudgetOwner(directory,token);
  const paths=startupPaths(project,directory);let ownsLane=false,mutationPossible=false,journalUncertain=false;
  try {
    try{mkdirSync(paths.lane,{mode:0o700});ownsLane=true;}catch(error){if(error.code==='EEXIST')throw new Error('Development startup admission is occupied; finish or reconcile its owner');throw error;}
    writeFileSync(join(paths.lane,'startup-owner.json'),JSON.stringify({token,project,pid:process.pid}),{mode:0o600,flag:'wx'});
    requireSynchronousBudgetOwner(directory,token);
    if(existsSync(join(directory,'builder-lane')))throw new Error('The owned builder execution lane is occupied; no Compose startup may share its allocation');
    assertDevelopmentProjectOwned(project,{directory,token,docker});
    // Compose run creates another allocation even when that service already exists.
    assertDevelopmentCapacity(services,oneOff?'__oneoff:'+randomUUID():project,docker,allocations);
    if(existsSync(paths.journal))requireStartupClaim(JSON.parse(readFileSync(paths.journal,'utf8')),project,token);
    else{journalUncertain=true;writeFileSync(paths.journal,JSON.stringify({key:paths.key,token,project,pid:process.pid}),{mode:0o600,flag:'wx'});journalUncertain=false;}
    mutationPossible=true;const result=start();
    if(result?.error)throw result.error;
    if(result?.status!==0)throw new Error('Owned Compose startup failed; retain admission until verified cleanup');
    const visible=assertDevelopmentProjectOwned(project,{directory,token,docker});
    assertDevelopmentCapacity([],project,docker,allocations);
    requireSynchronousBudgetOwner(directory,token);
    requireStartupClaim(JSON.parse(readFileSync(join(paths.lane,'startup-owner.json'),'utf8')),project,token);
    requireStartupClaim(JSON.parse(readFileSync(paths.journal,'utf8')),project,token);
    if(!visible.length)rmSync(paths.journal);
    rmSync(paths.lane,{recursive:true});ownsLane=false;
    return result;
  }catch(error){
    // Pre-mutation denial can yield. Any possibly issued start remains blocked.
    if(ownsLane&&!mutationPossible&&!journalUncertain){requireSynchronousBudgetOwner(directory,token);const ownerPath=join(paths.lane,'startup-owner.json');if(existsSync(ownerPath))requireStartupClaim(JSON.parse(readFileSync(ownerPath,'utf8')),project,token);rmSync(paths.lane,{recursive:true});}
    throw error;
  }
}
/** Cleanup bypasses admission; only verified absence can release a retained claim. */
export function reconcileDevelopmentStartup(project,{directory=budgetDirectory(),token=process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN,docker=synchronousDocker}={}) {
  const containers=assertDevelopmentProjectOwned(project,{directory,token,docker});
  if(containers.length)throw new Error('Compose resources remain; retain startup ownership');
  const paths=startupPaths(project,directory),journaled=existsSync(paths.journal);
  if(journaled)requireStartupClaim(JSON.parse(readFileSync(paths.journal,'utf8')),project,token);
  if(existsSync(paths.lane)){
    const ownerPath=join(paths.lane,'startup-owner.json');
    if(!existsSync(ownerPath)){if(journaled)throw new Error('Startup admission ownership is unverified; retain its journal');}
    else{
      const owner=JSON.parse(readFileSync(ownerPath,'utf8'));
      if(owner.token!==token)throw new Error('Startup admission ownership mismatch; retain its journal');
      if(owner.project===project){requireStartupClaim(owner,project,token);requireSynchronousBudgetOwner(directory,token);rmSync(paths.lane,{recursive:true});}
    }
  }
  if(journaled){requireSynchronousBudgetOwner(directory,token);rmSync(paths.journal);}
}

export async function managedBuild(build, { docker = defaultDocker, quiet = waitForStudyQuiet,
  directory = budgetDirectory(), token = process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN,
  limits,
  prepare = async path => writeFile(path, '[worker.oci]\n') } = {}) {
  // Validate and copy before acquiring ownership or yielding to caller hooks.
  const requestedLimits = limits === undefined ? undefined : snapshotBuildLimits(limits);
  const defaultMemoryMiB = resourceBudgets.development.memoryMiB;
  const lease = token ? undefined : await acquireDevelopmentBudget({ directory });
  token ??= lease.token;
  const name = `tempo-budget-builder-${randomUUID()}`;
  let journaled = false, ownsLane = false, buildDirectory, result, failure;
  const lane = join(directory, 'builder-lane');
  try {
    await requireBudgetOwner(directory, token);
    const admission = await acquireDevelopmentAdmission({directory, token});
    try {
      try { await mkdir(lane, {mode: 0o700}); ownsLane = true; }
      catch (error) {
        if (error.code === 'EEXIST') throw new Error('The owned builder execution lane is occupied; reconcile or finish it first');
        throw error;
      }
    } finally { await admission.release(); }
    buildDirectory = await mkdtemp(join(tmpdir(), 'tempo-managed-builder-'));
    const buildConfig = join(buildDirectory, 'buildkitd.toml');
    await prepare(buildConfig);
    await quiet();
    const buildLimits = requestedLimits ?? snapshotBuildLimits({memoryMiB: defaultMemoryMiB});
    const cpuPeriod = 100000, cpuQuota = buildLimits.cpus === undefined ? undefined : buildLimits.cpus * cpuPeriod;
    const managedIds = (await docker(['ps', '--filter', 'label=tempo.resource-class=development', '--format', '{{.ID}}'])).trim();
    const managedContainers = managedIds ? await docker(['inspect', ...managedIds.split(/\s+/)]) : '[]';
    assertDevelopmentCapacity([], '__builder', args => args[0] === 'ps' ? managedIds : managedContainers,
      resourceBudgets.developmentServices, buildLimits);
    if (await inspectBuilder(name, docker) !== undefined || await inspectBuilderContainer(`buildx_buildkit_${name}0`, docker) !== undefined)
      throw new Error(`Builder identity already exists: ${name}`);
    await recordBudgetResource(`builder:${name}`, true, token, directory);
    journaled = true;
    await docker(['buildx', 'create', '--name', name, '--driver', 'docker-container', '--buildkitd-config', buildConfig, '--driver-opt', 'image=moby/buildkit:rootless',
      '--driver-opt', `env.TEMPO_RESOURCE_OWNER=${token}`,
      '--driver-opt', `memory=${buildLimits.memoryMiB}m`,
      ...(cpuQuota === undefined ? [] : ['--driver-opt', `cpu-period=${cpuPeriod}`, '--driver-opt', `cpu-quota=${cpuQuota}`]),
      '--driver-opt', 'default-load=true']);
    await docker(['buildx', 'inspect', name, '--bootstrap']);
    const [container] = JSON.parse(await docker(['inspect', `buildx_buildkit_${name}0`]));
    const host = container.HostConfig;
    const cpuMatches = cpuQuota === undefined ? unrestrictedCpu(host)
      : host.CpuQuota === cpuQuota && host.CpuPeriod === cpuPeriod &&
        ((host.NanoCpus ?? 0) === 0 || host.NanoCpus === buildLimits.cpus * 1e9);
    if (!/^1000(?::1000)?$/.test(container.Config?.User ?? "") || host.Memory !== buildLimits.memoryMiB * 1024 * 1024 || !cpuMatches)
      throw new Error('Managed builder kernel limits do not match the development budget');
    result = await build(name);
  } catch (error) { failure = error;
  } finally {
    const errors = [];
    try { if (journaled) await reconcileDevelopmentBuilder(name, { directory, token, docker }); } catch (error) { errors.push(error); }
    try { if (buildDirectory) await rm(buildDirectory, {recursive:true,force:true}); } catch (error) { errors.push(error); }
    try { if (ownsLane && !errors.length) { await requireBudgetOwner(directory, token); await rm(lane, {recursive:true}); } } catch (error) { errors.push(error); }
    try { await lease?.release(); } catch (error) { errors.push(error); }
    if (errors.length) throw new AggregateError([...(failure ? [failure] : []), ...errors],
      `Managed builder cleanup incomplete (${name}); retain ownership at ${directory}: ${errors.map(String).join('; ')}`);
  }
  if (failure) throw failure;
  return result;
}

// Rootless execution inherits the builder container's aggregate cgroup. It
// cannot create privileged sibling groups outside that container's limits.

export async function managedComposeBuild(prepare, executeBuild, options = {}) {
  return managedBuild(async builder => {
    const specification = JSON.parse(await prepare());
    if (!specification.target || !Object.keys(specification.target).length) throw new Error('No selected Compose build targets');
    const directory = await mkdtemp(join(tmpdir(),'tempo-budget-bake-'));
    try {
      const path = join(directory,'build.json'); await writeFile(path,JSON.stringify(specification),{mode:0o600});
      return await executeBuild(['buildx','bake','--builder',builder,'--file',path,'--load']);
    } finally { await rm(directory,{recursive:true,force:true}); }
  }, options);
}

export function composeResourceLimits(services, owner, allocations = resourceBudgets.developmentServices) {
  const limits = Object.fromEntries(services.map(name => {
    const limit = allocations[name];
    if (!limit) throw new Error(`No bounded development allocation for ${name}`);
    validateDevelopmentAllocation(limit);
    return [name, { ...(limit.cpus === undefined ? {} : {cpus: limit.cpus}), mem_limit: `${limit.memoryMiB}m`,
      ...(limit.pidsLimit === undefined ? {} : {pids_limit: limit.pidsLimit}),
      labels: { 'tempo.resource-owner': owner, 'tempo.resource-class': 'development' } }];
  }));
  const memory = services.reduce((sum, name) => sum + allocations[name].memoryMiB, 0);
  if (memory > resourceBudgets.development.memoryMiB)
    throw new Error(`Development allocation exceeds the aggregate ${resourceBudgets.development.memoryMiB}-MiB memory budget`);
  return { services: limits };
}

export async function waitForStudyQuiet({ activityUrl = process.env.TEMPO_STUDY_ACTIVITY_URL ?? 'http://127.0.0.1:8000/api/system/foreground-active',
  read = fetch, pause = wait, signal, hostActivity = readHostStudyActivity,
  legacyAbsent = process.env.TEMPO_STUDY_ACTIVITY_LEGACY === 'absent' ||
    process.env.GITHUB_ACTIONS === 'true' && process.env.RUNNER_ENVIRONMENT === 'github-hosted' && !process.env.TEMPO_STUDY_ACTIVITY_URL } = {}) {
  for (;;) {
    signal?.throwIfAborted();
    const host = await hostActivity();
    if (!host.available || host.active) { await pause(500); continue; }
    if (legacyAbsent) return;
    try {
      const response = await read(activityUrl, { signal: AbortSignal.any([AbortSignal.timeout(200), ...(signal ? [signal] : [])]),
        headers: { 'X-Tempo-Work-Class': 'background' } });
      if (response.ok) { const state = await response.json();
        if (state.active === false && state.available !== false) return;
      }
    } catch (error) {
      signal?.throwIfAborted();
      if (["EPERM", "EACCES"].includes(error.cause?.code)) throw new Error("Study activity cannot be checked: local network permission is required");
      // Refusal is unavailable control, never proof that no protected Study exists.
    }
    await pause(500);
  }
}

export async function acquireDevelopmentBudget({ directory = budgetDirectory(), pause = wait, signal } = {}) {
  const token = randomUUID();
  for (;;) {
    signal?.throwIfAborted();
    try {
      await mkdir(directory, { mode: 0o700 });
      await mkdir(join(directory, 'resources'), { mode: 0o700 });
      try { await writeFile(join(directory, 'owner.json'), JSON.stringify({ schemaVersion: 1, pid: process.pid, token,
        startedAt: new Date().toISOString() }), { mode: 0o600, flag: 'wx' }); }
      catch (error) { await rm(directory, { recursive: true }); throw error; }
      return { token, directory, release: async () => {
        const owner = JSON.parse(await readFile(join(directory, 'owner.json'), 'utf8'));
        if (owner.token !== token || owner.pid !== process.pid) throw new Error('Development budget ownership changed; retaining its lock');
        if ((await readdir(join(directory, 'resources'))).length)
          throw new Error(`Development resources still need verified cleanup; retaining ownership at ${directory}`);
        await rm(directory, { recursive: true });
      } };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      try {
        const owner = JSON.parse(await readFile(join(directory, 'owner.json'), 'utf8'));
        try { process.kill(owner.pid, 0); }
        catch (probe) { if (probe.code === 'ESRCH') throw new Error(`Interrupted development owner ${owner.pid}; verify its resources before reclaiming ${directory}`); }
      } catch (probe) { if (probe.code !== 'ENOENT') throw probe; }
      // Stale/uncertain ownership is retained. Never infer permission to remove
      // another task's lock or containers from a missing process alone.
      await pause(500);
    }
  }
}

export async function reconcileDevelopmentBudget({ directory = budgetDirectory(), token, docker = defaultDocker,
  ownerAlive = pid => { try { process.kill(pid, 0); return true; } catch (error) { if (error.code === 'ESRCH') return false; throw error; } } } = {}) {
  const owner = await requireBudgetOwner(directory, token);
  if (ownerAlive(owner.pid)) throw new Error('Development owner is still running; do not reclaim its resources');
  for (const filename of await readdir(join(directory, 'resources'))) {
    const resource = JSON.parse(await readFile(join(directory, 'resources', filename), 'utf8'));
    if (resource.token !== token || !resource.key.startsWith('builder:'))
      throw new Error(`Resource needs its owning cleanup procedure: ${resource.key}`);
    await reconcileDevelopmentBuilder(resource.key.slice('builder:'.length), { directory, token, docker });
  }
  await requireBudgetOwner(directory, token);
  if (ownerAlive(owner.pid) || (await readdir(join(directory, 'resources'))).length)
    throw new Error('Development ownership or resources changed during reconciliation');
  await rm(directory, { recursive: true });
}

export async function runWithDevelopmentBudget(command, argumentsList, { cwd = process.cwd(), signal,
  acquire = acquireDevelopmentBudget, quiet = waitForStudyQuiet } = {}) {
  const controller = signal ? undefined : new AbortController();
  const interrupt = () => controller?.abort(new Error('Development command cancelled'));
  if (controller) { process.once('SIGINT', interrupt); process.once('SIGTERM', interrupt); }
  const cancellation = signal ?? controller.signal;
  let lease;
  try {
    if (process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN) {
      const owner = JSON.parse(await readFile(join(budgetDirectory(), 'owner.json'), 'utf8'));
      if (owner.token !== process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN) throw new Error('Invalid inherited development budget');
      return await run(command, argumentsList, cwd, cancellation, owner.token, budgetDirectory());
    }
    lease = await acquire({ signal:cancellation });
    await quiet({ signal:cancellation });
    return await run(command, argumentsList, cwd, cancellation, lease.token, lease.directory);
  } finally {
    try { await lease?.release(); }
    finally { if(controller) { process.removeListener('SIGINT',interrupt); process.removeListener('SIGTERM',interrupt); } }
  }
}

function run(command, argumentsList, cwd, signal, token, directory) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, argumentsList, { cwd, signal, stdio: 'inherit', env: { ...process.env,
      TEMPO_DEVELOPMENT_BUDGET_TOKEN: token, TEMPO_DEVELOPMENT_BUDGET_DIRECTORY: directory } });
    try { if (child.pid) setPriority(child.pid, 10); } catch { /* Kernel container caps remain the hard resource boundary. */ }
    let failure;
    child.on('error', error => { failure = error; if (!child.pid) reject(error); });
    child.on('exit', (code, reason) => failure ? reject(failure) : reason ? reject(new Error(`Owned development command ended by ${reason}`)) : resolve(code));
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2).filter((argument, index) => !(index === 0 && argument === '--'));
  if (!args.length) throw new Error('Supply an explicit development command');
  if (args[0] === '--reconcile-owner') {
    if (args.length !== 2) throw new Error('Supply the exact recorded owner token with --reconcile-owner');
    await reconcileDevelopmentBudget({ token: args[1] });
  } else {
    const controller = new AbortController();
    for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => controller.abort());
    process.exitCode = await runWithDevelopmentBudget(args[0], args.slice(1), { signal: controller.signal });
  }
}
