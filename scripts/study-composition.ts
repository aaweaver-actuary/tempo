import { relative } from 'node:path';
import type { Plugin } from 'vite';
import type { OutputChunk } from 'rollup';

const optionalModules = /(?:^|\/)app\/(?:views\/(?:games_view|legacy-games-workspace|legacy-home)|components\/workbench-game-sync|hooks\/use-game-sync|lib\/game-sync-command)\.[cm]?[jt]sx?$/;

/** A separate physical entry, not a runtime navigation/visibility condition. */
export function studyComposition(root = process.cwd()): Plugin {
  return {
    name: 'tempo-independent-study-composition',
    transformIndexHtml: {
      order: 'pre',
      handler(html) {
        if (!html.includes('src="/src/main.tsx"')) throw new Error('Study entry marker missing');
        return html.replace('src="/src/main.tsx"', 'src="/src/study-main.tsx"');
      },
    },
    generateBundle(_options, bundle) {
      const loaded = [...this.getModuleIds()].map(id => id.split('?')[0].replaceAll('\\', '/'));
      const forbidden = loaded.filter(id => optionalModules.test(id));
      if (forbidden.length) throw new Error(`Study imports optional Games implementation: ${forbidden.map(id => relative(root, id)).join(', ')}`);
      const appModule = (id: string) => {
        const path = relative(root, id.split('?')[0]).replaceAll('\\', '/');
        return /^(?:app|static\/src)\//.test(path) ? path : null;
      };
      const chunks = Object.values(bundle).filter((item): item is OutputChunk => item.type === 'chunk')
        .map(chunk => ({ file: chunk.fileName, modules: Object.keys(chunk.modules).map(appModule).filter(Boolean).sort() }));
      this.emitFile({ type: 'asset', fileName: 'study-composition.json', source: JSON.stringify({
        entry: 'static/src/study-main.tsx', optionalGamesModules: [],
        modules: [...new Set(loaded.map(appModule).filter(Boolean))].sort(), chunks,
      }, null, 2) + '\n' });
    },
  };
}
