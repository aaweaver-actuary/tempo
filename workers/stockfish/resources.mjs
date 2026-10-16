import { readFile } from 'node:fs/promises';
import path from 'node:path';

export const MINIMUM_MEMORY_MB = 512;
const MEBIBYTE = 1024 * 1024;

export class ResourceError extends Error {
  constructor(code, message) { super(message); this.name = 'ResourceError'; this.code = code; this.retryable = false; }
}

function decodeMount(value) { return value.replace(/\\(040|011|012|134)/g, (_match, octal) => String.fromCharCode(parseInt(octal, 8))); }
function finiteLimit(value) {
  const trimmed = value.trim();
  if (trimmed === 'max' || !/^\d+$/.test(trimmed)) return null;
  const bytes = Number(trimmed);
  return Number.isSafeInteger(bytes) && bytes > 0 ? bytes : null;
}

/** Inspect the process's actual cgroup and all visible ancestor hard limits. */
async function inspectMemoryResources({ platform = process.platform, readText = (filename) => readFile(filename, 'utf8') } = {}) {
  if (platform !== 'linux') return { memory_enforced: false, memory_limit_bytes: null, memory_limit_mb: null, source: null, minimum_memory_mb: MINIMUM_MEMORY_MB, reason: 'memory_limit_unavailable' };
  let memberships, mounts;
  try {
    memberships = (await readText('/proc/self/cgroup')).trim().split('\n').map((line) => line.split(':'));
    mounts = (await readText('/proc/self/mountinfo')).trim().split('\n').map((line) => {
      const [left, right] = line.split(' - '), fields = left.split(' '), filesystem = right?.split(' ');
      return { root: decodeMount(fields[3] ?? ''), mountpoint: decodeMount(fields[4] ?? ''), type: filesystem?.[0], options: filesystem?.[2]?.split(',') ?? [] };
    });
  } catch { return { memory_enforced: false, memory_limit_bytes: null, memory_limit_mb: null, source: null, minimum_memory_mb: MINIMUM_MEMORY_MB, reason: 'memory_limit_unavailable' }; }
  const limits = [];
  for (const membership of memberships) {
    const unified = membership[0] === '0' && membership[1] === '';
    if (!unified && !membership[1]?.split(',').includes('memory')) continue;
    for (const mount of mounts.filter((candidate) => unified ? candidate.type === 'cgroup2' : candidate.type === 'cgroup' && candidate.options.includes('memory'))) {
      const group = membership[2];
      if (!group || (mount.root !== '/' && group !== mount.root && !group.startsWith(`${mount.root}/`))) continue;
      let directory = path.resolve(mount.mountpoint, `.${group.slice(mount.root === '/' ? 0 : mount.root.length) || '/'}`);
      const root = path.resolve(mount.mountpoint);
      if (directory !== root && !directory.startsWith(`${root}/`)) continue;
      while (directory === root || directory.startsWith(`${root}/`)) {
        const filename = path.join(directory, unified ? 'memory.max' : 'memory.limit_in_bytes');
        try { const limit = finiteLimit(await readText(filename)); if (limit !== null) limits.push({ bytes: limit, filename }); } catch { /* Missing ancestor files do not invent an enforceable cap. */ }
        if (directory === root) break;
        directory = path.dirname(directory);
      }
    }
  }
  limits.sort((first, second) => first.bytes - second.bytes);
  const limiting = limits[0];
  return { memory_enforced: Boolean(limiting), memory_limit_bytes: limiting?.bytes ?? null, memory_limit_mb: limiting ? limiting.bytes / MEBIBYTE : null, source: limiting?.filename ?? null, minimum_memory_mb: MINIMUM_MEMORY_MB, reason: limiting ? null : 'memory_limit_unavailable' };
}

export function validateResourceBudget(constraints, resources) {
  if (!resources.memory_enforced || !Number.isFinite(resources.memory_limit_bytes)) throw new ResourceError('memory_limit_unavailable', 'Serve requires an actual finite Linux cgroup memory limit');
  if (resources.memory_limit_bytes < MINIMUM_MEMORY_MB * MEBIBYTE) throw new ResourceError('memory_budget_unsupported', `The supported runtime requires at least ${MINIMUM_MEMORY_MB} MiB`);
  if (resources.memory_limit_bytes > 65536 * MEBIBYTE) throw new ResourceError('memory_budget_unsupported', 'The cgroup hard limit exceeds the largest memory budget representable by wire v1');
  if (resources.memory_limit_bytes !== MINIMUM_MEMORY_MB * MEBIBYTE) throw new ResourceError('memory_budget_unsupported', 'This profile requires a 512 MiB hard cap');
  if (!(resources.cpu_limit > 0 && resources.cpu_limit <= 1) || resources.swap_limit_bytes !== 0) throw new ResourceError('resource_envelope_unsupported', 'Finite <=1 CPU and zero swap are required');
  if (constraints && resources.memory_limit_bytes > constraints.max_memory_mb * MEBIBYTE) throw new ResourceError('memory_budget_unsupported', 'The process cgroup hard limit exceeds the requested memory budget');
  return resources;
}

/** This initial supported envelope is Linux cgroup v2, 512 MiB, <=1 CPU, zero swap. */
export async function inspectResources(options = {}) {
  const resources = await inspectMemoryResources(options);
  if (!resources.memory_enforced) return resources;
  const readText = options.readText ?? (filename => readFile(filename, 'utf8'));
  const directory = path.dirname(resources.source);
  try {
    const [quota, period] = (await readText(path.join(directory, 'cpu.max'))).trim().split(/\s+/);
    resources.cpu_limit = quota === 'max' ? null : Number(quota) / Number(period);
    resources.swap_limit_bytes = Number((await readText(path.join(directory, 'memory.swap.max'))).trim());
    resources.memory_events = Object.fromEntries((await readText(path.join(directory, 'memory.events'))).trim().split('\n').map(line => { const [key, value] = line.split(' '); return [key, Number(value)]; }));
  } catch { resources.cpu_limit = null; resources.swap_limit_bytes = null; }
  return resources;
}
