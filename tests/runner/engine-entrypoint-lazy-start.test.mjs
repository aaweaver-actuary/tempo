import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

function run(scenario) {
  const dir = mkdtempSync(join(tmpdir(), 'tempo-engine-entrypoint-'));
  try {
    const result = spawnSync(process.execPath, [fileURLToPath(new URL('./fixtures/engine-entrypoint-edge.mjs', import.meta.url)), scenario], {
      env: {...process.env, TEMPO_ENGINE_OUTBOX_PATH:join(dir,'outbox.json'), TEMPO_API_URL:'http://example.test', TEMPO_ENGINE_SMOKE:'0'}, encoding:'utf8', timeout:5000,
    });
    assert.equal(result.status, 0, `${result.stderr}\n${result.stdout}`);
    const trace = result.stdout.split('\n').find(line => line.startsWith('TRACE '));
    assert.ok(trace, result.stdout);
    return JSON.parse(trace.slice(6));
  } finally { rmSync(dir, {recursive:true, force:true}); }
}
for (const scenario of ['idle', 'study', 'busy', 'recovery', 'finalize']) {
  test(`entrypoint does not start Stockfish for ${scenario}`, () => {
    const events = run(scenario);
    if (scenario === 'recovery') assert.ok(events.some(e => e.path?.endsWith('/old/report') && e.operation === 'original-operation' && JSON.parse(e.body).lease_id === 'original-lease'));
    if (scenario === 'finalize') assert.ok(events.some(e => e.path?.endsWith('/position/finalize') && JSON.parse(e.body).lease_id === 'original-lease'));
    assert.deepEqual(events.filter(e => e.type === 'fork' || e.type === 'uci'), [], JSON.stringify(events));
  });
}
test('entrypoint starts one Stockfish for an admitted search', () => {
  const events = run('search');
  assert.equal(events.filter(e => e.type === 'fork').length, 1);
  assert.equal(events.filter(e => e.type === 'uci' && e.command.startsWith('go ')).length, 1);
  assert.ok(events.some(e => e.path?.endsWith('/job-1/report') && JSON.parse(e.body).lease_id === 'original-lease'));
});
