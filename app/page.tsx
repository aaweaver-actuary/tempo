'use client';
/* eslint-disable react-hooks/refs, react-hooks/set-state-in-effect */

import type { DrawShape } from '@lichess-org/chessground/draw';
import type { Key } from '@lichess-org/chessground/types';
import { Chess, Move, Square } from 'chess.js';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { MoveNavigator, OutcomeFlash } from './components/board-controls';
import { Chessboard, type BoardTheme, type PieceSet } from './components/chessboard';
import { analyzeWithMaia, analyzeWithStockfish, type EngineMove } from './lib/analysis-engines';
import { generateLegalEndgameFen, type EndgameMaterial } from './lib/endgame-generator';
import type { LocalRepertoire, PracticeCard } from './lib/domain';
import { parsePgnImport } from './lib/pgn-import';
import { advanceTacticProgress, readTacticProgress, tacticProgressKey, writeTacticProgress } from './lib/tactics-progress';

const STANDARD_FEN = new Chess().fen();
const demoCards = [
  {
    id: 'open-sicilian-prefix',
    kind: 'opening',
    title: 'Open Sicilian',
    subtitle: 'Najdorf setup',
    startingFen: STANDARD_FEN,
    moves: ['e4', 'c5', 'Nf3', 'd6', 'd4', 'cxd4', 'Nxd4', 'Nf6', 'Nc3', 'a6', 'Be3'],
    userMoveTarget: 6,
    nextMove: '… e6 · 7. Qd2',
  },
  {
    id: 'french-classical-prefix',
    kind: 'opening',
    title: 'French Defense',
    subtitle: 'Classical variation',
    startingFen: STANDARD_FEN,
    moves: ['e4', 'e6', 'd4', 'd5', 'Nc3', 'Nf6', 'e5', 'Nfd7', 'f4', 'c5', 'Nf3'],
    userMoveTarget: 6,
    nextMove: '… Nc6 · 7. Be3',
  },
  {
    id: 'lichess-puzzle-00sHx',
    kind: 'puzzle',
    title: 'Mate in two',
    subtitle: 'Mate · middlegame · short',
    startingFen: 'q5nr/1ppknQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 w - - 1 18',
    moves: ['Be6+', 'Kd8', 'Qf8#'],
    userMoveTarget: 2,
    sourceUrl: 'https://lichess.org/training/00sHx',
  },
] satisfies PracticeCard[];

type Feedback = 'ready' | 'correct' | 'branch' | 'wrong' | 'complete';
type View = 'train' | 'tactics' | 'endgames' | 'repertoire' | 'analysis' | 'games' | 'progress';

type ExplorerMove = {
  uci: string;
  san: string;
  white: number;
  draws: number;
  black: number;
};

const analysisLines = [
  { title: 'Open Sicilian · Najdorf', side: 'White', moves: ['e4', 'c5', 'Nf3', 'd6', 'd4', 'cxd4', 'Nxd4', 'Nf6', 'Nc3', 'a6'] },
  { title: 'French · Classical', side: 'White', moves: ['e4', 'e6', 'd4', 'd5', 'Nc3', 'Nf6', 'e5', 'Nfd7'] },
  { title: 'Caro-Kann · Advance', side: 'White', moves: ['e4', 'c6', 'd4', 'd5', 'e5', 'Bf5', 'Nf3', 'e6'] },
  { title: 'King’s Indian · Main line', side: 'Black', moves: ['d4', 'Nf6', 'c4', 'g6', 'Nc3', 'Bg7', 'e4', 'd6'] },
];

function uciLine(sanMoves: string[], startingFen = STANDARD_FEN) {
  const chess = new Chess(startingFen);
  return sanMoves.map((san) => {
    const move = chess.move(san);
    return `${move.from}${move.to}${move.promotion ?? ''}`;
  });
}

function localDayKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function fenAfterMoves(moves: string[], count: number, startingFen = STANDARD_FEN) {
  const chess = new Chess(startingFen);
  for (const move of moves.slice(0, count)) chess.move(move);
  return chess.fen();
}

function lichessAnalysisUrl(moves: string[], startingFen = STANDARD_FEN) {
  if (moves.length === 0 && startingFen === STANDARD_FEN) return 'https://lichess.org/analysis/standard';
  const chess = new Chess(startingFen);
  for (const move of moves) chess.move(move);
  return `https://lichess.org/analysis/standard/${encodeURIComponent(chess.fen())}`;
}

function TreeBrowser({ onClose, theme, pieceSet }: { onClose: () => void; theme: BoardTheme; pieceSet: PieceSet }) {
  const line = demoCards[0].moves;
  const [ply, setPly] = useState(0);
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="tree-browser" role="dialog" aria-modal="true" aria-labelledby="tree-title" onMouseDown={(event) => event.stopPropagation()}>
        <button className="close-button" onClick={onClose} aria-label="Close repertoire browser">×</button>
        <div className="tree-heading"><div><p className="eyebrow">Repertoire browser</p><h2 id="tree-title">1. e4 Main Lines</h2></div><span>{ply === 0 ? 'Starting position' : `${ply} plies from start`}</span></div>
        <div className="tree-layout">
          <div className="tree-board"><Chessboard fen={fenAfterMoves(line, ply)} locked showHint={false} theme={theme} pieceSet={pieceSet} onMove={() => undefined} /></div>
          <div className="tree-panel">
            <div className="tree-path"><button className={ply === 0 ? 'current' : ''} onClick={() => setPly(0)}>Start</button>{line.map((move, index) => <button className={ply === index + 1 ? 'current' : ''} key={`${move}-${index}`} onClick={() => setPly(index + 1)}><span>{index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : '…'}</span>{move}</button>)}</div>
            <div className="branch-list"><span>Branches from the first move</span><button className="selected"><b>1… c5</b><small>Open Sicilian · 168 cards</small></button><button><b>1… e6</b><small>French Defense · 74 cards</small></button><button><b>1… c6</b><small>Caro-Kann · 51 cards</small></button></div>
            <a className="tree-analysis" href={lichessAnalysisUrl(line.slice(0, ply))} target="_blank" rel="noreferrer">↗ Analyze this position on Lichess</a>
          </div>
        </div>
      </section>
    </div>
  );
}

function candidatesThrough<T>(moves: T[], weight: (move: T) => number, target: number) {
  const total = moves.reduce((sum, move) => sum + weight(move), 0);
  let cumulative = 0;
  return moves.filter((move) => {
    if (total === 0 || cumulative / total >= target) return false;
    cumulative += weight(move);
    return true;
  });
}

function randomUrlSafe(bytes = 48) {
  const data = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode(...data)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
}

async function connectLichess() {
  const verifier = randomUrlSafe();
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  const challenge = btoa(String.fromCharCode(...new Uint8Array(digest))).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
  const state = randomUrlSafe(20);
  const redirectUri = `${location.origin}${location.pathname}`;
  sessionStorage.setItem('tempo-lichess-verifier', verifier);
  sessionStorage.setItem('tempo-lichess-state', state);
  sessionStorage.setItem('tempo-return-view', 'analysis');
  const url = new URL('https://lichess.org/oauth');
  url.search = new URLSearchParams({ response_type: 'code', client_id: 'tempo.local.chess.trainer', redirect_uri: redirectUri, code_challenge_method: 'S256', code_challenge: challenge, state }).toString();
  location.assign(url.toString());
}

function AnalysisView({ theme, pieceSet, imported, onTheme, onPieces }: { theme: BoardTheme; pieceSet: PieceSet; imported: LocalRepertoire[]; onTheme: (value: BoardTheme) => void; onPieces: (value: PieceSet) => void }) {
  const [history, setHistory] = useState<{ san: string; uci: string; fen: string }[]>([]);
  const [cursor, setCursor] = useState(0);
  const [explorerOn, setExplorerOn] = useState(() => typeof window === 'undefined' || localStorage.getItem('tempo-explorer-on') !== 'false');
  const [stockfishOn, setStockfishOn] = useState(() => typeof window !== 'undefined' && localStorage.getItem('tempo-stockfish-on') === 'true');
  const [maiaOn, setMaiaOn] = useState(() => typeof window !== 'undefined' && localStorage.getItem('tempo-maia-on') === 'true');
  const [maiaElo, setMaiaElo] = useState(() => typeof window === 'undefined' ? '1500' : localStorage.getItem('tempo-maia-elo') ?? '1500');
  const [coverageTarget, setCoverageTarget] = useState(() => typeof window === 'undefined' ? 90 : Number(localStorage.getItem('tempo-coverage-target') ?? 90));
  const [engineWindowCp, setEngineWindowCp] = useState(() => typeof window === 'undefined' ? 30 : Number(localStorage.getItem('tempo-engine-window-cp') ?? 30));
  const [lichessToken, setLichessToken] = useState(() => typeof window === 'undefined' ? '' : sessionStorage.getItem('tempo-lichess-token') ?? '');
  const [explorerMoves, setExplorerMoves] = useState<ExplorerMove[]>([]);
  const [mastersMoves, setMastersMoves] = useState<ExplorerMove[]>([]);
  const [explorerSpeeds, setExplorerSpeeds] = useState(() => typeof window === 'undefined' ? 'blitz,rapid,classical' : localStorage.getItem('tempo-explorer-speeds') ?? 'blitz,rapid,classical');
  const [explorerRatings, setExplorerRatings] = useState(() => typeof window === 'undefined' ? '1600,1800,2000,2200,2500' : localStorage.getItem('tempo-explorer-ratings') ?? '1600,1800,2000,2200,2500');
  const [branchStart, setBranchStart] = useState<number | null>(null);
  const [branchNote, setBranchNote] = useState('');
  const [explorerState, setExplorerState] = useState<'auth' | 'loading' | 'ready' | 'error'>(lichessToken ? 'loading' : 'auth');
  const [stockfishMoves, setStockfishMoves] = useState<EngineMove[]>([]);
  const [stockfishState, setStockfishState] = useState<'off' | 'loading' | 'ready' | 'error'>(stockfishOn ? 'loading' : 'off');
  const [maiaMoves, setMaiaMoves] = useState<EngineMove[]>([]);
  const [maiaState, setMaiaState] = useState<'off' | 'loading' | 'ready' | 'error'>(maiaOn ? 'loading' : 'off');
  const [maiaProgress, setMaiaProgress] = useState(0);
  const visibleHistory = history.slice(0, cursor);
  const fen = visibleHistory.at(-1)?.fen ?? STANDARD_FEN;
  const previousUci = visibleHistory.at(-1)?.uci;
  const lastMove: [string, string] | undefined = previousUci ? [previousUci.slice(0, 2), previousUci.slice(2, 4)] : undefined;
  const playedUci = visibleHistory.map((move) => move.uci);
  const availableLines = useMemo(() => [
    ...analysisLines.map((line) => ({ ...line, startingFen: STANDARD_FEN })),
    ...imported.flatMap((repertoire) => repertoire.cards.map((card) => ({ title: card.title, side: repertoire.side, moves: card.moves, startingFen: card.startingFen }))),
  ], [imported]);
  const lineMatches = availableLines.filter((line) => line.startingFen === STANDARD_FEN && playedUci.every((move, index) => uciLine(line.moves, line.startingFen)[index] === move));
  const coveredReplies = new Set(lineMatches.flatMap((line) => {
    const uci = uciLine(line.moves, line.startingFen)[cursor];
    return uci ? [uci] : [];
  }));

  function rememberToggle(key: string, value: boolean, setter: (next: boolean) => void) {
    localStorage.setItem(key, String(value));
    setter(value);
  }

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const code = params.get('code');
    if (!code) return;
    const verifier = sessionStorage.getItem('tempo-lichess-verifier');
    const expectedState = sessionStorage.getItem('tempo-lichess-state');
    if (!verifier || params.get('state') !== expectedState) { setExplorerState('error'); return; }
    const redirectUri = `${location.origin}${location.pathname}`;
    fetch('https://lichess.org/api/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'authorization_code', code, code_verifier: verifier, redirect_uri: redirectUri, client_id: 'tempo.local.chess.trainer' }),
    }).then((response) => response.ok ? response.json() : Promise.reject(new Error('Lichess connection failed')))
      .then((data: { access_token: string }) => {
        sessionStorage.setItem('tempo-lichess-token', data.access_token);
        setLichessToken(data.access_token);
        window.history.replaceState({}, '', redirectUri);
      }).catch(() => setExplorerState('error'));
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key === 'ArrowLeft') { event.preventDefault(); setCursor((value) => Math.max(0, value - 1)); }
      if (event.key === 'ArrowRight') { event.preventDefault(); setCursor((value) => Math.min(history.length, value + 1)); }
      if (event.key === 'Home') { event.preventDefault(); setCursor(0); }
      if (event.key === 'End') { event.preventDefault(); setCursor(history.length); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [history.length]);

  useEffect(() => {
    if (!explorerOn || !lichessToken) { setExplorerState(lichessToken ? 'ready' : 'auth'); return; }
    const controller = new AbortController();
    setExplorerState('loading');
    const headers = { Authorization: `Bearer ${lichessToken}` };
    const base = 'https://explorer.lichess.org';
    Promise.all([
      fetch(`${base}/lichess?variant=standard&speeds=${explorerSpeeds}&ratings=${explorerRatings}&fen=${encodeURIComponent(fen)}`, { signal: controller.signal, headers }),
      fetch(`${base}/masters?variant=standard&fen=${encodeURIComponent(fen)}`, { signal: controller.signal, headers }),
    ]).then(async ([lichess, masters]) => {
      if (!lichess.ok || !masters.ok) throw new Error('Explorer request failed');
      return Promise.all([lichess.json(), masters.json()]);
    })
      .then(([human, masters]: [{ moves?: ExplorerMove[] }, { moves?: ExplorerMove[] }]) => { setExplorerMoves(human.moves ?? []); setMastersMoves(masters.moves ?? []); setExplorerState('ready'); })
      .catch((error) => { if (error.name !== 'AbortError') setExplorerState('error'); });
    return () => controller.abort();
  }, [fen, explorerOn, lichessToken, explorerRatings, explorerSpeeds]);

  useEffect(() => {
    if (!stockfishOn) { setStockfishState('off'); setStockfishMoves([]); return; }
    let current = true;
    setStockfishState('loading');
    analyzeWithStockfish(fen).then((moves) => { if (current) { setStockfishMoves(moves); setStockfishState('ready'); } }).catch((error) => { console.error('Stockfish 19:', error); if (current) setStockfishState('error'); });
    return () => { current = false; };
  }, [fen, stockfishOn]);

  useEffect(() => {
    if (!maiaOn) { setMaiaState('off'); setMaiaMoves([]); return; }
    let current = true;
    setMaiaState('loading');
    analyzeWithMaia(fen, Number(maiaElo), setMaiaProgress).then((moves) => { if (current) { setMaiaMoves(moves); setMaiaState('ready'); } }).catch((error) => { console.error('Maia 3:', error); if (current) setMaiaState('error'); });
    return () => { current = false; };
  }, [fen, maiaElo, maiaOn]);

  function playMove(from: Square, to: Square) {
    const chess = new Chess(fen);
    try {
      const move = chess.move({ from, to, promotion: 'q' });
      const uci = `${move.from}${move.to}${move.promotion ?? ''}`;
      if (!coveredReplies.has(uci) && branchStart === null) setBranchStart(cursor);
      setHistory((current) => [...current.slice(0, cursor), { san: move.san, uci, fen: chess.fen() }]);
      setCursor((value) => value + 1);
    } catch { /* Chessground only offers legal destinations. */ }
  }

  function playUci(uci: string) { playMove(uci.slice(0, 2) as Square, uci.slice(2, 4) as Square); }
  function saveBranch() {
    if (branchStart === null || history.length <= branchStart) return;
    const stored = JSON.parse(localStorage.getItem('tempo-saved-branches') ?? '[]') as string[][];
    const moves = history.map((move) => move.uci);
    if (!stored.some((line) => line.join(' ') === moves.join(' '))) stored.push(moves);
    localStorage.setItem('tempo-saved-branches', JSON.stringify(stored));
    setBranchNote('Saved and deduplicated · response cards updated'); setBranchStart(null);
  }

  const target = coverageTarget / 100;
  const explorerCandidates = candidatesThrough(explorerMoves, (move) => move.white + move.draws + move.black, target);
  const maiaCandidates = candidatesThrough(maiaMoves, (move) => move.probability ?? 0, target);
  const explorerTotal = explorerMoves.reduce((sum, move) => sum + move.white + move.draws + move.black, 0);
  const explorerCovered = explorerMoves.filter((move) => coveredReplies.has(move.uci)).reduce((sum, move) => sum + move.white + move.draws + move.black, 0);
  const maiaCovered = maiaMoves.filter((move) => coveredReplies.has(move.uci)).reduce((sum, move) => sum + (move.probability ?? 0), 0);
  const topEngine = stockfishMoves[0];
  const engineCandidates = stockfishMoves.filter((move, index) => {
    if (index >= 5) return false;
    if (topEngine?.mate !== undefined) return move.mate !== undefined;
    if (topEngine?.cp === undefined || move.cp === undefined) return index === 0;
    return topEngine.cp - move.cp <= engineWindowCp;
  });
  const arrowSources = new Map<string, Set<string>>();
  for (const move of explorerCandidates) arrowSources.set(move.uci, new Set([...(arrowSources.get(move.uci) ?? []), 'L']));
  for (const move of engineCandidates) arrowSources.set(move.uci, new Set([...(arrowSources.get(move.uci) ?? []), 'S']));
  for (const move of maiaCandidates) arrowSources.set(move.uci, new Set([...(arrowSources.get(move.uci) ?? []), 'M']));
  for (const move of coveredReplies) arrowSources.set(move, new Set([...(arrowSources.get(move) ?? []), 'R']));
  const shapes: DrawShape[] = [...arrowSources.entries()].slice(0, 9).map(([uci, sources]) => ({
    orig: uci.slice(0, 2) as Key,
    dest: uci.slice(2, 4) as Key,
    brush: sources.has('R') ? 'yellow' : 'blue',
    label: { text: [...sources].filter((source) => source !== 'R').join('·') || 'R' },
  }));

  function reset() { setHistory([]); setCursor(0); }
  function disconnectLichess() { sessionStorage.removeItem('tempo-lichess-token'); setLichessToken(''); setExplorerMoves([]); }

  return <section className="analysis-page" id="analysis">
    <div className="analysis-heading compact-analysis"><h1>Builder</h1><div className="analysis-switches"><button className={stockfishOn ? 'on' : ''} onClick={() => rememberToggle('tempo-stockfish-on', !stockfishOn, setStockfishOn)}><i /> Stockfish 19</button><button className={maiaOn ? 'on' : ''} onClick={() => rememberToggle('tempo-maia-on', !maiaOn, setMaiaOn)}><i /> Maia 3</button><label>Within <input aria-label="Engine centipawn window" type="number" min="0" max="300" value={engineWindowCp} onChange={(event) => { const value=Number(event.target.value); setEngineWindowCp(value); localStorage.setItem('tempo-engine-window-cp',String(value)); }} /> cp</label></div></div>
    <div className="analysis-layout">
      <div className="analysis-board-column">
        <Chessboard fen={fen} lastMove={lastMove} locked={false} showHint={false} theme={theme} pieceSet={pieceSet} shapes={shapes} onMove={playMove} />
        <div className="arrow-legend"><span><i className="known" /> Covered</span><span><i className="candidate" /> Gap</span><span tabIndex={0} title="R: already in your repertoire"><b>R</b> Repertoire</span><span tabIndex={0} title="L: Lichess opening explorer"><b>L</b> Lichess</span><span tabIndex={0} title="S: Stockfish engine line"><b>S</b> Stockfish</span><span tabIndex={0} title="M: Maia human-likelihood model"><b>M</b> Maia</span></div>
        <div className="board-tools"><button onClick={() => setCursor((value) => Math.max(0, value - 1))} disabled={!cursor}>← <span>Back</span></button><button onClick={() => setCursor((value) => Math.min(history.length, value + 1))} disabled={cursor === history.length}>→ <span>Forward</span></button><button onClick={reset}>↻ <span>Reset</span></button><a href={`https://lichess.org/analysis/standard/${encodeURIComponent(fen)}`} target="_blank" rel="noreferrer">↗ <span>Open in Lichess</span></a><label>Board<select value={theme} onChange={(event) => onTheme(event.target.value as BoardTheme)}><option value="brown">Brown</option><option value="blue">Blue</option><option value="green">Green</option></select></label><label>Pieces<select value={pieceSet} onChange={(event) => onPieces(event.target.value as PieceSet)}><option value="cburnett">Cburnett</option><option value="merida">Merida</option></select></label></div>
        <div className="analysis-moves"><span>{history.length ? history.map((move, index) => <button className={index < cursor ? 'shown' : ''} key={`${move.uci}-${index}`} onClick={() => setCursor(index + 1)}>{index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : ''}{move.san}</button>) : 'Make a move to search your repertoire'}</span><small>←/→ move · Home/End jump</small><button onClick={() => navigator.clipboard?.writeText(fen)}>Copy FEN</button></div>
        <div className="branch-editor"><span><b>{branchStart === null ? 'Branch editor' : `Drafting from ply ${branchStart}`}</b><small>{branchNote || 'Select an opponent move, then play your response and any continuation.'}</small></span>{branchStart === null ? <button onClick={() => { setBranchStart(cursor); setBranchNote(''); }}>＋ Add branch here</button> : <><button className="save" onClick={saveBranch}>Save branch</button><button onClick={() => { setHistory((h) => h.slice(0, branchStart)); setCursor(branchStart); setBranchStart(null); }}>Cancel</button></>}</div>
      </div>
      <aside className="analysis-sidebar">
        <section className="analysis-panel coverage-panel"><div className="panel-heading"><div><span>Coverage target</span><strong>Cover the likely {coverageTarget}%</strong></div><select value={coverageTarget} onChange={(event) => { const value = Number(event.target.value); setCoverageTarget(value); localStorage.setItem('tempo-coverage-target', String(value)); }}><option value="80">80%</option><option value="90">90%</option><option value="95">95%</option></select></div><div className="coverage-summary"><div><span>Lichess coverage</span><strong>{explorerTotal ? Math.round(explorerCovered / explorerTotal * 100) : '—'}%</strong></div><div><span>Maia coverage</span><strong>{maiaMoves.length ? Math.round(maiaCovered * 100) : '—'}%</strong></div><div><span>Responses saved</span><strong>{coveredReplies.size}</strong></div></div></section>
        <section className="analysis-panel repertoire-results"><div className="panel-heading"><div><span>Position search</span><strong>{lineMatches.length ? `${lineMatches.length} repertoire ${lineMatches.length === 1 ? 'match' : 'matches'}` : 'Repertoire gap'}</strong></div><b className={lineMatches.length ? 'covered' : 'gap'}>{lineMatches.length ? 'Covered' : 'Uncovered'}</b></div>{lineMatches.length ? lineMatches.map((line) => <div className="line-result" key={line.title}><span>{line.side}</span><strong>{line.title}</strong><small>{line.moves.slice(cursor, cursor + 3).join(' · ') || 'Exact line endpoint'}</small></div>) : <div className="empty-result"><strong>No saved line reaches this position.</strong><p>Add a response here without leaving the board.</p><button>＋ Add to repertoire</button></div>}</section>
        <section className="analysis-panel explorer-panel"><div className="panel-heading"><div><span>Lichess opening explorer</span><strong>Human games · {coverageTarget}% set</strong></div><button className={`tiny-switch${explorerOn ? ' on' : ''}`} onClick={() => rememberToggle('tempo-explorer-on', !explorerOn, setExplorerOn)}>{explorerOn ? 'Live' : 'Off'}</button></div>
          <div className="explorer-filters"><label>Games<select value={explorerSpeeds} onChange={(e) => { setExplorerSpeeds(e.target.value); localStorage.setItem('tempo-explorer-speeds',e.target.value); }}><option value="blitz,rapid,classical">Blitz + rapid + classical</option><option value="rapid,classical">Rapid + classical</option><option value="classical">Classical only</option></select></label><label>Ratings<select value={explorerRatings} onChange={(e) => { setExplorerRatings(e.target.value); localStorage.setItem('tempo-explorer-ratings',e.target.value); }}><option value="1600,1800,2000,2200,2500">1600+</option><option value="2000,2200,2500">2000+</option><option value="2200,2500">2200+</option></select></label></div>
          {!explorerOn ? <p className="panel-message">Explorer is paused.</p> : explorerState === 'auth' ? <div className="connect-panel"><p>Lichess now requires a signed-in connection for Explorer data.</p><button onClick={connectLichess}>Connect Lichess</button><small>No account permissions are requested.</small></div> : explorerState === 'loading' ? <p className="panel-message">Loading Lichess and Masters data…</p> : explorerState === 'error' ? <div className="connect-panel"><p>The Lichess connection needs to be refreshed.</p><button onClick={connectLichess}>Reconnect</button></div> : <><div className="source-status"><span>Connected · Lichess + Masters</span><button onClick={disconnectLichess}>Disconnect</button></div><MoveRows moves={explorerCandidates.map((move) => ({ ...move, probability: explorerTotal ? (move.white + move.draws + move.black) / explorerTotal : 0 }))} covered={coveredReplies} detail="probability" onPlay={playUci} /><p className="masters-note">Masters: {mastersMoves.slice(0,3).map((move) => move.san).join(' · ') || 'No games at this position'}</p></>}
        </section>
        <section className="analysis-panel engine-panel"><div className="panel-heading"><div><span>Stockfish 19</span><strong>Engine lines</strong></div><b className={`engine-badge ${stockfishState}`}>{stockfishState === 'loading' ? 'Analyzing…' : stockfishState === 'ready' ? 'Local' : stockfishState === 'error' ? 'Could not start' : 'Off'}</b></div>{stockfishState === 'ready' && <MoveRows moves={engineCandidates} covered={coveredReplies} detail="score" onPlay={playUci} />}</section>
        <section className="analysis-panel engine-panel"><div className="panel-heading"><div><span>Maia 3</span><strong>Likely moves at your level</strong></div><label className="elo-select">Elo<select value={maiaElo} onChange={(event) => { setMaiaElo(event.target.value); localStorage.setItem('tempo-maia-elo', event.target.value); }}><option>1100</option><option>1500</option><option>1900</option></select></label></div>{maiaState === 'loading' ? <p className="panel-message">{maiaProgress ? `Loading local model · ${maiaProgress}%` : 'Starting local Maia model…'}</p> : maiaState === 'error' ? <p className="panel-message error">Maia could not start in this browser.</p> : maiaState === 'ready' ? <MoveRows moves={maiaCandidates} covered={coveredReplies} detail="probability" onPlay={playUci} /> : <p className="panel-message">Maia is off.</p>}</section>
      </aside>
    </div>
  </section>;
}

function MoveRows({ moves, covered, detail, onPlay }: { moves: EngineMove[]; covered: Set<string>; detail: 'probability' | 'score'; onPlay?: (uci: string) => void }) {
  return <div className="candidate-list">{moves.map((move, index) => <button className="candidate-row" key={move.uci} onClick={() => onPlay?.(move.uci)}><span>{index + 1}</span><strong>{move.san}</strong><small>{detail === 'probability' ? `${Math.round((move.probability ?? 0) * 100)}%` : move.score}</small><em className={covered.has(move.uci) ? 'covered' : 'gap'}>{covered.has(move.uci) ? 'Covered' : 'Gap'}</em></button>)}</div>;
}

function RepertoireView({ imported, onImport, onBrowse }: { imported: LocalRepertoire[]; onImport: () => void; onBrowse: () => void }) {
  const bundled = [
    { id: 'sample-white', side: 'White' as const, title: '1. e4 Main Lines', sourceName: 'Tempo examples', detail: '4 lines · 2 cards due', progress: 76, due: 2, pgn: '[Event "1. e4 Main Lines"]\n[Result "*"]\n\n1. e4 c5 2. Nf3 d6 3. d4 cxd4 *' },
    { id: 'sample-black', side: 'Black' as const, title: 'Sicilian Defense', sourceName: 'Tempo examples', detail: '2 lines · 1 card due', progress: 58, due: 1, pgn: '[Event "Sicilian Defense"]\n[Result "*"]\n\n1. e4 c5 2. Nf3 d6 *' },
  ];
  const importedItems = useMemo(() => imported.map((item) => ({ ...item, detail: `${item.cards.length} unique ${item.cards.length === 1 ? 'line' : 'lines'} · imported locally`, progress: 0, due: item.cards.length })), [imported]);
  const [repertoires, setRepertoires] = useState([...bundled, ...importedItems]);
  useEffect(() => {
    setRepertoires((current) => [...current.filter((item) => !item.id.startsWith('repertoire-')), ...importedItems]);
  }, [importedItems]);
  function rename(index:number) {
    const value=window.prompt('Repertoire nickname',repertoires[index].title);
    if(!value?.trim()) return;
    setRepertoires((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, title: value.trim() } : item));
  }
  function exportPgn(item?: typeof repertoires[number]) {
    const text = item ? item.pgn : repertoires.map((entry) => entry.pgn).join('\n\n');
    const url=URL.createObjectURL(new Blob([text],{type:'application/x-chess-pgn'}));
    const link=document.createElement('a'); link.href=url; link.download=item?`${item.title}.pgn`:'tempo-repertoires.pgn'; link.click(); URL.revokeObjectURL(url);
  }
  return (
    <section className="library-page" id="repertoire">
      <div className="page-heading">
        <div><p className="eyebrow">Your source material</p><h1>Repertoire</h1><p>Upload PGNs once. Tempo turns transpositions and shared prefixes into one clean set of cards.</p></div>
        <div className="heading-actions"><button onClick={()=>exportPgn()}>⇩ Export all PGN</button><button className="primary-button" onClick={onImport}>＋ Import PGN</button></div>
      </div>
      <div className="library-grid">
        {repertoires.map((item,index) => (
          <article className="repertoire-card" key={item.id}>
            <div className="repertoire-top"><span className="side-badge">{item.side}</span><span>{item.due ? `${item.due} due` : 'Up to date'}</span></div>
            <div className="mini-board" aria-hidden="true">{Array.from({ length: 16 }).map((_, index) => <i key={index} />)}</div>
            <div className="repertoire-name"><h2>{item.title}</h2><button onClick={()=>rename(index)} title="Rename repertoire">✎</button></div><p>{item.detail}</p><small className="source-name">{item.sourceName}</small>
            <div className="maturity-row"><span>Maturity</span><strong>{item.progress}%</strong></div>
            <div className="maturity-track"><span style={{ width: `${item.progress}%` }} /></div>
            <div className="repertoire-actions"><button className="browse-button" onClick={onBrowse}>Browse tree</button><button onClick={()=>exportPgn(item)}>⇩ PGN</button></div>
          </article>
        ))}
        <button className="new-repertoire-card" onClick={onImport}><span>＋</span><strong>Add a repertoire</strong><small>PGN files stay on this computer</small></button>
      </div>
    </section>
  );
}

const sampleGames = [
  { id: 'g1', source: 'Lichess', date: 'Sep 12', speed: 'Rapid', color: 'White', result: 'Won', opening: 'Open Sicilian', status: 'covered', detail: 'Covered through 12… Be7', flag: 'First major mistake · 18. Bxh7? · −1.24', flagPly: 13, moves: ['e4','c5','Nf3','d6','d4','cxd4','Nxd4','Nf6','Nc3','a6','Be3','e6','f3','b5','Qd2'] },
  { id: 'g2', source: 'Chess.com', date: 'Sep 10', speed: 'Blitz', color: 'Black', result: 'Lost', opening: 'King’s Indian', status: 'opponent gap', detail: 'New opponent move 7. d5', flag: 'Missed punishment · 12… Nxe4 · +1.18 available', flagPly: 9, moves: ['d4','Nf6','c4','g6','Nc3','Bg7','e4','d6','Nf3','O-O','Be2'] },
  { id: 'g3', source: 'Lichess', date: 'Sep 8', speed: 'Blitz', color: 'White', result: 'Draw', opening: 'French Defense', status: 'player deviation', detail: 'You played 8. Bd3 instead of 8. Qd2', flag: 'First major mistake · 8. Bd3 · −1.07', flagPly: 10, moves: ['e4','e6','d4','d5','Nc3','Nf6','e5','Nfd7','f4','c5','Nf3'] },
  { id: 'g4', source: 'Lichess', date: 'Sep 2', speed: 'Classical', color: 'Black', result: 'Won', opening: 'English Opening', status: 'no repertoire', detail: 'No applicable Black repertoire', flag: 'No ≥100cp swing found', flagPly: 0, moves: ['c4','e5','Nc3','Nf6','g3','d5'] },
];

const tacticMotifs = [
  ['hangingPiece','Hanging pieces','♟'],['fork','Forks','♘'],['pin','Pins','⌖'],['skewer','Skewers','⇥'],['discoveredAttack','Discoveries','✦'],
  ['mateIn1','Mate in 1','#1'],['mateIn2','Mate in 2','#2'],['mateIn3','Mate in 3','#3'],['mateIn4Plus','Mate in 4+','#4'],
  ['calculation2','2-move calculation','2×'],['calculation3','3-move calculation','3×'],['calculation4','4-move calculation','4×'],['trappedPiece','Trapped pieces','▣'],
];
const hangingSample:PracticeCard={id:'hanging-sample',kind:'puzzle',title:'Loose queen',subtitle:'Hanging piece',startingFen:'4k3/8/8/8/3q4/3R4/8/4K3 w - - 0 1',moves:['Rxd4'],userMoveTarget:1};
const tacticExamples: Record<string, PracticeCard> = {
  hangingPiece: hangingSample,
  fork: { id:'fork-sample',kind:'puzzle',title:'Knight fork',subtitle:'Fork',startingFen:'3q3k/8/8/4N3/8/8/8/4K3 w - - 0 1',moves:['Nf7+'],userMoveTarget:1 },
  pin: { id:'pin-sample',kind:'puzzle',title:'Create the pin',subtitle:'Pin',startingFen:'4k3/8/2n5/8/2B5/8/8/4K3 w - - 0 1',moves:['Bb5'],userMoveTarget:1 },
  skewer: { id:'skewer-sample',kind:'puzzle',title:'Skewer king and queen',subtitle:'Skewer',startingFen:'4k3/8/8/7q/8/8/2B5/4K3 w - - 0 1',moves:['Bg6+'],userMoveTarget:1 },
  discoveredAttack: { id:'discovery-sample',kind:'puzzle',title:'Open the file',subtitle:'Discovery',startingFen:'4k3/8/8/8/4B3/8/8/4R1K1 w - - 0 1',moves:['Bd5+'],userMoveTarget:1 },
};
const alternateTactics: PracticeCard[] = [
  { id:'loose-rook',kind:'puzzle',title:'Loose rook',subtitle:'Material',startingFen:'4k3/8/5r2/8/8/2B5/8/4K3 w - - 0 1',moves:['Bxf6'],userMoveTarget:1 },
  { id:'loose-knight',kind:'puzzle',title:'Loose knight',subtitle:'Material',startingFen:'4k3/8/8/3n4/8/8/8/3QK3 w - - 0 1',moves:['Qxd5'],userMoveTarget:1 },
  demoCards[2],
];

function TacticsView({ theme, pieceSet }: { theme: BoardTheme; pieceSet: PieceSet }) {
  const [motif, setMotif] = useState('hangingPiece');
  const [stage, setStage] = useState('easy');
  const [progress, setProgress] = useState(readTacticProgress);
  const [step, setStep] = useState(0);
  const [hint, setHint] = useState(false);
  const [failed, setFailed] = useState(false);
  const [outcome, setOutcome] = useState<'correct' | 'wrong' | null>(null);
  const progressKey = tacticProgressKey(motif, stage);
  const currentProgress = progress[progressKey] ?? { clean: 0, index: 0 };
  const deck = [tacticExamples[motif] ?? demoCards[2], ...alternateTactics];
  const puzzle = deck[currentProgress.index % deck.length];
  const [fen, setFen] = useState(puzzle.startingFen);

  const resetAttempt = useCallback((markFailed = false) => {
    setFen(puzzle.startingFen);
    setStep(0);
    setHint(markFailed);
    setFailed(markFailed);
    setOutcome(null);
  }, [puzzle]);

  useEffect(() => { resetAttempt(); }, [resetAttempt]);

  function finish() {
    const clean = !failed;
    setOutcome(clean ? 'correct' : 'wrong');
    window.setTimeout(() => {
      setProgress((current) => {
        const next = advanceTacticProgress(current, progressKey, clean);
        writeTacticProgress(next);
        return next;
      });
    }, 750);
  }

  function movePiece(from: Square, to: Square) {
    if (outcome || step % 2 || step >= puzzle.moves.length) return;
    const board = new Chess(fen);
    let move: Move;
    try { move = board.move({ from, to, promotion: 'q' }); } catch { return; }
    if (!board.isCheckmate() && move.san !== puzzle.moves[step]) {
      setFailed(true);
      setHint(true);
      return;
    }
    setFen(board.fen());
    const replyIndex = step + 1;
    if (replyIndex >= puzzle.moves.length) { finish(); return; }
    const replyBoard = new Chess(board.fen());
    replyBoard.move(puzzle.moves[replyIndex]);
    setFen(replyBoard.fen());
    setStep(replyIndex + 1);
    setHint(false);
    if (replyIndex + 1 >= puzzle.moves.length) finish();
  }

  const current = tacticMotifs.find((item) => item[0] === motif)!;
  const target = stage === 'focused' ? 250 : 100;
  const previousStages = ['easy', 'medium', 'hard'];
  return (
    <section className="tactics-page">
      <div className="workspace-title">
        <div><h1>Tactics</h1><span>{currentProgress.clean} clean solves in {current[1].toLowerCase()} · {stage}</span></div>
        <div className="stage-tabs">{['easy', 'medium', 'hard', 'focused'].map((item, index) => (
          <button key={item} disabled={index > 0 && (progress[tacticProgressKey(motif, previousStages[index - 1])]?.clean ?? 0) < 100} className={stage === item ? 'active' : ''} onClick={() => setStage(item)}>
            {item === 'focused' ? 'Focused · 250' : `${item[0].toUpperCase() + item.slice(1)} · 100`}
          </button>
        ))}</div>
      </div>
      <div className="tactics-workspace">
        <aside className="motif-rail">{tacticMotifs.map(([id, name, icon]) => {
          const clean = progress[tacticProgressKey(id, stage)]?.clean ?? 0;
          return <button className={motif === id ? 'active' : ''} key={id} onClick={() => setMotif(id)}><b>{icon}</b><span>{name}</span><small>{clean ? `${clean} / ${stage === 'focused' ? 250 : 100}` : 'Not started'}</small></button>;
        })}</aside>
        <div className="board-column centered-board">
          <Chessboard fen={fen} expectedSan={puzzle.moves[step]} locked={Boolean(outcome) || step >= puzzle.moves.length} showHint={hint} theme={theme} pieceSet={pieceSet} onMove={movePiece}/>
          <div className="board-tools"><button onClick={() => { setFailed(true); setHint(true); }}>⌁ <span>Show move</span></button><button onClick={() => resetAttempt(true)}>↻ <span>Restart</span></button>{puzzle.sourceUrl && <a href={puzzle.sourceUrl} target="_blank" rel="noreferrer">↗ <span>Original</span></a>}</div>
          {outcome && (
            <OutcomeFlash outcome={outcome}/>
          )}
        </div>
        <aside className="study-panel tactic-study">
          <span className="pill puzzle">{stage}</span><h2>{current[1]}</h2><p className="tactic-rating">Puzzle {currentProgress.index + 1} of {target}</p>
          {!outcome && <div className={`feedback ${failed ? 'wrong' : 'ready'}`} role="status"><span className="feedback-icon">{failed ? '×' : '●'}</span><div><strong>{failed ? 'Follow the arrow' : 'Your move'}</strong></div></div>}
          <div className="stage-progress"><span style={{ width: `${Math.min(100, currentProgress.clean / target * 100)}%` }}/></div><small>Easy → Medium → Hard → Focused</small>
        </aside>
      </div>
    </section>
  );
}

const endgameTemplates: EndgameMaterial[] = [
  { white: 'KQ', black: 'K', name: 'Queen + king' },
  { white: 'KR', black: 'K', name: 'Rook + king' },
  { white: 'KQR', black: 'K', name: 'Queen + rook' },
  { white: 'KQQ', black: 'K', name: 'Two queens' },
];

function EndgamesView({ theme, pieceSet }: { theme: BoardTheme; pieceSet: PieceSet }) {
  const [selected, setSelected] = useState(1);
  const [goal, setGoal] = useState<'win' | 'draw' | null>(null);
  const [status, setStatus] = useState('Classify the position before playing.');
  const [fen, setFen] = useState(() => generateLegalEndgameFen(endgameTemplates[1]));

  function newPosition(index = selected) {
    setFen(generateLegalEndgameFen(endgameTemplates[index]));
    setGoal(null);
    setStatus('Classify the position before playing.');
  }

  function classify(value: 'win' | 'draw') {
    setGoal(value);
    setStatus(value === 'win' ? 'Correct · now convert the win.' : 'This position is winning. Try again.');
  }

  function play(from: Square, to: Square) {
    if (goal !== 'win') return;
    const board = new Chess(fen);
    try {
      board.move({ from, to, promotion: 'q' });
      setFen(board.fen());
      setStatus(board.isCheckmate() ? 'Converted · template review complete.' : 'Winning status preserved · best defense is preparing…');
    } catch { /* Chessground restricts this to legal moves. */ }
  }

  return (
    <section className="endgames-page">
      <div className="workspace-title"><div><h1>Endgames</h1><span>Exact seven-piece practice</span></div><button className="primary-button">＋ New material set</button></div>
      <div className="endgame-workspace">
        <aside className="template-list">{endgameTemplates.map((template, index) => <button className={selected === index ? 'active' : ''} key={template.name} onClick={() => { setSelected(index); newPosition(index); }}><strong>{template.name}</strong><small>{template.white} vs {template.black} · White</small></button>)}</aside>
        <div className="board-column centered-board"><Chessboard fen={fen} locked={!goal || status.startsWith('Converted')} showHint={false} theme={theme} pieceSet={pieceSet} onMove={play}/><div className="board-tools"><button onClick={() => newPosition()}>⤨ <span>New position</span></button><button>⚙ <span>Edit material</span></button></div></div>
        <aside className="study-panel endgame-study"><span className="pill">Material template</span><h2>{endgameTemplates[selected].name}</h2><p>White to move · exact tablebase</p><div className="classification"><button className={goal === 'win' ? 'active' : ''} onClick={() => classify('win')}>Win</button><button className={goal === 'draw' ? 'active' : ''} onClick={() => classify('draw')}>Draw</button></div><div className="feedback ready"><span className="feedback-icon">●</span><div><strong>{goal ? 'Play the position' : 'Win or draw?'}</strong><p>{status}</p></div></div><small>A draw is secured after 20 accurate user moves. Any worsened tablebase result fails immediately.</small></aside>
      </div>
    </section>
  );
}

const pieceSymbols: Record<string, string> = { K:'♔', Q:'♕', R:'♖', B:'♗', N:'♘', P:'♙', k:'♚', q:'♛', r:'♜', b:'♝', n:'♞', p:'♟', '':'×' };

function editFenSquare(fen: string, square: Square, piece: string) {
  const [placement, ...state] = fen.trim().split(/\s+/);
  const rows = placement.split('/').map((row) => row.split('').flatMap((value) => /\d/.test(value) ? Array(Number(value)).fill('') : [value]));
  const rank = 8 - Number(square[1]);
  const file = square.charCodeAt(0) - 97;
  rows[rank][file] = piece;
  const compact = rows.map((row) => {
    let empty = 0;
    let result = '';
    for (const value of row) {
      if (!value) { empty += 1; continue; }
      if (empty) { result += empty; empty = 0; }
      result += value;
    }
    return result + (empty || '');
  }).join('/');
  return `${compact} ${state.join(' ')}`;
}

function moveFenPiece(fen: string, from: Square, to: Square) {
  const chess = new Chess(fen);
  const piece = chess.get(from);
  if (!piece) return fen;
  const symbol = piece.color === 'w' ? piece.type.toUpperCase() : piece.type;
  return editFenSquare(editFenSquare(fen, from, ''), to, symbol);
}

function CardEditor({ card, theme, pieceSet, onClose, onSave }: { card: PracticeCard; theme: BoardTheme; pieceSet: PieceSet; onClose: () => void; onSave: (card: PracticeCard) => void }) {
  const [fen, setFen] = useState(card.startingFen);
  const [solution, setSolution] = useState(card.moves);
  const [cursor, setCursor] = useState(0);
  const [tab, setTab] = useState<'position' | 'solution'>('position');
  const [historyMode, setHistoryMode] = useState<'preserve' | 'reset'>('preserve');
  const [piece, setPiece] = useState('B');
  const [error, setError] = useState('');
  const previewFen = useMemo(() => { try { return fenAfterMoves(solution, cursor, fen); } catch { return fen; } }, [cursor, fen, solution]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return; }
      if (event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLInputElement) return;
      if (event.key === 'ArrowLeft') { event.preventDefault(); setCursor((value) => Math.max(0, value - 1)); }
      if (event.key === 'ArrowRight') { event.preventDefault(); setCursor((value) => Math.min(solution.length, value + 1)); }
      if (event.key === 'ArrowUp') { event.preventDefault(); setCursor(0); }
      if (event.key === 'ArrowDown') { event.preventDefault(); setCursor(solution.length); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose, solution.length]);

  function playSolution(from: Square, to: Square) {
    const board = new Chess(previewFen);
    try {
      const move = board.move({ from, to, promotion: 'q' });
      setSolution((moves) => [...moves.slice(0, cursor), move.san]);
      setCursor((value) => value + 1);
      setError('');
    } catch { setError('That move is not legal from this position.'); }
  }

  function restoreOriginal() {
    setFen('q5nr/1ppknQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 w - - 1 18');
    setSolution(['Be6+', 'Kd8', 'Qf8#']);
    setCursor(0);
    setError('');
  }

  function save() {
    try {
      const board = new Chess(fen);
      for (const san of solution) board.move(san);
      onSave({ ...card, startingFen: fen, moves: solution });
      onClose();
    } catch { setError('The position or solution contains an illegal move.'); }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section className="card-editor" role="dialog" aria-modal="true" aria-labelledby="card-editor-title" onMouseDown={(event) => event.stopPropagation()}>
        <button className="close-button" onClick={onClose} aria-label="Close card editor">×</button>
        <div className="editor-heading"><div><p className="eyebrow">Card repair</p><h2 id="card-editor-title">Edit {card.title}</h2></div>{card.sourceUrl && <button onClick={restoreOriginal}>Restore Lichess original</button>}</div>
        <div className="editor-tabs"><button className={tab === 'position' ? 'active' : ''} onClick={() => setTab('position')}>Position</button><button className={tab === 'solution' ? 'active' : ''} onClick={() => setTab('solution')}>Solution</button></div>
        <div className="editor-layout">
          <div className="editor-board-column">
            {tab === 'position' && <div className="piece-palette">{Object.entries(pieceSymbols).map(([id, symbol]) => <button className={piece === id ? 'active' : ''} key={id || 'remove'} onClick={() => setPiece(id)} aria-label={id ? `Place ${id}` : 'Remove piece'}>{symbol}</button>)}</div>}
            <Chessboard fen={tab === 'position' ? fen : previewFen} locked={false} showHint={false} theme={theme} pieceSet={pieceSet} editMode={tab === 'position'} onSquareSelect={(square) => { if (tab === 'position') setFen((current) => editFenSquare(current, square, piece)); }} onFreeMove={(from, to) => setFen((current) => moveFenPiece(current, from, to))} onMove={playSolution}/>
            {tab === 'solution' && <><MoveNavigator cursor={cursor} length={solution.length} onChange={setCursor}/><div className="solution-line">{solution.length ? solution.map((move, index) => <button className={index < cursor ? 'shown' : ''} key={`${move}-${index}`} onClick={() => setCursor(index + 1)}>{index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : ''}{move}</button>) : <span>Play the solution on the board.</span>}</div></>}
          </div>
          <div className="editor-fields">
            <label>FEN<textarea value={fen} onChange={(event) => { setFen(event.target.value); setCursor(0); }}/></label>
            <p className="editor-key-help">←/→ step · ↑ start · ↓ end · Esc close</p>
            <fieldset><legend>Scheduling history</legend><label><input type="radio" checked={historyMode === 'preserve'} onChange={() => setHistoryMode('preserve')}/> Preserve history</label><label><input type="radio" checked={historyMode === 'reset'} onChange={() => setHistoryMode('reset')}/> Reset as a new card</label></fieldset>
            {error && <p className="editor-error">{error}</p>}
            <div className="editor-actions"><button onClick={onClose}>Cancel</button><button className="primary-button" onClick={save}>Validate & save</button></div>
          </div>
        </div>
      </section>
    </div>
  );
}

function GamesView({ onAnalyze, theme, pieceSet }: { onAnalyze: () => void; theme: BoardTheme; pieceSet: PieceSet }) {
  const [lichess, setLichess] = useState(() => typeof window === 'undefined' ? '' : localStorage.getItem('tempo-lichess-username') ?? '');
  const [chesscom, setChesscom] = useState(() => typeof window === 'undefined' ? '' : localStorage.getItem('tempo-chesscom-username') ?? '');
  const [source, setSource] = useState('All');
  const [status, setStatus] = useState('All');
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState(() => typeof window === 'undefined' ? 'Last 90 days · rated blitz, rapid, and classical' : localStorage.getItem('tempo-last-sync-note') ?? 'Last 90 days · rated blitz, rapid, and classical');
  const [lastSync,setLastSync]=useState(()=>typeof window==='undefined'?'':localStorage.getItem('tempo-last-sync')??'');
  const [selected,setSelected]=useState(sampleGames[0]); const [cursor,setCursor]=useState(sampleGames[0].flagPly); const [engineOn,setEngineOn]=useState(true); const [engineText,setEngineText]=useState('Quick scan found a 124cp swing.');
  const games = sampleGames.filter((game) => (source === 'All' || game.source === source) && (status === 'All' || game.status === status));
  const gameFen=fenAfterMoves(selected.moves,Math.min(cursor,selected.moves.length));
  const gameLast=cursor ? uciLine(selected.moves)[cursor-1] : undefined;
  const localApi=typeof window!=='undefined' && ['localhost','127.0.0.1'].includes(location.hostname) ? (process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000') : '';

  function saveAccounts() {
    localStorage.setItem('tempo-lichess-username', lichess.trim());
    localStorage.setItem('tempo-chesscom-username', chesscom.trim());
    setSyncNote('Accounts saved locally');
  }
  const sync = useCallback(async () => {
    if (syncing) return;
    localStorage.setItem('tempo-lichess-username', lichess.trim()); localStorage.setItem('tempo-chesscom-username', chesscom.trim()); setSyncing(true);
    if (!localApi) { setSyncNote('Live sync is available in local Tempo. This private Site keeps only the comparison preview.'); setSyncing(false); return; }
    try {
      const response = await fetch(`${localApi}/api/games/sync`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lichess_username: lichess, chesscom_username: chesscom, days: 90, speeds: ['blitz', 'rapid', 'classical'], rated_only: true }) });
      if (!response.ok) throw new Error();
      const data = await response.json(); const time=new Date(data.synced_at).toISOString(); setLastSync(time); localStorage.setItem('tempo-last-sync',time); const note=`${data.imported} new games · local cache is up to date`; setSyncNote(note); localStorage.setItem('tempo-last-sync-note',note);
    } catch { setSyncNote('Saved. Start the local service to sync live games; the comparison preview remains available.'); }
    setSyncing(false);
  },[chesscom,lichess,localApi,syncing]);
  const syncRef=useRef(sync); syncRef.current=sync;
  useEffect(()=>{ const run=()=>{if(document.visibilityState==='visible') void syncRef.current()}; const timer=window.setInterval(run,180000); window.addEventListener('focus',run); window.addEventListener('online',run); run(); return()=>{clearInterval(timer);window.removeEventListener('focus',run);window.removeEventListener('online',run)}; },[]); // sync once on mount and then while visible

  useEffect(()=>{if(!engineOn)return;setEngineText('Analyzing selected position…');analyzeWithStockfish(gameFen).then(lines=>setEngineText(lines[0]?`${lines[0].san} · ${lines[0].score}`:'No line')).catch(()=>setEngineText('Local engine unavailable'));},[engineOn,gameFen]);

  return <section className="games-page" id="games">
    <div className="page-heading compact"><div><h1>Games</h1><p>{lastSync?`Last synced at ${new Date(lastSync).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}`:'Automatic local sync checks every 3 minutes'}</p></div><button className="primary-button sync-button" onClick={()=>void sync()} disabled={syncing}>{syncing&&<i/>}{syncing ? 'Syncing games' : '↻ Sync games'}</button></div>
    <section className="account-strip"><label><span>Lichess username</span><input value={lichess} onChange={(e) => setLichess(e.target.value)} placeholder="Optional" /></label><label><span>Chess.com username</span><input value={chesscom} onChange={(e) => setChesscom(e.target.value)} placeholder="Optional" /></label><button onClick={saveAccounts}>Save locally</button><small>{syncNote}</small></section>
    <div className="games-metrics"><article><span>Repertoire adherence</span><strong>78%</strong><small>31 of 40 applicable games</small></article><article><span>Opponent gaps</span><strong>6</strong><small>3 recurring moves</small></article><article><span>Your deviations</span><strong>3</strong><small>French occurs twice</small></article><article><span>No repertoire</span><strong>4</strong><small>Mostly English openings</small></article></div>
    <div className="game-review"><div className="game-board"><Chessboard fen={gameFen} lastMove={gameLast?[gameLast.slice(0,2),gameLast.slice(2,4)]:undefined} locked showHint={false} theme={theme} pieceSet={pieceSet} onMove={()=>undefined}/><div className="board-tools"><button onClick={()=>setCursor(Math.max(0,cursor-1))}>← Back</button><button onClick={()=>setCursor(Math.min(selected.moves.length,cursor+1))}>Forward →</button><button onClick={()=>setCursor(selected.flagPly)}>⚑ First mistake</button><button className={engineOn?'active':''} onClick={()=>setEngineOn(!engineOn)}>Stockfish</button></div></div><aside className="game-inspector"><span className="pill">Quick scan</span><h2>{selected.opening}</h2><strong>{selected.flag}</strong><p>{engineText}</p><div className="game-moves">{selected.moves.map((move,index)=><button className={`${index<cursor?'shown':''}${index===selected.flagPly?' flagged':''}`} onClick={()=>setCursor(index+1)} key={`${move}-${index}`}>{index%2===0?`${Math.floor(index/2)+1}.`:''}{move}</button>)}</div><button className="primary-button" onClick={onAnalyze}>Open gap in builder</button></aside></div>
    <div className="games-workspace"><aside className="gap-list"><span>Recurring repairs</span><button onClick={onAnalyze}><b>French · 7… Nc6</b><small>3 games · add a response</small></button><button onClick={onAnalyze}><b>King’s Indian · 7. d5</b><small>2 games · opponent gap</small></button><button onClick={onAnalyze}><b>Sicilian · 6… e5</b><small>2 personal deviations</small></button></aside><section className="game-list"><div className="game-filters"><select value={source} onChange={(e) => setSource(e.target.value)}><option>All</option><option>Lichess</option><option>Chess.com</option></select><select value={status} onChange={(e) => setStatus(e.target.value)}><option>All</option><option>covered</option><option>opponent gap</option><option>player deviation</option><option>no repertoire</option></select><span>90 days · rated · blitz / rapid / classical</span></div>{games.map((game) => <button className={`game-row${selected.id===game.id?' selected':''}`} key={game.id} onClick={()=>{setSelected(game);setCursor(game.flagPly)}}><span><b>{game.opening}</b><small>{game.source} · {game.date} · {game.speed} · {game.color} · {game.result}</small></span><span><em className={game.status.replace(' ','-')}>{game.status}</em><small>{game.detail}</small></span><i>Review →</i></button>)}</section></div>
  </section>;
}

function ProgressView({ reviewed, cardsLeft, totalCards }: { reviewed: number; cardsLeft: number; totalCards: number }) {
  const days = [
    ['Thu', 16], ['Fri', 22], ['Sat', 8], ['Sun', 28], ['Mon', 19], ['Tue', 31], ['Today', Math.max(8, reviewed)],
  ];
  return (
    <section className="progress-page" id="progress">
      <div className="page-heading compact"><div><p className="eyebrow">Quiet consistency</p><h1>Progress</h1><p>Review volume matters less than returning when each card is due.</p></div><span className="streak">12 day streak</span></div>
      <div className="metric-grid">
        <article><span>Due today</span><strong>{cardsLeft}</strong><small>{cardsLeft ? 'Continue today’s queue' : 'Queue complete'}</small></article>
        <article><span>Cards available</span><strong>{totalCards}</strong><small>Across local repertoires and examples</small></article>
        <article><span>Reviewed today</span><strong>{reviewed}</strong><small>Completed attempts</small></article>
        <article><span>Clean passes</span><strong>{typeof window === 'undefined' ? 0 : JSON.parse(localStorage.getItem('tempo-first-clean-passes') ?? '[]').length}</strong><small>Cards recalled without guidance</small></article>
      </div>
      <div className="analytics-grid">
        <article className="chart-card">
          <div className="chart-heading"><div><span>Review activity</span><strong>{reviewed} cards today</strong></div><small>7 days</small></div>
          <div className="bar-chart">{days.map(([label, value]) => <div className="bar-column" key={label}><span style={{ height: `${Number(value) * 3.3}px` }} /><small>{label}</small></div>)}</div>
        </article>
        <article className="maturity-card"><span>Maturity</span><h2>Most of your repertoire is becoming stable.</h2><div className="donut"><strong>72%</strong><small>learning or mature</small></div><ul><li><i className="new" />New <strong>96</strong></li><li><i className="learning" />Learning <strong>88</strong></li><li><i className="mature" />Mature <strong>184</strong></li></ul></article>
      </div>
    </section>
  );
}

function ImportDialog({ onClose, onImported, onViewRepertoire }: { onClose: () => void; onImported: (repertoire: LocalRepertoire) => void; onViewRepertoire: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [initialDepth, setInitialDepth] = useState(6);
  const [trainedColor, setTrainedColor] = useState<'white' | 'black'>('white');
  const [finished, setFinished] = useState(false);
  const [summary, setSummary] = useState({ lines: 0, duplicates: 0, backend: false });
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');

  async function importFile() {
    if (!file) return;
    setWorking(true);
    setError('');
    try {
      const parsed = parsePgnImport(file.name, await file.text(), trainedColor, initialDepth);
      onImported(parsed.repertoire);
      let backend = false;
      if (['localhost', '127.0.0.1'].includes(location.hostname)) {
        const data = new FormData();
        data.append('file', file);
        data.append('trained_color', trainedColor);
        data.append('initial_depth', String(initialDepth));
        try {
          const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000'}/api/imports/pgn`, { method: 'POST', body: data });
          backend = response.ok;
        } catch { /* The browser-local import remains usable without the service. */ }
      }
      setSummary({ lines: parsed.cards.length, duplicates: parsed.duplicateLines, backend });
      setFinished(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Tempo could not read this PGN.');
    } finally { setWorking(false); }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="import-dialog" role="dialog" aria-modal="true" aria-labelledby="import-title" onMouseDown={(event) => event.stopPropagation()}>
        <button className="close-button" onClick={onClose} aria-label="Close import dialog">×</button>
        {finished ? <div className="import-finished"><span>✓</span><h2 id="import-title">Imported</h2><p><strong>{file?.name}</strong> added {summary.lines} unique {summary.lines === 1 ? 'line' : 'lines'}{summary.duplicates ? ` and merged ${summary.duplicates} duplicate ${summary.duplicates === 1 ? 'line' : 'lines'}` : ''}. {summary.backend ? 'The local database and today’s practice are updated.' : 'This browser’s repertoire and practice queue are updated.'}</p><button className="primary-button" onClick={() => { onClose(); onViewRepertoire(); }}>View imported repertoire</button></div> : <>
          <p className="eyebrow">Local import</p><h2 id="import-title">Add PGN repertoire</h2><p className="dialog-copy">Your file is parsed on this computer. Re-uploading the same positions updates the repertoire without duplicating cards.</p>
          <label className={`drop-zone${file ? ' has-file' : ''}`}><input type="file" accept=".pgn" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setError(''); }} /><span>{file ? '♟' : '⇧'}</span><strong>{file?.name || 'Choose a PGN file'}</strong><small>{file ? 'Ready to import' : '.pgn files only'}</small></label>
          <div className="color-setting"><span><strong>Side to train</strong><small>Only your moves count toward line depth</small></span><span className="color-toggle"><button className={trainedColor === 'white' ? 'active' : ''} onClick={() => setTrainedColor('white')}>White</button><button className={trainedColor === 'black' ? 'active' : ''} onClick={() => setTrainedColor('black')}>Black</button></span></div>
          <label className="depth-setting"><span><strong>Initial line depth</strong><small>New prefix cards test this many user moves</small></span><span className="stepper"><button onClick={() => setInitialDepth(Math.max(2, initialDepth - 1))}>−</button><b>{initialDepth} user moves</b><button onClick={() => setInitialDepth(Math.min(20, initialDepth + 1))}>＋</button></span></label>
          {error && <p className="editor-error">{error}</p>}
          <div className="dialog-footer"><span><i className="status-dot" /> Stored locally</span><button className="primary-button" disabled={!file || working} onClick={() => void importFile()}>{working ? 'Importing…' : 'Import repertoire'}</button></div>
        </>}
      </section>
    </div>
  );
}

export default function Home() {
  const [view, setView] = useState<View>('train');
  const [practiceCards,setPracticeCards]=useState<PracticeCard[]>([...demoCards]);
  const [importedRepertoires, setImportedRepertoires] = useState<LocalRepertoire[]>([]);
  const [activeCardIndex, setActiveCardIndex] = useState(0);
  const [fen, setFen] = useState(demoCards[0].startingFen);
  const [step, setStep] = useState(0);
  const [feedback, setFeedback] = useState<Feedback>('ready');
  const [lastMove, setLastMove] = useState<[string, string]>();
  const [locked, setLocked] = useState(false);
  const [showHint, setShowHint] = useState(false);
  const [cardsLeft, setCardsLeft] = useState(12);
  const [reviewed, setReviewed] = useState(0);
  const [showImport, setShowImport] = useState(false);
  const [showTree, setShowTree] = useState(false);
  const [editorCard,setEditorCard]=useState<PracticeCard|null>(null);
  const [suggestShorter,setSuggestShorter]=useState(false);
  const [seenMoves, setSeenMoves] = useState<Set<string>>(new Set());
  const [firstCleanPasses, setFirstCleanPasses] = useState<Set<string>>(new Set());
  const [attemptFailed, setAttemptFailed] = useState(false);
  const [dailyQueue, setDailyQueue] = useState<number[]>(Array.from({ length: 12 }, (_, index) => index % demoCards.length));
  const [queueNotice, setQueueNotice] = useState('');
  const [boardTheme, setBoardTheme] = useState<BoardTheme>('brown');
  const [pieceSet, setPieceSet] = useState<PieceSet>('cburnett');
  const card = practiceCards[activeCardIndex] ?? practiceCards[0];
  const repertoireLine = card.moves;

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has('code') || sessionStorage.getItem('tempo-return-view') === 'analysis') {
      setView('analysis');
      sessionStorage.removeItem('tempo-return-view');
    }
    const today = localDayKey();
    if (localStorage.getItem('tempo-day') !== today) {
      localStorage.setItem('tempo-day', today);
      localStorage.setItem('tempo-cards-left', '12');
      localStorage.setItem('tempo-reviewed', '0');
      localStorage.setItem('tempo-daily-queue', JSON.stringify(Array.from({ length: 12 }, (_, index) => index % demoCards.length)));
    }
    setCardsLeft(Number(localStorage.getItem('tempo-cards-left') ?? 12));
    setReviewed(Number(localStorage.getItem('tempo-reviewed') ?? 0));
    setSeenMoves(new Set(JSON.parse(localStorage.getItem('tempo-seen-moves') ?? '[]')));
    setFirstCleanPasses(new Set(JSON.parse(localStorage.getItem('tempo-first-clean-passes') ?? '[]')));
    const savedRepertoires = JSON.parse(localStorage.getItem('tempo-imported-repertoires') ?? '[]') as LocalRepertoire[];
    setImportedRepertoires(savedRepertoires);
    const savedCards = [...new Map(savedRepertoires.flatMap((repertoire) => repertoire.cards).map((savedCard) => [savedCard.id, savedCard])).values()];
    setPracticeCards([...demoCards, ...savedCards]);
    const storedQueue = JSON.parse(localStorage.getItem('tempo-daily-queue') ?? JSON.stringify(Array.from({ length: 12 }, (_, index) => index % demoCards.length))) as number[];
    setDailyQueue(storedQueue); setCardsLeft(storedQueue.length); setActiveCardIndex(storedQueue[0] ?? 0);
    setBoardTheme((localStorage.getItem('tempo-board-theme') as BoardTheme | null) ?? 'brown');
    setPieceSet((localStorage.getItem('tempo-piece-set') as PieceSet | null) ?? 'cburnett');
  }, []);

  function addImportedRepertoire(repertoire: LocalRepertoire) {
    const repertoires = [...importedRepertoires.filter((item) => item.id !== repertoire.id), repertoire];
    setImportedRepertoires(repertoires);
    localStorage.setItem('tempo-imported-repertoires', JSON.stringify(repertoires));
    const existingIds = new Set(practiceCards.map((item) => item.id));
    const additions = repertoire.cards.filter((item) => !existingIds.has(item.id));
    const cards = [...practiceCards, ...additions];
    setPracticeCards(cards);
    const addedIndexes = additions.map((item) => cards.findIndex((cardItem) => cardItem.id === item.id));
    const queue = [...dailyQueue, ...addedIndexes];
    setDailyQueue(queue);
    setCardsLeft(queue.length);
    localStorage.setItem('tempo-daily-queue', JSON.stringify(queue));
    localStorage.setItem('tempo-cards-left', String(queue.length));
  }

  function resetLine(nextCard = card) {
    setFen(nextCard.startingFen); setStep(0); setFeedback('ready'); setLastMove(undefined); setLocked(false); setShowHint(false); setAttemptFailed(false);
  }

  function changeBoardTheme(value: BoardTheme) {
    setBoardTheme(value);
    localStorage.setItem('tempo-board-theme', value);
  }

  function changePieceSet(value: PieceSet) {
    setPieceSet(value);
    localStorage.setItem('tempo-piece-set', value);
  }

  function rateCard(outcome: 'again' | 'correct') {
    const firstClean = outcome === 'correct' && !firstCleanPasses.has(card.id);
    const nextQueue = dailyQueue.slice(1);
    if (outcome === 'again') {
      const key=`tempo-failures-${card.id}`; const failures=Number(localStorage.getItem(key)??0)+1; localStorage.setItem(key,String(failures)); if(failures>=3)setSuggestShorter(true);
      nextQueue.splice(Math.min(4, nextQueue.length), 0, activeCardIndex);
      setQueueNotice('Guided review complete · shuffled behind 4 cards');
    } else if (firstClean) {
      nextQueue.push(activeCardIndex);
      const nextPasses = new Set(firstCleanPasses).add(card.id); setFirstCleanPasses(nextPasses);
      localStorage.setItem('tempo-first-clean-passes', JSON.stringify([...nextPasses]));
      setQueueNotice('First clean solve · one reinforcement at the end of today’s queue');
    } else setQueueNotice('Correct · next review scheduled by FSRS');
    setDailyQueue(nextQueue); localStorage.setItem('tempo-daily-queue', JSON.stringify(nextQueue));
    setCardsLeft(nextQueue.length); localStorage.setItem('tempo-cards-left', String(nextQueue.length));
    setReviewed((count) => { const next = count + 1; localStorage.setItem('tempo-reviewed', String(next)); return next; });
    const nextIndex = nextQueue[0] ?? 0;
    setActiveCardIndex(nextIndex);
    resetLine(practiceCards[nextIndex]);
  }

  function markMoveSeen(moveStep: number) {
    const key = `${card.id}:${moveStep}`;
    setSeenMoves((current) => {
      const next = new Set(current).add(key);
      localStorage.setItem('tempo-seen-moves', JSON.stringify(Array.from(next)));
      return next;
    });
  }

  function tryMove(from: Square, to: Square) {
    if (locked || step >= repertoireLine.length || step % 2 === 1) return;
    const position = new Chess(fen);
    let move: Move | null = null;
    try { move = position.move({ from, to, promotion: 'q' }); } catch {
      setFeedback('wrong'); setShowHint(true); setAttemptFailed(true); setQueueNotice('Again recorded · replay the guided move'); return;
    }
    if (!move) return;
    if (position.isCheckmate()) { setFeedback('complete'); setTimeout(() => rateCard(attemptFailed ? 'again' : 'correct'), 650); return; }
    if (move.san !== repertoireLine[step]) {
      const alternateBranch = demoCards.some((other) => other.id !== card.id && other.startingFen === card.startingFen && other.moves.slice(0, step).every((san, index) => san === repertoireLine[index]) && other.moves[step] === move?.san);
      if (alternateBranch) { setFeedback('branch'); setShowHint(true); setQueueNotice('Valid repertoire move · follow the arrow for today’s branch'); return; }
      setFeedback('wrong'); setShowHint(true); setAttemptFailed(true); setQueueNotice('Again recorded · replay this move, then finish the line'); return;
    }
    markMoveSeen(step);
    setFen(position.fen()); setLastMove([move.from, move.to]); setFeedback('correct'); setShowHint(false);
    setQueueNotice('');
    const opponentStep = step + 1;
    setStep(opponentStep);
    if (opponentStep >= repertoireLine.length) { setFeedback('complete'); setTimeout(() => rateCard(attemptFailed ? 'again' : 'correct'), 650); return; }
    setLocked(true);
    window.setTimeout(() => {
      const replyPosition = new Chess(position.fen());
      const reply = replyPosition.move(repertoireLine[opponentStep]);
      const nextStep = opponentStep + 1;
      setFen(replyPosition.fen()); setLastMove([reply.from, reply.to]); setStep(nextStep); setLocked(false); setFeedback(nextStep >= repertoireLine.length ? 'complete' : 'ready');
      if (nextStep >= repertoireLine.length) setTimeout(() => rateCard(attemptFailed ? 'again' : 'correct'), 650);
    }, 420);
  }

  const feedbackCopy = {
    ready: { title: step === 0 ? 'Your move' : 'Find the continuation', body: card.kind === 'puzzle' ? 'Find the strongest continuation.' : step === 0 ? 'Recall White’s first move.' : 'Continue the line for White.' },
    correct: { title: 'That’s it', body: 'Black is replying…' },
    branch: { title: 'Also in your repertoire', body: 'That move is valid. Replay the arrowed move for the branch being tested.' },
    wrong: { title: 'Try that position again', body: 'That move is legal, but it isn’t in this repertoire.' },
    complete: { title: attemptFailed ? 'Guided line complete' : 'Line recalled', body: attemptFailed ? 'Again will return after four other cards.' : 'Correct is being recorded automatically.' },
  }[feedback];

  const currentMoveKey = `${card.id}:${step}`;
  const showTeachingArrow = step % 2 === 0 && step < repertoireLine.length && (showHint || feedback === 'wrong' || !seenMoves.has(currentMoveKey));
  const analysisUrl = lichessAnalysisUrl(repertoireLine.slice(0, step), card.startingFen);
  const revealedMoves = repertoireLine.slice(0, feedback === 'complete' ? repertoireLine.length : step);

  const dateLabel = new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric' }).format(new Date());

  return (
    <main className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => setView('train')} aria-label="Tempo home"><span className="brand-mark">T</span><span>Tempo</span></button>
        <nav className="nav" aria-label="Primary navigation">
          {(['train','tactics','endgames','repertoire','analysis','games','progress'] as View[]).map((item) => <button className={view === item ? 'active' : ''} key={item} onClick={() => setView(item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}
        </nav>
        <button className="local-status" onClick={() => setShowImport(true)}><span className="status-dot" /> Saved locally</button>
      </header>

      {view === 'train' && <>
        <section className="training-header"><div><p className="eyebrow">Today · {dateLabel}</p><h1>{cardsLeft === 0 ? 'You’re done for today' : 'Daily training'}</h1></div><div className="session-count"><strong>{cardsLeft}</strong><span>cards left</span></div></section>
        <section className="training-grid" id="train">
          <div className="board-column">
            <Chessboard fen={fen} expectedSan={repertoireLine[step]} lastMove={lastMove} locked={locked || step >= repertoireLine.length || cardsLeft === 0} showHint={showTeachingArrow} theme={boardTheme} pieceSet={pieceSet} onMove={tryMove} />
            <div className="board-tools"><button onClick={() => { if(!attemptFailed){setAttemptFailed(true);setQueueNotice('Again recorded · finish with guidance');} setShowHint((value) => !value); }} disabled={feedback === 'complete' || cardsLeft === 0}>⌁ <span>{showHint ? 'Hide move' : 'Show move'}</span></button><button onClick={() => { resetLine(); setAttemptFailed(true); setShowHint(true); setQueueNotice('Again recorded · restarted in guided mode'); }}>↻ <span>Restart</span></button><a href={analysisUrl} onClick={()=>{if(!attemptFailed) rateCard('again')}} target="_blank" rel="noreferrer">↗ <span>Analyze</span></a><button onClick={()=>setEditorCard(card)}>✎ <span>Edit card</span></button><label>Board<select value={boardTheme} onChange={(event) => changeBoardTheme(event.target.value as BoardTheme)}><option value="brown">Brown</option><option value="blue">Blue</option><option value="green">Green</option></select></label><label>Pieces<select value={pieceSet} onChange={(event) => changePieceSet(event.target.value as PieceSet)}><option value="cburnett">Cburnett</option><option value="merida">Merida</option></select></label></div>
          </div>
          <aside className="study-panel">
            <div className="card-meta"><span className={`pill${card.kind === 'puzzle' ? ' puzzle' : ''}`}>{card.kind === 'puzzle' ? 'Puzzle' : 'Review'}</span>{queueNotice && <em>{queueNotice}</em>}</div>
            <div className="opening-title"><h2>{card.title}</h2><span>{card.subtitle}</span></div>
            <div className={`feedback ${feedback}`} role="status" aria-live="polite"><span className="feedback-icon">{feedback === 'wrong' ? '×' : feedback === 'complete' ? '✓' : '●'}</span><div><strong>{feedbackCopy.title}</strong><p>{feedbackCopy.body}</p></div></div>
            <div className={`move-trail${revealedMoves.length ? '' : ' empty'}`} aria-live="polite"><span>Moves played</span>{revealedMoves.length ? <ol>{revealedMoves.map((move, index) => <li key={`${move}-${index}`}><b>{index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : '…'}</b>{move}</li>)}</ol> : <p>Nothing is revealed until you play it.</p>}</div>
            {suggestShorter&&card.kind==='opening'&&<div className="shorten-suggestion"><strong>This prefix may be carrying too much at once.</strong><button onClick={()=>setEditorCard({...card,moves:card.moves.slice(0,-2)})}>Preview one move shorter</button></div>}
            <div className="ratings binary"><button onClick={() => { setAttemptFailed(true); setShowHint(true); setQueueNotice('Again recorded · finish the line with guidance'); }}><strong>Again</strong></button><button className="primary" disabled={attemptFailed} onClick={() => rateCard('correct')}><strong>{attemptFailed?'Finish on the board':'Correct'}</strong></button></div>
          </aside>
        </section>
      </>}
      {view === 'tactics' && (
        <TacticsView theme={boardTheme} pieceSet={pieceSet}/>
      )}
      {view === 'endgames' && (
        <EndgamesView theme={boardTheme} pieceSet={pieceSet}/>
      )}
      {view === 'repertoire' && <RepertoireView imported={importedRepertoires} onImport={() => setShowImport(true)} onBrowse={() => setShowTree(true)} />}
      {view === 'analysis' && <AnalysisView theme={boardTheme} pieceSet={pieceSet} imported={importedRepertoires} onTheme={changeBoardTheme} onPieces={changePieceSet} />}
      {view === 'games' && <GamesView onAnalyze={() => setView('analysis')} theme={boardTheme} pieceSet={pieceSet} />}
      {view === 'progress' && <ProgressView reviewed={reviewed} cardsLeft={cardsLeft} totalCards={practiceCards.length} />}
      {showImport && <ImportDialog onClose={() => setShowImport(false)} onImported={addImportedRepertoire} onViewRepertoire={() => setView('repertoire')} />}
      {showTree && <TreeBrowser onClose={() => setShowTree(false)} theme={boardTheme} pieceSet={pieceSet} />}
      {editorCard && <CardEditor card={editorCard} theme={boardTheme} pieceSet={pieceSet} onClose={()=>setEditorCard(null)} onSave={(updated)=>{setPracticeCards(current=>current.map(item=>item.id===updated.id?updated:item));resetLine(updated);setSuggestShorter(false);}}/>}
      <footer className="source-footer">Board interaction by <a href="https://github.com/lichess-org/chessground" target="_blank" rel="noreferrer">Chessground</a> · Cburnett and Merida pieces from Lichess · Puzzle positions from the public-domain <a href="https://database.lichess.org/#puzzles" target="_blank" rel="noreferrer">Lichess database</a></footer>
    </main>
  );
}
