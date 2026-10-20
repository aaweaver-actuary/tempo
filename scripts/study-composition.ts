import { relative } from 'node:path';
import type { Plugin } from 'vite';

/** Feature entry implementations excluded from the Study source and emitted closure. */
export const optionalStudyImplementations = {
  games: ['app/views/games_view.tsx', 'app/components/workbench-game-sync.tsx',
    'app/hooks/use-game-sync.ts', 'app/lib/game-sync-command.ts'],
  composition: ['app/views/legacy-home.tsx', 'app/views/legacy-workspace.tsx', 'app/views/legacy-games-workspace.tsx'],
  authoring: ['app/views/analysis_view.tsx', 'app/views/repertoire_view.tsx',
    'app/views/card_editor.tsx', 'app/views/tree_browser.tsx', 'app/views/studies_view.tsx',
    'app/ImportDialogBox.tsx', 'app/AnalysisPasteDialog.tsx'],
  comparison: ['app/views/comparison_view.tsx'],
  insights: ['app/views/insights_view.tsx', 'app/views/statistics_view.tsx', 'app/views/progress_view.tsx'],
  workspaceAdministration: ['app/views/settings_view.tsx', 'app/components/service-status-panel.tsx',
    'app/components/integrity-repair-status.tsx', 'app/components/repertoire-integrity-dialog.tsx',
    'app/components/discoveries-tray.tsx'],
} as const;
const optionalModules = new Set<string>(Object.values(optionalStudyImplementations).flat());

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
      const forbidden = loaded.filter(id => optionalModules.has(relative(root, id).replaceAll('\\', '/')));
      if (forbidden.length) throw new Error(`Study imports optional workspace implementation: ${forbidden.map(id => relative(root, id)).join(', ')}`);
      const appModule = (id: string) => {
        const path = relative(root, id.split('?')[0]).replaceAll('\\', '/');
        return /^(?:app|static\/src)\//.test(path) ? path : null;
      };
      const chunks = Object.values(bundle).flatMap(item => item.type === 'chunk'
        ? [{ file: item.fileName, modules: Object.keys(item.modules).map(appModule).filter(Boolean).sort() }] : []);
      this.emitFile({ type: 'asset', fileName: 'study-composition.json', source: JSON.stringify({
        entry: 'static/src/study-main.tsx', optionalGamesModules: [], optionalImplementations: [],
        modules: [...new Set(loaded.map(appModule).filter(Boolean))].sort(), chunks,
      }, null, 2) + '\n' });
    },
  };
}
