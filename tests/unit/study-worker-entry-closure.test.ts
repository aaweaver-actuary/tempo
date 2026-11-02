// @vitest-environment node
import { build } from 'esbuild';
import { expect, it } from 'vitest';

it('essential_and_optional_worker_entries_have_separate_emitted_implementation_closures', async () => {
  const bundle = async (entry: string) => (await build({ entryPoints:[entry], bundle:true,
    platform:'browser', write:false, metafile:true })).metafile!;
  const essential = await bundle('app/lib/study.worker.ts');
  const optional = await bundle('app/lib/optional-study.worker.ts');
  const inputs = (metafile: Awaited<ReturnType<typeof bundle>>) => Object.keys(metafile.inputs);
  const emittedBytes = (metafile: Awaited<ReturnType<typeof bundle>>, source: string) =>
    Object.values(metafile.outputs).reduce((total, output) => total +
      Object.entries(output.inputs ?? {}).reduce((sum, [path, details]) =>
        path.replaceAll('\\', '/').endsWith(source) ? sum + (details.bytesInOutput ?? 0) : sum, 0), 0);
  const essentialInputs = inputs(essential);
  const optionalInputs = inputs(optional);
  for (const dependency of ['app/lib/position-similarity.ts', 'app/lib/repertoire-alternative-index.ts',
    'app/lib/optional-study-computation.ts']) {
    expect(essentialInputs).not.toContain(dependency);
    expect(optionalInputs).toContain(dependency);
  }
  expect(essentialInputs).toContain('app/lib/essential-study-computation.ts');
  expect(optionalInputs).not.toContain('app/lib/essential-study-computation.ts');

  expect(emittedBytes(essential, 'app/lib/data-validation.ts')).toBeGreaterThan(0);
  expect(emittedBytes(optional, 'app/lib/data-validation.ts')).toBeGreaterThan(0);
  expect(emittedBytes(essential, 'app/lib/essential-study-computation.ts')).toBeGreaterThan(0);
  expect(emittedBytes(optional, 'app/lib/optional-study-computation.ts')).toBeGreaterThan(0);
  for (const dependency of ['app/lib/position-similarity.ts', 'app/lib/repertoire-alternative-index.ts'])
    expect(emittedBytes(optional, dependency)).toBeGreaterThan(0);
  expect(emittedBytes(essential, 'app/lib/optional-study-computation.ts')).toBe(0);
  for (const entry of [essential, optional]) {
    for (const dependency of ['app/lib/debug-reporting.ts', 'app/state/training-store.ts',
      'app/state/board-shell-store.ts', 'app/lib/validated-data.ts']) {
      expect(emittedBytes(entry, dependency), `${dependency} must not contribute emitted bytes`).toBe(0);
    }
  }
});

it('workerless fallback selects the correct computation owner', async () => {
  const { runStudyTask } = await import('../../app/lib/background-study');
  expect(await runStudyTask({ kind:'queue', payload:{ cards:[], count:0 } })).toEqual([]);
  expect(await runStudyTask({ kind:'repertoireAlternatives', generation:7, lines:[] }))
    .toEqual({ generation:7, positions:[], movesByRepertoire:new Map() });
});
