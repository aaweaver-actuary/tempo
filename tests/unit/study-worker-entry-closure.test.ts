// @vitest-environment node
import { build } from 'esbuild';
import { expect, it } from 'vitest';

it('essential_and_optional_worker_entries_have_separate_emitted_implementation_closures', async () => {
  const inputs = async (entry: string) => Object.keys((await build({ entryPoints:[entry], bundle:true,
    platform:'browser', write:false, metafile:true })).metafile!.inputs);
  const essential = await inputs('app/lib/study.worker.ts');
  const optional = await inputs('app/lib/optional-study.worker.ts');
  for (const dependency of ['app/lib/position-similarity.ts', 'app/lib/repertoire-alternative-index.ts',
    'app/lib/optional-study-computation.ts']) {
    expect(essential).not.toContain(dependency);
    expect(optional).toContain(dependency);
  }
  expect(essential).toContain('app/lib/essential-study-computation.ts');
  expect(optional).not.toContain('app/lib/essential-study-computation.ts');
});

it('workerless fallback selects the correct computation owner', async () => {
  const { runStudyTask } = await import('../../app/lib/background-study');
  expect(await runStudyTask({ kind:'queue', payload:{ cards:[], count:0 } })).toEqual([]);
  expect(await runStudyTask({ kind:'repertoireAlternatives', generation:7, lines:[] }))
    .toEqual({ generation:7, positions:[], movesByRepertoire:new Map() });
});
