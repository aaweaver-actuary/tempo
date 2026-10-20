// @vitest-environment node
import { expect, it } from 'vitest';
import { studyComposition, optionalStudyImplementations } from '../../scripts/study-composition';
import { canOpenWorkspace } from '../../app/lib/study-workspace-policy';
import type { View } from '../../app/types';

it('independent Study rejects direct optional-workspace intents while legacy navigation remains', () => {
  const supported: View[] = ['train', 'tactics', 'endgames', 'settings'];
  const optional: View[] = ['games', 'builder', 'repertoire', 'studies', 'insights', 'compare', 'progress', 'statistics'];
  for (const view of supported) expect(canOpenWorkspace(view, true)).toBe(true);
  for (const view of optional) expect(canOpenWorkspace(view, true)).toBe(false);
  for (const view of [...supported, ...optional]) expect(canOpenWorkspace(view, false)).toBe(true);
});

it('Study selects a physical entry with no legacy conditional import', () => {
  const hook = studyComposition('/repo').transformIndexHtml!;
  const handler = typeof hook === 'function' ? hook : hook.handler;
  const html = '<script type="module" src="/src/main.tsx"></script>';
  expect(handler.call({} as never, html, {} as never)).toBe('<script type="module" src="/src/study-main.tsx"></script>');
  expect(() => handler.call({} as never, '<p>missing entry</p>', {} as never)).toThrow('Study entry marker missing');
});

it.each(Object.values(optionalStudyImplementations).flat())('Study build rejects loaded optional implementation even before emitting it: %s', path => {
  const hook = studyComposition('/repo').generateBundle!;
  const handler = typeof hook === 'function' ? hook : hook.handler;
  const context = { getModuleIds: () => [`/repo/${path}`][Symbol.iterator](), emitFile: () => undefined };
  expect(() => handler.call(context as never, {} as never, {} as never, false)).toThrow('Study imports optional workspace implementation');
});

it('Study composition emits an inspectable source/chunk inventory without private paths', () => {
  const hook = studyComposition('/repo').generateBundle!;
  const handler = typeof hook === 'function' ? hook : hook.handler;
  const assets: { fileName: string; source: string }[] = [];
  const context = { getModuleIds: () => ['/repo/static/src/study-main.tsx', '/repo/app/views/training_view.tsx'][Symbol.iterator](),
    emitFile: (asset: { fileName: string; source: string }) => { assets.push(asset); } };
  handler.call(context as never, {} as never, { 'assets/study.js': { type: 'chunk', fileName: 'assets/study.js', modules: { '/repo/app/views/training_view.tsx': {} } } } as never, false);
  expect(assets).toHaveLength(1);
  expect(assets[0].fileName).toBe('study-composition.json');
  const inventory = JSON.parse(assets[0].source);
  expect(inventory.optionalGamesModules).toEqual([]);
  expect(inventory.modules).toEqual(['app/views/training_view.tsx', 'static/src/study-main.tsx']);
  expect(inventory.chunks[0]).toEqual({ file: 'assets/study.js', modules: ['app/views/training_view.tsx'] });
  expect(assets[0].source).not.toContain('/repo/');
});
