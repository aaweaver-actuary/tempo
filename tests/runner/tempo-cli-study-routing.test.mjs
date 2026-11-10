import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { main } from '../../scripts/tempo-cli.mjs';

// An old experimental registration must never be reinterpreted as permission
// to operate the retained PostgreSQL installation.
for (const command of ['start', 'restart', 'stop', 'status', 'doctor', 'logs', 'backup', 'migrate']) {
  test(`retired Learning registration refuses ${command} before runtime selection`, async t => {
    const directory = mkdtempSync(join(tmpdir(), 'tempo-retired-learning-'));
    t.after(() => rmSync(directory, { recursive: true, force: true }));
    const config = join(directory, 'registration.json');
    const registration = JSON.stringify({ version: 2, root: directory,
      context: 'retired-target', daemonId: 'unselected-daemon', project: 'retired-learning',
      composeFiles: [join(directory, 'absent-compose.yml')], postgresVolumeKey: 'learning' });
    writeFileSync(config, registration);
    const output = [];
    await assert.rejects(main([command, '--config', config], line => output.push(line)), {
      code: 'retired_learning_registration',
    });
    assert.equal(readFileSync(config, 'utf8'), registration);
    assert.deepEqual(output, []);
  });
}
