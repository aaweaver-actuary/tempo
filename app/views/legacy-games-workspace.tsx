import { lazy, Suspense } from 'react';
import { Chess } from 'chess.js';
import type { OptionalWorkspaceProps } from '../components/optional-workspace';
import { WorkbenchGameSync } from '../components/workbench-game-sync';
const GamesView = lazy(() => import('./games_view'));

/** Only the full legacy entry owns provider sync and Games presentation. */
export default function LegacyGamesWorkspace(props: OptionalWorkspaceProps) {
  if (props.view !== 'games') return null;
  return <Suspense fallback={<p role="status">Loading Games…</p>}>
    <WorkbenchGameSync>{gameSync => <GamesView
      initialFenFilter={props.positionFilter}
      initialRepertoireFilter={props.repertoireFilter}
      onClearFenFilter={props.onClearFilter}
      syncState={gameSync.state}
      onSync={() => void gameSync.sync(true)}
      onRepair={() => void gameSync.sync(true, true)}
      onQueueUpdated={props.onQueueUpdated}
      onSettings={() => props.onNavigate('settings')}
      onAnalyze={(game, gameCursor) => {
        const position = new Chess(game.startFen);
        const history = game.moves.slice(0, gameCursor).map(san => {
          const move = position.move(san);
          return { san: move.san, uci: `${move.from}${move.to}${move.promotion ?? ''}`, fen: position.fen() };
        });
        localStorage.setItem('tempo-builder-session', JSON.stringify({
          version: 1, activeRepertoireId: game.repertoireId, activeRepertoireByColor: {},
          orientation: game.color, startingFen: game.startFen, history,
          cursor: history.length, branchStart: history.length,
        }));
        props.onNavigate('builder');
      }}
      theme={props.theme} pieceSet={props.pieceSet} useSharedBoard
    />}</WorkbenchGameSync>
  </Suspense>;
}
