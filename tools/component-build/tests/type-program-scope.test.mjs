import test from 'node:test';
import assert from 'node:assert/strict';
import { compareTypeProgramRoots, verifyTypeProgramRoots } from '../../../scripts/check-type-program-scope.mjs';

test('type program coverage retains every aggregate root and rejects unexpected roots', () => {
  assert.deepEqual(compareTypeProgramRoots(['a', 'b'], ['a'], ['b']), { missing: [], extra: [] });
  assert.deepEqual(compareTypeProgramRoots(['a', 'b'], ['a'], ['c']), { missing: ['b'], extra: ['c'] });
  const current = verifyTypeProgramRoots();
  assert.ok(current.aggregate > 0 && current.browser > 0 && current.tooling > 0);
  assert.deepEqual(current.missing, []); assert.deepEqual(current.extra, []);
});

test('type program coverage detects an omitted real source root', async () => {
  const { mkdtempSync, mkdirSync, rmSync, writeFileSync } = await import('node:fs');
  const { join } = await import('node:path');
  const { tmpdir } = await import('node:os');
  const root = mkdtempSync(join(tmpdir(), 'tempo-type-scope-'));
  try {
    mkdirSync(join(root, 'app')); mkdirSync(join(root, 'scripts'));
    writeFileSync(join(root, 'app', 'browser.ts'), 'export {};');
    writeFileSync(join(root, 'scripts', 'runner.ts'), 'export {};');
    writeFileSync(join(root, 'tsconfig.json'), JSON.stringify({ include: ['**/*.ts'] }));
    writeFileSync(join(root, 'tsconfig.browser.json'), JSON.stringify({ include: ['app/*.ts'] }));
    writeFileSync(join(root, 'tsconfig.tooling.json'), JSON.stringify({ include: ['scripts/*.ts'] }));
    assert.deepEqual(verifyTypeProgramRoots(root).missing, []);
    writeFileSync(join(root, 'tsconfig.tooling.json'), JSON.stringify({ include: ['app/*.ts'] }));
    assert.throws(() => verifyTypeProgramRoots(root), /scripts\/runner\.ts/);
  } finally { rmSync(root, { recursive:true, force:true }); }
});
