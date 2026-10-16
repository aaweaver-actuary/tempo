import test from 'node:test';
import assert from 'node:assert/strict';
import { inspectResources, validateResourceBudget } from '../resources.mjs';

const mib = 1024 * 1024;
function fixtureResources(files) { return { platform: 'linux', readText: async (filename) => { if (!(filename in files)) throw new Error('ENOENT'); return files[filename]; } }; }

test('native health cannot claim an unenforced memory budget', async () => {
  const resources = await inspectResources({ platform: 'darwin' });
  assert.equal(resources.memory_enforced, false);
  assert.throws(() => validateResourceBudget({ max_memory_mb: 2048 }, resources), { code: 'memory_limit_unavailable' });
});

test('cgroup v2 uses the effective minimum visible ancestor hard cap', async () => {
  const resources = await inspectResources(fixtureResources({
    '/proc/self/cgroup': '0::/tasks/maia\n',
    '/proc/self/mountinfo': '36 25 0:32 / /sys/fs/cgroup rw,nosuid,nodev,noexec,relatime - cgroup2 cgroup rw\n',
    '/sys/fs/cgroup/tasks/maia/memory.max': 'max\n',
    '/sys/fs/cgroup/tasks/memory.max': String(1536 * mib),
    '/sys/fs/cgroup/memory.max': String(2048 * mib),
  }));
  assert.equal(resources.memory_limit_mb, 1536);
  assert.equal(resources.source, '/sys/fs/cgroup/tasks/memory.max');
  assert.equal(validateResourceBudget({ max_memory_mb: 1536 }, resources), resources);
  assert.throws(() => validateResourceBudget({ max_memory_mb: 1535 }, resources), { code: 'memory_budget_unsupported' });
});

test('container cgroup mount root resolves its own group and v1 limits', async () => {
  const resources = await inspectResources(fixtureResources({
    '/proc/self/cgroup': '5:memory:/docker/task-a\n',
    '/proc/self/mountinfo': '30 25 0:28 /docker/task-a /sys/fs/cgroup/memory rw - cgroup cgroup rw,memory\n',
    '/sys/fs/cgroup/memory/memory.limit_in_bytes': String(1024 * mib),
  }));
  assert.equal(resources.memory_limit_mb, 1024);
  assert.doesNotThrow(() => validateResourceBudget({ max_memory_mb: 1024 }, resources));
});

test('unlimited or missing cgroup files never become an invented hard limit', async () => {
  const base = { '/proc/self/cgroup': '0::/\n', '/proc/self/mountinfo': '36 25 0:32 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n' };
  for (const value of ['max', '9223372036854771712', 'garbage', '0']) {
    const resources = await inspectResources(fixtureResources({ ...base, '/sys/fs/cgroup/memory.max': value }));
    assert.equal(resources.memory_enforced, false);
  }
  assert.equal((await inspectResources(fixtureResources({}))).memory_enforced, false);
});

test('hard cgroup below qualified minimum is rejected even for a smaller job', async () => {
  const resources = { memory_enforced: true, memory_limit_bytes: 768 * mib };
  assert.throws(() => validateResourceBudget({ max_memory_mb: 768 }, resources), { code: 'memory_budget_unsupported' });
});

test('health rejects a hard cap larger than any representable v1 job budget', () => {
  assert.throws(() => validateResourceBudget(null, { memory_enforced: true, memory_limit_bytes: 65537 * mib }), { code: 'memory_budget_unsupported' });
});

test('cgroup paths outside the mounted hierarchy are ignored', async () => {
  const resources = await inspectResources(fixtureResources({ '/proc/self/cgroup': '0::/other/task\n', '/proc/self/mountinfo': '36 25 0:32 /docker/task /sys/fs/cgroup rw - cgroup2 cgroup rw\n', '/sys/fs/cgroup/memory.max': String(1024 * mib) }));
  assert.equal(resources.memory_enforced, false);
});
