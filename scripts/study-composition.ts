import { relative } from 'node:path';
import type { Plugin } from 'vite';

/** Feature entry implementations excluded from the Study source and emitted closure. */
export const optionalStudyImplementations = {
  games: ['app/hooks/use-game-sync.ts', 'app/lib/game-sync-command.ts'],
  composition: ['app/views/legacy-home.tsx', 'app/views/legacy-workspace.tsx'],
  // workbench-navigation.ts is retained as a build-time URL validator in vite.static.config.ts.
  workbench: ['app/components/workbench-launch.tsx'],
  discovery: ['app/lib/discovery-admission-outbox.ts'],
  authoring: ['app/views/repertoire_view.tsx',
    'app/views/card_editor.tsx', 'app/views/tree_browser.tsx', 'app/views/studies_view.tsx',
    'app/ImportDialogBox.tsx', 'app/AnalysisPasteDialog.tsx'],
  workspaceAdministration: ['app/views/settings_view.tsx', 'app/components/service-status-panel.tsx',
    'app/components/integrity-repair-status.tsx', 'app/components/repertoire-integrity-dialog.tsx',
    'app/components/pending-discovery-saves.tsx'],
} as const;
export const optionalStudyContractExceptions = {
  'app/domain/analysis.ts': 'Shared study exercise grading and annotation schemas.',
  'app/domain/games.ts': 'Shared game metadata types used by retained Study views.',
  'app/lib/saved-endgame-practice.ts': 'Retained Study endgame line validation for playable saved cards.',
  'app/lib/tactics-progress.ts': 'Retained Study tactics progress projection for accepted packs.',
  'app/views/endgames_view.tsx': 'Retained Study endgames experience.',
} as const;
const optionalModules = new Set<string>(Object.values(optionalStudyImplementations).flat());
const optionalKeyword = /(game|workbench|discover|analysis|engine|maia|insight|statistics|progress|builder)/i;
const contractExceptions = new Set<string>(Object.keys(optionalStudyContractExceptions));

type SharedDependencies = {
  browserIntents: string[];
  chess: string[];
  board: string[];
  workersAndWasm: string[];
};

function modulePath(root: string, id: string) {
  const resolved = id.split('?')[0].replaceAll('\\', '/');
  const path = relative(root, resolved).replaceAll('\\', '/');
  if (!path.startsWith('..')) return path;
  const nodeModules = resolved.lastIndexOf('/node_modules/');
  return nodeModules >= 0 ? resolved.slice(nodeModules + 1) : null;
}

function appModule(path: string | null) {
  return path && /^(?:app|static\/src)\//.test(path) ? path : null;
}

function classifySharedDependencies(modules: string[], assets: string[]): SharedDependencies {
  const grouped: SharedDependencies = { browserIntents: [], chess: [], board: [], workersAndWasm: [] };
  for (const path of modules) {
    if (path.startsWith('database/browser-intents/')) grouped.browserIntents.push(path);
    if (path.startsWith('packages/chess-line/') || path.startsWith('node_modules/chess.js/')) grouped.chess.push(path);
    if (path.startsWith('node_modules/@lichess-org/chessground/')) grouped.board.push(path);
    if (/study\.worker|tempo-core|onnxruntime|\.wasm(?:\?|$)|\/ort\/|stockfish-worker|maia-worker/.test(path))
      grouped.workersAndWasm.push(path);
  }
  for (const path of assets)
    if (/study\.worker|tempo-core|onnxruntime|\.wasm(?:\?|$)|\/ort\/|stockfish-worker|maia-worker/.test(path))
      grouped.workersAndWasm.push(`asset:${path}`);
  return Object.fromEntries(Object.entries(grouped).map(([key, values]) => [key, [...new Set(values)].sort()])) as SharedDependencies;
}

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
      const loaded = [...new Set([...this.getModuleIds()].map(id => modulePath(root, id)).filter((id): id is string => Boolean(id)))];
      const forbidden = loaded.filter(id => optionalModules.has(id));
      if (forbidden.length) throw new Error(`Study imports optional workspace implementation: ${forbidden.join(', ')}`);
      const modules = [...new Set(loaded.map(path => appModule(path)).filter((path): path is string => Boolean(path)))].sort();
      const optionalNamedModules = modules.filter(path => optionalKeyword.test(path) && !optionalModules.has(path));
      const unexpected = optionalNamedModules.filter(path => !contractExceptions.has(path));
      if (unexpected.length) throw new Error(`Study imports unclassified optional-domain module: ${unexpected.join(', ')}`);
      const allowedOptionalNameContracts = Object.fromEntries(optionalNamedModules
        .filter(path => contractExceptions.has(path))
        .map(path => [path, optionalStudyContractExceptions[path as keyof typeof optionalStudyContractExceptions]]));
      const assets = Object.values(bundle).map(item => item.fileName).sort();
      const chunks = Object.values(bundle).flatMap(item => item.type === 'chunk'
        ? [{ file: item.fileName, modules: Object.keys(item.modules).map(id => appModule(modulePath(root, id))).filter(Boolean).sort() }] : []);
      this.emitFile({ type: 'asset', fileName: 'study-composition.json', source: JSON.stringify({
        entry: 'static/src/study-main.tsx', optionalImplementationFamilies: optionalStudyImplementations,
        optionalImplementations: [...optionalModules].sort(), allowedOptionalNameContracts,
        modules, sharedDependencies: classifySharedDependencies(loaded, assets),
        assets, chunks,
      }, null, 2) + '\n' });
    },
  };
}
