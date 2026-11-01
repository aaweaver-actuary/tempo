// @vitest-environment node
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { isLocalQualificationBuild, withTaskBrowserBuild } from '../../scripts/task-browser-build.mjs';

it('task_owned_targets_do_not_share_destructive_build_outputs', () => {
  const parent = mkdtempSync(join(tmpdir(), 'tempo-build-test-'));
  try {
    withTaskBrowserBuild((local: string, localOwner: string) => {
      withTaskBrowserBuild((study: string, studyOwner: string) => {
        withTaskBrowserBuild((otherLocal: string) => {
          expect(new Set([local, study, otherLocal]).size).toBe(3);
          writeFileSync(join(localOwner, 'sentinel'), 'local');
          expect(existsSync(join(studyOwner, 'sentinel'))).toBe(false);
          expect(readFileSync(join(localOwner, 'sentinel'), 'utf8')).toBe('local');
        }, { parent, target: 'local' });
      }, { parent, target: 'study' });
    }, { parent, target: 'local' });
    expect(isLocalQualificationBuild('npm', ['run', 'build:local'])).toBe(true);
    expect(isLocalQualificationBuild('npm', ['run', 'build:study'])).toBe(false);
  } finally { rmSync(parent, { recursive: true, force: true }); }
});

it('task_build_cleanup_runs_on_failure_without_touching_parent_files', () => {
  const parent = mkdtempSync(join(tmpdir(), 'tempo-build-test-'));
  const sentinel = join(parent, 'keep');
  writeFileSync(sentinel, 'safe');
  let owned = '';
  try {
    expect(() => withTaskBrowserBuild((_output: string, directory: string) => { owned = directory; throw new Error('build failed'); }, { parent })).toThrow('build failed');
    expect(existsSync(owned)).toBe(false);
    expect(readFileSync(sentinel, 'utf8')).toBe('safe');
  } finally { rmSync(parent, { recursive: true, force: true }); }
});

it('pinned_visual_build_and_preview_share_the_vite_output_override', () => {
  const runner = readFileSync('scripts/test-visual.mjs', 'utf8');
  const visualConfig = readFileSync('playwright.visual.config.ts', 'utf8');
  const viteConfig = readFileSync('vite.static.config.ts', 'utf8');
  expect(runner).toContain('TEMPO_BROWSER_BUILD_DIR=/workspace/');
  expect(visualConfig).toContain('npm run build:local && TEMPO_TARGET=local vite preview --config vite.static.config.ts');
  expect(viteConfig).toContain('process.env.TEMPO_BROWSER_BUILD_DIR ??');
  expect(readFileSync('Dockerfile.study-web', 'utf8')).toContain('COPY --from=build /app/local-dist/');
});

it('local_qualification_consumers_use_the_task_owned_override', () => {
  for (const path of ['scripts/test-all.mjs', 'scripts/ci-run-layer.mjs']) {
    const source = readFileSync(path, 'utf8');
    expect(source).toContain('isLocalQualificationBuild(command, args)');
    expect(source).toContain('withTaskBrowserBuild(output => run({ ...environment, TEMPO_BROWSER_BUILD_DIR: output }))');
  }
});
