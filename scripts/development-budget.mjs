// One host-wide owner for newly managed heavy work. Never stop another task's resources.
import { mkdir, mkdtemp, readFile, writeFile, rm, readdir } from 'node:fs/promises';
import { readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir, setPriority } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { execFile, execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const resourceBudgets = JSON.parse(readFileSync(new URL('../config/resource-budgets.json', import.meta.url), 'utf8'));
const lockDirectory = join(tmpdir(), 'tempo-development-resource-budget-v1.lock');
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const execute = promisify(execFile);

export function recordBudgetResourceSync(key, present, token = process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN) {
  if (!token) throw new Error('A resource must have a development budget owner');
  const owner = JSON.parse(readFileSync(join(lockDirectory, 'owner.json'), 'utf8'));
  if (owner.token !== token) throw new Error('Development resource ownership changed');
  const file = join(lockDirectory, 'resources', encodeURIComponent(key));
  if (present) writeFileSync(file, JSON.stringify({ token, pid: process.pid, key }), { mode: 0o600 });
  else rmSync(file, { force: true });
}
export async function recordBudgetResource(key,present,token) {recordBudgetResourceSync(key,present,token);}

export function assertDevelopmentCapacity(requestedServices, project, docker = args =>
  execFileSync('docker', args, { encoding: 'utf8' }), allocations = resourceBudgets.developmentServices) {
  const ids = docker(['ps', '--filter', 'label=tempo.resource-class=development', '--format', '{{.ID}}']).trim().split(/\s+/).filter(Boolean);
  const existing = ids.length ? JSON.parse(docker(['inspect', ...ids])) : [];
  let cpus = project === "__builder" ? resourceBudgets.development.cpus : 0;
  let memory = project === "__builder" ? resourceBudgets.development.memoryMiB : 0;
  for (const container of existing) {
    const host = container.HostConfig;
    const limit = host.NanoCpus / 1e9 || host.CpuQuota / host.CpuPeriod;
    if (!(limit > 0) || !(host.Memory > 0)) throw new Error('An existing managed development container has unbounded resources');
    cpus += limit; memory += host.Memory / 1024 / 1024;
  }
  for (const name of new Set(requestedServices)) {
    if (existing.some(container => container.Config.Labels['com.docker.compose.project'] === project &&
      container.Config.Labels['com.docker.compose.service'] === name)) continue;
    const limit = allocations[name];
    if (!limit) throw new Error(`No development resource allocation for ${name}`);
    cpus += limit.cpus; memory += limit.memoryMiB;
  }
  if (cpus > resourceBudgets.development.cpus + 1e-9 || memory > resourceBudgets.development.memoryMiB)
    throw new Error('New development work would exceed the aggregate study-protection budget; existing resources were left untouched');
}

export async function managedBuild(build, { docker = async args =>
  (await execute('docker', args, { maxBuffer: 1024 * 1024 })).stdout, quiet = waitForStudyQuiet } = {}) {
  const lease = process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN ? undefined : await acquireDevelopmentBudget();
  const name = `tempo-budget-builder-${randomUUID()}`;
  let created = false;
  const buildDirectory = await mkdtemp(join(tmpdir(), "tempo-managed-builder-"));
  const buildConfig = join(buildDirectory, "buildkitd.toml");
  await writeFile(buildConfig, "[worker.oci]\nmax-parallelism = 1\n");
  try {
    await quiet();
    const managedIds = (await docker(['ps', '--filter', 'label=tempo.resource-class=development', '--format', '{{.ID}}'])).trim();
    const managedContainers = managedIds ? await docker(['inspect', ...managedIds.split(/\s+/)]) : '[]';
    assertDevelopmentCapacity([], '__builder', args => args[0] === 'ps' ? managedIds : managedContainers);
    await docker(['buildx', 'create', '--name', name, '--driver', 'docker-container', '--buildkitd-config', buildConfig, '--driver-opt', 'image=moby/buildkit:rootless',
      '--driver-opt', `memory=${resourceBudgets.development.memoryMiB}m`,
      '--driver-opt', 'cpu-period=100000', '--driver-opt', `cpu-quota=${resourceBudgets.development.cpus * 100000}`,
      '--driver-opt', 'default-load=true']);
    created = true;
    await recordBudgetResource(`builder:${name}`, true, lease?.token ?? process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN);
    await docker(['buildx', 'inspect', name, '--bootstrap']);
    const [container] = JSON.parse(await docker(['inspect', `buildx_buildkit_${name}0`]));
    const host = container.HostConfig;
    if (!/^1000(?::1000)?$/.test(container.Config?.User ?? "") || host.Memory !== resourceBudgets.development.memoryMiB * 1024 * 1024 ||
        host.CpuQuota / host.CpuPeriod !== resourceBudgets.development.cpus)
      throw new Error('Managed builder kernel limits do not match the development budget');
    return await build(name);
  } finally {
    try { if (created) {
      await docker(['buildx', 'rm', name]);
      await recordBudgetResource(`builder:${name}`, false, lease?.token ?? process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN);
    } }
    finally { await rm(buildDirectory, {recursive:true,force:true}); await lease?.release(); }
  }
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
    return [name, { cpus: limit.cpus, mem_limit: `${limit.memoryMiB}m`,
      labels: { 'tempo.resource-owner': owner, 'tempo.resource-class': 'development' } }];
  }));
  const cpus = Object.values(limits).reduce((sum, limit) => sum + limit.cpus, 0);
  const memory = services.reduce((sum, name) => sum + allocations[name].memoryMiB, 0);
  if (cpus > resourceBudgets.development.cpus + 1e-9 || memory > resourceBudgets.development.memoryMiB)
    throw new Error(`Development allocation exceeds the aggregate ${resourceBudgets.development.cpus}-CPU/${resourceBudgets.development.memoryMiB}-MiB budget`);
  return { services: limits };
}

export async function waitForStudyQuiet({ activityUrl = process.env.TEMPO_STUDY_ACTIVITY_URL ?? 'http://127.0.0.1:8000/api/system/foreground-active',
  read = fetch, pause = wait, signal } = {}) {
  if (process.env.CI === 'true' && !process.env.TEMPO_STUDY_ACTIVITY_URL) return;
  for (;;) {
    signal?.throwIfAborted();
    try {
      const response = await read(activityUrl, { signal: AbortSignal.any([AbortSignal.timeout(200), ...(signal ? [signal] : [])]),
        headers: { 'X-Tempo-Work-Class': 'background' } });
      if (response.ok) { const state = await response.json();
        if (state.active === false && state.available !== false) return;
      }
    } catch (error) {
      signal?.throwIfAborted();
      if (["EPERM", "EACCES"].includes(error.cause?.code)) throw new Error("Study activity cannot be checked: local network permission is required");
      // A local refused connection means no study API is listening. A slow or
      // failing live API is unavailable evidence and does not admit heavy work.
      if (error.cause?.code === 'ECONNREFUSED' && ['127.0.0.1', 'localhost', '[::1]'].includes(new URL(activityUrl).hostname)) return;
    }
    await pause(500);
  }
}

export async function acquireDevelopmentBudget({ directory = lockDirectory, pause = wait, signal } = {}) {
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

export async function runWithDevelopmentBudget(command, argumentsList, { cwd = process.cwd(), signal,
  acquire = acquireDevelopmentBudget, quiet = waitForStudyQuiet } = {}) {
  const controller = signal ? undefined : new AbortController();
  const interrupt = () => controller?.abort(new Error('Development command cancelled'));
  if (controller) { process.once('SIGINT', interrupt); process.once('SIGTERM', interrupt); }
  const cancellation = signal ?? controller.signal;
  let lease;
  try {
    if (process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN) {
      const owner = JSON.parse(await readFile(join(lockDirectory, 'owner.json'), 'utf8'));
      if (owner.token !== process.env.TEMPO_DEVELOPMENT_BUDGET_TOKEN) throw new Error('Invalid inherited development budget');
      return await run(command, argumentsList, cwd, cancellation, owner.token);
    }
    lease = await acquire({ signal:cancellation });
    await quiet({ signal:cancellation });
    return await run(command, argumentsList, cwd, cancellation, lease.token);
  } finally {
    try { await lease?.release(); }
    finally { if(controller) { process.removeListener('SIGINT',interrupt); process.removeListener('SIGTERM',interrupt); } }
  }
}

function run(command, argumentsList, cwd, signal, token) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, argumentsList, { cwd, signal, stdio: 'inherit', env: { ...process.env,
      TEMPO_DEVELOPMENT_BUDGET_TOKEN: token, TEMPO_DEVELOPMENT_BUDGET_DIRECTORY: lockDirectory, CARGO_BUILD_JOBS: '1', CMAKE_BUILD_PARALLEL_LEVEL: '1',
      RAYON_NUM_THREADS: '1', UV_CONCURRENT_BUILDS: '1' } });
    try { if (child.pid) setPriority(child.pid, 10); } catch { /* Kernel container caps remain the hard resource boundary. */ }
    let failure;
    child.on('error', error => { failure = error; if (!child.pid) reject(error); });
    child.on('exit', (code, reason) => failure ? reject(failure) : reason ? reject(new Error(`Owned development command ended by ${reason}`)) : resolve(code));
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2).filter((argument, index) => !(index === 0 && argument === '--'));
  if (!args.length) throw new Error('Supply an explicit development command');
  const controller = new AbortController();
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => controller.abort());
  process.exitCode = await runWithDevelopmentBudget(args[0], args.slice(1), { signal: controller.signal });
}
