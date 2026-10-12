// @vitest-environment node
import { spawnSync } from 'node:child_process';
import { expect, it } from 'vitest';
it('emergency CI fast-lane classification and evidence regressions execute in the regular gate', () => {
  const result = spawnSync(process.execPath, ['--test', 'tests/runner/ci-fast-lane.test.mjs'], { encoding: 'utf8' });
  expect(result.status, `${result.stdout}\n${result.stderr}`).toBe(0);
  expect(result.stdout).toContain('emergency_stale_other_head_evidence_cannot_qualify');
  expect(result.stdout).toMatch(/(?:#|ℹ) tests [1-9][0-9]*/);
}, 30_000);
