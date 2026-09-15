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
import { moveSoundEnabled, playMoveSound } from './lib/move-sound';
import { advanceTacticProgress, readTacticProgress, tacticProgressKey, writeTacticProgress } from './lib/tactics-progress';

const STANDARD_FEN = new Chess().fen();
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000';

function usesLocalApi() {
  return typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(location.hostname);
}

function sanLine(startingFen: string, moves: string[]) {
  const board = new Chess(startingFen);
  return moves.map((value) => {
    const move = /^[a-h][1-8][a-h][1-8][qrbn]?$/.test(value)
      ? board.move({ from: value.slice(0, 2) as Square, to: value.slice(2, 4) as Square, promotion: value[4] })
      : board.move(value);
    return move.san;
  });
}

type BackendQueueCard = {
  id: string; queue_entry_id: number; start_fen: string; moves: string[];
  content_type: 'opening' | 'tactic' | 'endgame'; repertoire_name: string;
  repertoire_source: string; source_ref?: string; is_main?: number;
  trained_color?: 'white' | 'black';
};

function practiceCardFromQueue(card: BackendQueueCard): PracticeCard {
  return {
    id: `queue-${card.queue_entry_id}`,
    backendId: card.id,
    queueEntryId: card.queue_entry_id,
    kind: card.content_type === 'tactic' ? 'puzzle' : card.content_type === 'endgame' ? 'endgame' : 'opening',
    title: card.content_type === 'tactic' ? 'Tactics review' : card.content_type === 'endgame' ? 'Endgame study' : card.repertoire_name,
    subtitle: card.content_type === 'tactic' ? `Lichess puzzle ${card.source_ref ?? ''}` : card.repertoire_source,
    startingFen: card.start_fen,
    moves: card.content_type === 'endgame' ? [] : sanLine(card.start_fen, card.moves),
    userMoveTarget: Math.ceil(card.moves.length / 2),
    sourceUrl: card.source_ref ? `https://lichess.org/training/${card.source_ref}` : undefined,
    orientation: card.content_type === 'tactic'
      ? (new Chess(card.start_fen).turn() === 'b' ? 'black' : 'white')
      : card.trained_color ?? 'white',
  };
}
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
type View = 'train' | 'tactics' | 'endgames' | 'repertoire' | 'analysis' | 'games' | 'progress' | 'settings';

function trainedColor(card: PracticeCard): 'white' | 'black' {
  if (card.orientation) return card.orientation;
  return card.kind === 'opening' ? 'white' : new Chess(card.startingFen).turn() === 'b' ? 'black' : 'white';
}

function initialTrainingState(card: PracticeCard) {
  const position = new Chess(card.startingFen);
  const turn = position.turn() === 'b' ? 'black' : 'white';
  if (card.kind === 'opening' && turn !== trainedColor(card) && card.moves[0]) {
    const move = position.move(card.moves[0]);
    return { fen: position.fen(), step: 1, lastMove: [move.from, move.to] as [string, string] };
  }
  return { fen: card.startingFen, step: 0, lastMove: undefined };
}

type ExplorerMove = {
  uci: string;
  san: string;
  white: number;
  draws: number;
  black: number;
};

type AnalysisLine = {
  id: string; repertoireId: string; repertoireName: string; title: string;
  side: 'White' | 'Black'; moves: string[]; startingFen: string;
};

function lineMoveName(line: Pick<AnalysisLine, 'title' | 'moves' | 'startingFen'>) {
  if (line.title && !/^(analysis branch|line|variation)$/i.test(line.title.trim())) return line.title;
  try { return sanLine(line.startingFen, line.moves).join(' '); } catch { return line.moves.join(' '); }
}

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

function AnalysisView({ theme, pieceSet, imported }: { theme: BoardTheme; pieceSet: PieceSet; imported: LocalRepertoire[] }) {
  const [history, setHistory] = useState<{ san: string; uci: string; fen: string }[]>([]);
  const [cursor, setCursor] = useState(0);
  const [explorerOn, setExplorerOn] = useState(() => typeof window === 'undefined' || localStorage.getItem('tempo-explorer-on') !== 'false');
  const [stockfishOn, setStockfishOn] = useState(() => typeof window !== 'undefined' && localStorage.getItem('tempo-stockfish-on') === 'true');
  const [maiaOn, setMaiaOn] = useState(() => typeof window !== 'undefined' && localStorage.getItem('tempo-maia-on') === 'true');
  const [maiaElo] = useState(() => typeof window === 'undefined' ? '1500' : localStorage.getItem('tempo-maia-elo') ?? '1500');
  const [coverageTarget] = useState(() => typeof window === 'undefined' ? 90 : Number(localStorage.getItem('tempo-coverage-target') ?? 90));
  const [engineWindowCp] = useState(() => typeof window === 'undefined' ? 30 : Number(localStorage.getItem('tempo-engine-window-cp') ?? 30));
  const [lichessToken, setLichessToken] = useState(() => typeof window === 'undefined' ? '' : sessionStorage.getItem('tempo-lichess-token') ?? '');
  const [explorerMoves, setExplorerMoves] = useState<ExplorerMove[]>([]);
  const [mastersMoves, setMastersMoves] = useState<ExplorerMove[]>([]);
  const [explorerSpeeds] = useState(() => typeof window === 'undefined' ? 'blitz,rapid,classical' : localStorage.getItem('tempo-explorer-speeds') ?? 'blitz,rapid,classical');
  const [explorerRatings] = useState(() => typeof window === 'undefined' ? '1600,1800,2000,2200,2500' : localStorage.getItem('tempo-explorer-ratings') ?? '1600,1800,2000,2200,2500');
  const [branchStart, setBranchStart] = useState<number | null>(null);
  const [branchNote, setBranchNote] = useState('');
  const [explorerState, setExplorerState] = useState<'auth' | 'loading' | 'ready' | 'error'>(lichessToken ? 'loading' : 'auth');
  const [stockfishMoves, setStockfishMoves] = useState<EngineMove[]>([]);
  const [stockfishState, setStockfishState] = useState<'off' | 'loading' | 'ready' | 'error'>(stockfishOn ? 'loading' : 'off');
  const [maiaMoves, setMaiaMoves] = useState<EngineMove[]>([]);
  const [maiaState, setMaiaState] = useState<'off' | 'loading' | 'ready' | 'error'>(maiaOn ? 'loading' : 'off');
  const [maiaProgress, setMaiaProgress] = useState(0);
  const [backendLines, setBackendLines] = useState<AnalysisLine[]>([]);
  const [orientation, setOrientation] = useState<'white' | 'black'>(() => typeof window === 'undefined' ? 'white' : (localStorage.getItem('tempo-builder-orientation') as 'white' | 'black' | null) ?? 'white');
  const [activeRepertoire, setActiveRepertoire] = useState(() => typeof window === 'undefined' ? '' : localStorage.getItem('tempo-active-repertoire-white') ?? '');
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchIndex, setSearchIndex] = useState(0);
  const [hoveredMove, setHoveredMove] = useState<string | null>(null);
  const arrowMetric=(typeof window==='undefined'?'stockfish':localStorage.getItem('tempo-arrow-metric')??'stockfish') as 'stockfish'|'lichess'|'masters';
  const visibleHistory = history.slice(0, cursor);
  const fen = visibleHistory.at(-1)?.fen ?? STANDARD_FEN;
  const previousUci = visibleHistory.at(-1)?.uci;
  const lastMove: [string, string] | undefined = previousUci ? [previousUci.slice(0, 2), previousUci.slice(2, 4)] : undefined;
  const playedUci = visibleHistory.map((move) => move.uci);
  const availableLines = useMemo<AnalysisLine[]>(() => [
    ...backendLines,
    ...analysisLines.map((line, index) => ({ id:`sample-${index}`, repertoireId:`sample-${line.side.toLowerCase()}`, repertoireName:`${line.side} examples`, ...line, startingFen: STANDARD_FEN })),
    ...imported.flatMap((repertoire) => repertoire.cards.map((card) => ({ id:card.id, repertoireId:repertoire.id, repertoireName:repertoire.title, title:card.title, side: repertoire.side, moves: card.moves, startingFen: card.startingFen }))),
  ], [backendLines, imported]);
  const repertoires = [...new Map(availableLines.map((line) => [line.repertoireId, { id:line.repertoireId, name:line.repertoireName, side:line.side }])).values()];
  const selectedRepertoire = repertoires.find((item) => item.id === activeRepertoire && item.side.toLowerCase() === orientation) ?? repertoires.find((item) => item.side.toLowerCase() === orientation);
  const lineMatches = availableLines.filter((line) => (!selectedRepertoire || line.repertoireId === selectedRepertoire.id) && line.startingFen.split(' ').slice(0,4).join(' ') === STANDARD_FEN.split(' ').slice(0,4).join(' ') && playedUci.every((move, index) => uciLine(line.moves, line.startingFen)[index] === move));
  const coveredReplies = new Set(lineMatches.flatMap((line) => {
    const uci = uciLine(line.moves, line.startingFen)[cursor];
    return uci ? [uci] : [];
  }));

  function rememberToggle(key: string, value: boolean, setter: (next: boolean) => void) {
    localStorage.setItem(key, String(value));
    setter(value);
  }

  useEffect(() => {
    if (!usesLocalApi()) return;
    void fetch(`${API_URL}/api/repertoire/lines`).then((response) => response.ok ? response.json() : Promise.reject())
      .then((body: { lines: Array<{ id:string; repertoire_id:string; repertoire_name:string; name:string; trained_color:'white'|'black'; start_fen:string; moves:string[] }> }) => {
        setBackendLines(body.lines.map((line) => ({ id:line.id, repertoireId:line.repertoire_id, repertoireName:line.repertoire_name, title:line.name, side:line.trained_color === 'black' ? 'Black' : 'White', startingFen:line.start_fen, moves:line.moves })));
      }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!selectedRepertoire) return;
    if (activeRepertoire !== selectedRepertoire.id) setActiveRepertoire(selectedRepertoire.id);
    localStorage.setItem(`tempo-active-repertoire-${orientation}`, selectedRepertoire.id);
  }, [activeRepertoire, orientation, selectedRepertoire]);

  const flipBuilder = useCallback(() => {
    setOrientation((current) => {
      const next = current === 'white' ? 'black' : 'white';
      localStorage.setItem('tempo-builder-orientation', next);
      setActiveRepertoire(localStorage.getItem(`tempo-active-repertoire-${next}`) ?? '');
      return next;
    });
  }, []);

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
      if (event.key === 'Escape' && searchOpen) { event.preventDefault(); setSearchOpen(false); }
      if (event.key === 'ArrowDown' && searchOpen) { event.preventDefault(); setSearchIndex((value)=>Math.min(Math.max(0,lineMatches.length-1),value+1)); }
      if (event.key === 'ArrowUp' && searchOpen) { event.preventDefault(); setSearchIndex((value)=>Math.max(0,value-1)); }
      if (event.key === 'Enter' && searchOpen && lineMatches[searchIndex]) { event.preventDefault(); setSearchOpen(false); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [flipBuilder, history.length, lineMatches, searchIndex, searchOpen]);

  useEffect(() => {
    setExplorerMoves([]); setMastersMoves([]);
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
    setStockfishMoves([]); setStockfishState('loading');
    analyzeWithStockfish(fen).then((moves) => { if (current) { setStockfishMoves(moves); setStockfishState('ready'); } }).catch((error) => { console.error('Stockfish 19:', error); if (current) setStockfishState('error'); });
    return () => { current = false; };
  }, [fen, stockfishOn]);

  useEffect(() => {
    if (!maiaOn) { setMaiaState('off'); setMaiaMoves([]); return; }
    let current = true;
    setMaiaMoves([]); setMaiaProgress(0); setMaiaState('loading');
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
  const explorerCandidates = candidatesThrough(explorerMoves, (move) => move.white + move.draws + move.black, target).filter((move) => !coveredReplies.has(move.uci));
  const mastersCandidates = candidatesThrough(mastersMoves, (move) => move.white + move.draws + move.black, target).filter((move) => !coveredReplies.has(move.uci));
  const maiaCandidates = candidatesThrough(maiaMoves, (move) => move.probability ?? 0, target).filter((move) => !coveredReplies.has(move.uci));
  const explorerTotal = explorerMoves.reduce((sum, move) => sum + move.white + move.draws + move.black, 0);
  const explorerCovered = explorerMoves.filter((move) => coveredReplies.has(move.uci)).reduce((sum, move) => sum + move.white + move.draws + move.black, 0);
  const maiaCovered = maiaMoves.filter((move) => coveredReplies.has(move.uci)).reduce((sum, move) => sum + (move.probability ?? 0), 0);
  const topEngine = stockfishMoves[0];
  const engineCandidates = stockfishMoves.filter((move, index) => {
    if (index >= 5) return false;
    if (topEngine?.mate !== undefined) return move.mate !== undefined;
    if (topEngine?.cp === undefined || move.cp === undefined) return index === 0;
    return topEngine.cp - move.cp <= engineWindowCp;
  }).filter((move) => !coveredReplies.has(move.uci));
  const arrowSources = new Map<string, Set<string>>();
  for (const move of explorerCandidates) arrowSources.set(move.uci, new Set([...(arrowSources.get(move.uci) ?? []), 'L']));
  for (const move of mastersCandidates) arrowSources.set(move.uci, new Set([...(arrowSources.get(move.uci) ?? []), 'D']));
  for (const move of engineCandidates) arrowSources.set(move.uci, new Set([...(arrowSources.get(move.uci) ?? []), 'S']));
  for (const move of maiaCandidates) arrowSources.set(move.uci, new Set([...(arrowSources.get(move.uci) ?? []), 'M']));
  for (const move of coveredReplies) arrowSources.set(move, new Set([...(arrowSources.get(move) ?? []), 'R']));
  const trainedTurn = new Chess(fen).turn() === (orientation === 'white' ? 'w' : 'b');
  const repertoireMoves = [...coveredReplies].map((uci) => {
    const board=new Chess(fen); const move=board.move({from:uci.slice(0,2) as Square,to:uci.slice(2,4) as Square,promotion:uci[4]||'q'});
    return {uci,san:move.san};
  });
  const practicalMoves=arrowMetric==='masters'?mastersMoves:explorerMoves;
  const practicalScores=new Map(practicalMoves.map((move)=>{const total=move.white+move.draws+move.black; const won=orientation==='white'?move.white:move.black; return [move.uci,total>=100?(won+move.draws/2)/total:undefined];}));
  const bestPractical=Math.max(0,...[...practicalScores.values()].filter((value):value is number=>value!==undefined));
  const shapes: DrawShape[] = (hoveredMove ? [[hoveredMove, new Set([''])] as const] : [...arrowSources.entries()].slice(0, 9)).map(([uci, sources]) => {
    const engineIndex = engineCandidates.findIndex((move) => move.uci === uci);
    const practical=practicalScores.get(uci);
    const graded=arrowMetric==='stockfish'?(engineIndex===0?'green':engineIndex>0?'blue':'red'):(practical===undefined?'blue':practical===bestPractical?'green':bestPractical-practical<=.05?'blue':'red');
    const brush = sources.has('R') ? 'yellow' : !trainedTurn ? 'blue' : graded;
    return {
    orig: uci.slice(0, 2) as Key,
    dest: uci.slice(2, 4) as Key,
    brush,
    label: hoveredMove ? undefined : { text: [...sources].filter((source) => source !== 'R').join('·') || 'R' },
  }; });

  function reset() { setHistory([]); setCursor(0); }
  function disconnectLichess() { sessionStorage.removeItem('tempo-lichess-token'); setLichessToken(''); setExplorerMoves([]); }

  return <section className="analysis-page" id="analysis">
    <div className="analysis-heading compact-analysis"><h1>Builder</h1><div className="analysis-switches"><select aria-label="Active repertoire" value={selectedRepertoire?.id ?? ''} onChange={(event) => { const selected=repertoires.find((item)=>item.id===event.target.value); if(!selected)return; const side=selected.side.toLowerCase() as 'white'|'black'; setOrientation(side); setActiveRepertoire(selected.id); localStorage.setItem(`tempo-active-repertoire-${side}`,selected.id); localStorage.setItem('tempo-builder-orientation',side); }}><option value="" disabled>Choose repertoire</option>{repertoires.map((item)=><option key={item.id} value={item.id}>{item.name} · {item.side}</option>)}</select><button title="Flip board (F)" onClick={flipBuilder}>⇅ {orientation === 'white' ? 'White' : 'Black'}</button><button className={stockfishOn ? 'on' : ''} onClick={() => rememberToggle('tempo-stockfish-on', !stockfishOn, setStockfishOn)}><i /> Stockfish 19</button><button className={maiaOn ? 'on' : ''} onClick={() => rememberToggle('tempo-maia-on', !maiaOn, setMaiaOn)}><i /> Maia 3</button></div></div>
    <div className="analysis-layout">
      <div className="analysis-board-column">
        <Chessboard fen={fen} lastMove={lastMove} locked={false} showHint={false} theme={theme} pieceSet={pieceSet} shapes={shapes} onMove={playMove} orientation={orientation} onFlip={flipBuilder} />
        <div className="arrow-legend"><span><i className="known" /> Covered</span><span><i className="candidate" /> Gap</span><span tabIndex={0} title="R: already in your repertoire"><b>R</b> Repertoire</span><span tabIndex={0} title="L: Lichess opening explorer"><b>L</b> Lichess</span><span tabIndex={0} title="D: Lichess Masters database"><b>D</b> Masters</span><span tabIndex={0} title="S: Stockfish engine line"><b>S</b> Stockfish</span><span tabIndex={0} title="M: Maia human-likelihood model"><b>M</b> Maia</span></div>
        <div className="board-tools"><button onClick={() => setCursor((value) => Math.max(0, value - 1))} disabled={!cursor}>← <span>Back</span></button><button onClick={() => setCursor((value) => Math.min(history.length, value + 1))} disabled={cursor === history.length}>→ <span>Forward</span></button><button onClick={reset}>↻ <span>Reset</span></button><a href={`https://lichess.org/analysis/standard/${encodeURIComponent(fen)}`} target="_blank" rel="noreferrer">↗ <span>Open in Lichess</span></a></div>
        <div className="analysis-moves"><span>{history.length ? history.map((move, index) => <button className={index < cursor ? 'shown' : ''} key={`${move.uci}-${index}`} onClick={() => setCursor(index + 1)}>{index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : ''}{move.san}</button>) : 'Make a move to search your repertoire'}</span><small>←/→ move · Home/End jump</small><button onClick={() => navigator.clipboard?.writeText(fen)}>Copy FEN</button></div>
        <div className="branch-editor"><span><b>{branchStart === null ? 'Branch editor' : `Drafting from ply ${branchStart}`}</b><small>{branchNote || 'Select an opponent move, then play your response and any continuation.'}</small></span>{branchStart === null ? <button onClick={() => { setBranchStart(cursor); setBranchNote(''); }}>＋ Add branch here</button> : <><button className="save" onClick={saveBranch}>Save branch</button><button onClick={() => { setHistory((h) => h.slice(0, branchStart)); setCursor(branchStart); setBranchStart(null); }}>Cancel</button></>}</div>
      </div>
      <aside className="analysis-sidebar">
        <section className="analysis-panel repertoire-panel"><div className="panel-heading"><div><span>Active repertoire</span><strong>{selectedRepertoire?.name??'No repertoire selected'}</strong></div></div>{repertoireMoves.length?<MoveRows moves={repertoireMoves} covered={coveredReplies} detail="score" onPlay={playUci} onHover={setHoveredMove}/>:<p className="panel-message">No saved response at this position.</p>}</section>
        <section className="analysis-panel coverage-panel"><div className="panel-heading"><div><span>Coverage</span><strong>Likely {coverageTarget}% · change in Settings</strong></div></div><div className="coverage-summary"><div><span>Lichess coverage</span><strong>{explorerTotal ? Math.round(explorerCovered / explorerTotal * 100) : '—'}%</strong></div><div><span>Maia coverage</span><strong>{maiaMoves.length ? Math.round(maiaCovered * 100) : '—'}%</strong></div><div><span>Responses saved</span><strong>{coveredReplies.size}</strong></div></div></section>
        <button className="analysis-panel repertoire-results position-preview" onClick={()=>{setSearchIndex(0);setSearchOpen(true);}}><div className="panel-heading"><div><span>Position search</span><strong>{lineMatches.length ? `${lineMatches.length} repertoire ${lineMatches.length === 1 ? 'match' : 'matches'}` : 'Repertoire gap'}</strong></div><b className={lineMatches.length ? 'covered' : 'gap'}>{lineMatches.length ? 'Browse' : 'Add'}</b></div>{lineMatches.slice(0,2).map((line)=><span className="line-result" key={line.id}><strong>{lineMoveName(line)}</strong></span>)}</button>
        <section className="analysis-panel explorer-panel"><div className="panel-heading"><div><span>Lichess opening explorer</span><strong>Human games · {coverageTarget}% set</strong></div><button className={`tiny-switch${explorerOn ? ' on' : ''}`} onClick={() => rememberToggle('tempo-explorer-on', !explorerOn, setExplorerOn)}>{explorerOn ? 'Live' : 'Off'}</button></div>
          {!explorerOn ? <p className="panel-message">Explorer is paused.</p> : explorerState === 'auth' ? <div className="connect-panel"><p>Connect Lichess to load Explorer data.</p><button onClick={connectLichess}>Connect Lichess</button></div> : explorerState === 'loading' ? <p className="panel-message">Loading Lichess data…</p> : explorerState === 'error' ? <div className="connect-panel"><p>The Lichess connection needs to be refreshed.</p><button onClick={connectLichess}>Reconnect</button></div> : <><div className="source-status"><span>Connected</span><button onClick={disconnectLichess}>Disconnect</button></div><MoveRows moves={explorerCandidates.map((move) => ({ ...move, probability: explorerTotal ? (move.white + move.draws + move.black) / explorerTotal : 0 }))} covered={coveredReplies} detail="results" onPlay={playUci} onHover={setHoveredMove} /></>}
        </section>
        <section className="analysis-panel explorer-panel"><div className="panel-heading"><div><span>Masters database</span><strong>Master games · {coverageTarget}% set</strong></div></div><MoveRows moves={mastersCandidates} covered={coveredReplies} detail="results" onPlay={playUci} onHover={setHoveredMove}/></section>
        <section className="analysis-panel engine-panel"><div className="panel-heading"><div><span>Stockfish 19</span><strong>Engine lines</strong></div><b className={`engine-badge ${stockfishState}`}>{stockfishState === 'loading' ? 'Analyzing…' : stockfishState === 'ready' ? 'Local' : stockfishState === 'error' ? 'Could not start' : 'Off'}</b></div>{stockfishState === 'ready' && <MoveRows moves={engineCandidates} covered={coveredReplies} detail="score" onPlay={playUci} onHover={setHoveredMove}/>}</section>
        <section className="analysis-panel engine-panel"><div className="panel-heading"><div><span>Maia 3</span><strong>Likely moves at {maiaElo}</strong></div></div>{maiaState === 'loading' ? <p className="panel-message">{maiaProgress ? `Downloading model · ${maiaProgress}%` : 'Initializing local Maia…'}</p> : maiaState === 'error' ? <p className="panel-message error">Maia could not start. Toggle it off and on to retry.</p> : maiaState === 'ready' ? <MoveRows moves={maiaCandidates} covered={coveredReplies} detail="probability" onPlay={playUci} onHover={setHoveredMove}/> : <p className="panel-message">Maia is off.</p>}</section>
      </aside>
    </div>
    {searchOpen&&<div className="modal-backdrop" onMouseDown={()=>setSearchOpen(false)}><section className="position-search-modal" role="dialog" aria-modal="true" aria-label="Position search" onMouseDown={(event)=>event.stopPropagation()}><button className="close-button" onClick={()=>setSearchOpen(false)} aria-label="Close position search">×</button><h2>Position search</h2><p>{lineMatches.length} matching branches</p><div className="position-search-list">{lineMatches.map((line,index)=><button className={index===searchIndex?'active':''} key={line.id} onMouseEnter={()=>setSearchIndex(index)} onClick={()=>setSearchOpen(false)}><span>{line.side} · {line.repertoireName}</span><strong>{lineMoveName(line)}</strong><small>{line.moves.slice(cursor,cursor+4).join(' · ')||'Exact line endpoint'}</small></button>)}</div></section></div>}
  </section>;
}

function MoveRows({ moves, covered, detail, onPlay, onHover }: { moves: Array<EngineMove & Partial<ExplorerMove>>; covered: Set<string>; detail: 'probability' | 'score' | 'results'; onPlay?: (uci: string) => void; onHover?: (uci: string | null) => void }) {
  return <div className="candidate-list">{moves.map((move, index) => {
    const games=(move.white??0)+(move.draws??0)+(move.black??0);
    const result=games?`${Math.round((move.white??0)/games*100)}W · ${Math.round((move.draws??0)/games*100)}D · ${Math.round((move.black??0)/games*100)}L`:'';
    const value=detail==='probability'?`${Math.round((move.probability??0)*100)}%`:detail==='results'?result:(move.score??'Repertoire');
    return <button className="candidate-row" key={move.uci} onClick={() => onPlay?.(move.uci)} onMouseEnter={()=>onHover?.(move.uci)} onMouseLeave={()=>onHover?.(null)} onFocus={()=>onHover?.(move.uci)} onBlur={()=>onHover?.(null)}><span>{index+1}</span><strong>{move.san}</strong><small>{value}</small><em className={covered.has(move.uci)?'covered':'gap'}>{detail==='results'&&games?games.toLocaleString():covered.has(move.uci)?'Covered':'Gap'}</em></button>;
  })}</div>;
}

type RepertoireItem = { id: string; side: 'White' | 'Black'; title: string; sourceName: string; detail: string; progress: number; due: number; pgn?: string; backend?: boolean };

function RepertoireView({ imported, onImport, onBrowse, onDeleteLocal, onRenameLocal, onQueueChanged }: { imported: LocalRepertoire[]; onImport: () => void; onBrowse: () => void; onDeleteLocal: (id: string, sourceName?: string) => void; onRenameLocal: (id: string, name: string) => void; onQueueChanged: () => Promise<void> }) {
  const [samples, setSamples] = useState<RepertoireItem[]>([
    { id: 'sample-white', side: 'White', title: '1. e4 Main Lines', sourceName: 'Tempo examples', detail: '4 example lines', progress: 76, due: 0, pgn: '[Event "1. e4 Main Lines"]\n[Result "*"]\n\n1. e4 c5 2. Nf3 d6 3. d4 cxd4 *' },
    { id: 'sample-black', side: 'Black', title: 'Sicilian Defense', sourceName: 'Tempo examples', detail: '2 example lines', progress: 58, due: 0, pgn: '[Event "Sicilian Defense"]\n[Result "*"]\n\n1. e4 c5 2. Nf3 d6 *' },
  ]);
  const [backendItems, setBackendItems] = useState<RepertoireItem[]>([]);

  const loadBackend = useCallback(async () => {
    if (!usesLocalApi()) return;
    try {
      const response = await fetch(`${API_URL}/api/repertoires`);
      if (!response.ok) return;
      const body = await response.json() as { repertoires: { id: string; name: string; source_name: string; line_count: number; card_count: number; due_count: number; trained_color?: 'white' | 'black' }[] };
      setBackendItems(body.repertoires.map((item) => ({ id: item.id, side: item.trained_color === 'black' ? 'Black' : 'White', title: item.name, sourceName: item.source_name, detail: `${item.line_count} unique ${item.line_count === 1 ? 'line' : 'lines'} · ${item.card_count} cards`, progress: 0, due: item.due_count, backend: true })));
    } catch { /* Browser-local repertoires remain available. */ }
  }, []);

  useEffect(() => { void loadBackend(); }, [loadBackend]);
  const backendSources = new Set(backendItems.map((item) => item.sourceName));
  const importedItems: RepertoireItem[] = imported.filter((item) => !backendSources.has(item.sourceName)).map((item) => ({ id: item.id, side: item.side, title: item.title, sourceName: item.sourceName, detail: `${item.cards.length} unique ${item.cards.length === 1 ? 'line' : 'lines'} · stored in this browser`, progress: 0, due: 0, pgn: item.pgn }));
  const repertoires = [...samples, ...backendItems, ...importedItems];

  async function rename(item: RepertoireItem) {
    const value=window.prompt('Repertoire nickname',item.title)?.trim();
    if(!value) return;
    if (item.backend) {
      const response = await fetch(`${API_URL}/api/repertoires/${item.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: value }) });
      if (response.ok) await loadBackend();
    } else if (item.id.startsWith('sample-')) setSamples((current) => current.map((entry) => entry.id === item.id ? { ...entry, title: value } : entry));
    else onRenameLocal(item.id, value);
  }

  async function remove(item: RepertoireItem) {
    if (!window.confirm(`Delete “${item.title}”? Its cards and review history will also be removed.`)) return;
    if (item.backend) {
      const response = await fetch(`${API_URL}/api/repertoires/${item.id}`, { method: 'DELETE' });
      if (!response.ok) return;
      onDeleteLocal(item.id, item.sourceName);
      await onQueueChanged();
      await loadBackend();
    } else if (item.id.startsWith('sample-')) setSamples((current) => current.filter((entry) => entry.id !== item.id));
    else onDeleteLocal(item.id, item.sourceName);
  }

  function exportPgn(item?: RepertoireItem) {
    if ((item?.backend || (!item && backendItems.length)) && usesLocalApi()) {
      const link=document.createElement('a'); link.href=item ? `${API_URL}/api/repertoires/${item.id}/export.pgn` : `${API_URL}/api/repertoires/export.pgn`; link.click(); return;
    }
    const text = item?.pgn ?? repertoires.flatMap((entry) => entry.pgn ? [entry.pgn] : []).join('\n\n');
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
        {repertoires.map((item) => (
          <article className="repertoire-card" key={item.id}>
            <div className="repertoire-top"><span className="side-badge">{item.side}</span><span>{item.due ? `${item.due} due` : 'Up to date'}</span></div>
            <div className="mini-board" aria-hidden="true">{Array.from({ length: 16 }).map((_, index) => <i key={index} />)}</div>
            <div className="repertoire-name"><h2>{item.title}</h2><button onClick={()=>void rename(item)} title="Rename repertoire">✎</button></div><p>{item.detail}</p><small className="source-name">{item.sourceName}</small>
            <div className="maturity-row"><span>Maturity</span><strong>{item.progress}%</strong></div>
            <div className="maturity-track"><span style={{ width: `${item.progress}%` }} /></div>
            <div className="repertoire-actions"><button className="browse-button" onClick={onBrowse}>Browse tree</button><button onClick={()=>exportPgn(item)}>⇩ PGN</button><button className="delete-repertoire" onClick={()=>void remove(item)}>Delete</button></div>
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

type PackagedPuzzle = {
  DeckId: string;
  DeckPosition: number;
  PuzzleId: string;
  FEN: string;
  Moves: string;
  Rating: number;
};

function packagedPuzzleCard(record: PackagedPuzzle): PracticeCard | null {
  const board = new Chess(record.FEN);
  const uciMoves = record.Moves.split(/\s+/).filter(Boolean);
  try {
    const setup = uciMoves.shift()!;
    board.move({ from: setup.slice(0, 2) as Square, to: setup.slice(2, 4) as Square, promotion: setup[4] });
    const startingFen = board.fen();
    const moves = uciMoves.map((uci) => board.move({ from: uci.slice(0, 2) as Square, to: uci.slice(2, 4) as Square, promotion: uci[4] }).san);
    return { id: `lichess-${record.PuzzleId}`, kind: 'puzzle', title: `Puzzle ${record.DeckPosition}`, subtitle: `Lichess · ${record.Rating}`, startingFen, moves, userMoveTarget: Math.ceil(moves.length / 2), sourceUrl: `https://lichess.org/training/${record.PuzzleId}`, orientation: new Chess(startingFen).turn() === 'b' ? 'black' : 'white' };
  } catch { return null; }
}

function TacticsView({ theme, pieceSet, onQueueChanged }: { theme: BoardTheme; pieceSet: PieceSet; onQueueChanged: () => void }) {
  const [motif, setMotif] = useState('hangingPiece');
  const [stage, setStage] = useState('easy');
  const [progress, setProgress] = useState(readTacticProgress);
  const [step, setStep] = useState(0);
  const [hint, setHint] = useState(false);
  const [failed, setFailed] = useState(false);
  const [outcome, setOutcome] = useState<'correct' | 'wrong' | null>(null);
  const [boardAttempt, setBoardAttempt] = useState(0);
  const finishingRef = useRef(false);
  const attemptTokenRef = useRef(0);
  const advanceTimerRef = useRef<number | undefined>(undefined);
  const [catalog, setCatalog] = useState<PackagedPuzzle[]>([]);
  useEffect(() => { fetch('/data/tactics-decks.json').then((response) => response.json()).then(setCatalog).catch(() => setCatalog([])); }, []);
  const progressKey = tacticProgressKey(motif, stage);
  const currentProgress = progress[progressKey] ?? { clean: 0, index: 0 };
  const packagedDeck = useMemo(() => catalog.filter((record) => record.DeckId === `${motif}-${stage}`).sort((a, b) => a.DeckPosition - b.DeckPosition).map(packagedPuzzleCard).filter((card): card is PracticeCard => Boolean(card)), [catalog, motif, stage]);
  const deck = packagedDeck.length ? packagedDeck : [tacticExamples[motif] ?? demoCards[2], ...alternateTactics];
  const puzzle = deck[currentProgress.index % deck.length];
  const puzzleSide = puzzle.orientation ?? (new Chess(puzzle.startingFen).turn() === 'b' ? 'black' : 'white');
  const packagedRecord = catalog.find((record) => record.PuzzleId === puzzle.id.replace(/^lichess-/, ''));
  const [fen, setFen] = useState(puzzle.startingFen);

  const resetAttempt = useCallback((markFailed = false) => {
    attemptTokenRef.current += 1;
    finishingRef.current = false;
    setFen(puzzle.startingFen);
    setStep(0);
    setHint(markFailed);
    setFailed(markFailed);
    setOutcome(null);
    setBoardAttempt((value) => value + 1);
  }, [puzzle]);

  useEffect(() => { resetAttempt(); }, [resetAttempt]);
  useEffect(() => () => { if (advanceTimerRef.current) window.clearTimeout(advanceTimerRef.current); }, []);

  function finish() {
    if (finishingRef.current) return;
    finishingRef.current = true;
    const clean = !failed;
    const completedKey = progressKey;
    const token = ++attemptTokenRef.current;
    setOutcome(clean ? 'correct' : 'wrong');
    if(packagedRecord&&typeof window!=='undefined'&&['localhost','127.0.0.1'].includes(location.hostname)){
      void fetch(`${API_URL}/api/tactics/attempt`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({puzzle_id:packagedRecord.PuzzleId,deck_id:packagedRecord.DeckId,correct:clean,clean,source_fen:packagedRecord.FEN,moves:packagedRecord.Moves.split(/\s+/),rating:packagedRecord.Rating})}).then((response)=>{if(response.ok)onQueueChanged();}).catch(()=>undefined);
    }
    advanceTimerRef.current = window.setTimeout(() => {
      setProgress((current) => {
        const next = advanceTacticProgress(current, completedKey, clean);
        writeTacticProgress(next);
        return next;
      });
      if (attemptTokenRef.current === token) {
        finishingRef.current = false;
        setFen(puzzle.startingFen);
        setStep(0);
        setHint(false);
        setFailed(false);
        setOutcome(null);
        setBoardAttempt((value) => value + 1);
      }
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
      setBoardAttempt((value) => value + 1);
      return;
    }
    setFen(board.fen());
    const replyIndex = step + 1;
    if (replyIndex >= puzzle.moves.length) { finish(); return; }
    const replyBoard = new Chess(board.fen());
    replyBoard.move(puzzle.moves[replyIndex]);
    setFen(replyBoard.fen());
    playMoveSound();
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
          <Chessboard key={`${puzzle.id}:${boardAttempt}`} fen={fen} expectedSan={puzzle.moves[step]} locked={Boolean(outcome) || step >= puzzle.moves.length} showHint={hint} theme={theme} pieceSet={pieceSet} onMove={movePiece} orientation={puzzleSide}/>
          <div className="board-tools"><button onClick={() => { setFailed(true); setHint(true); }}>⌁ <span>Show move</span></button><button onClick={() => resetAttempt(true)}>↻ <span>Restart</span></button>{puzzle.sourceUrl && <a href={puzzle.sourceUrl} target="_blank" rel="noreferrer">↗ <span>Original</span></a>}</div>
          {outcome && (
            <OutcomeFlash outcome={outcome}/>
          )}
        </div>
        <aside className="study-panel tactic-study">
          <span className="pill puzzle">{stage}</span><h2>{current[1]}</h2><p className="side-to-play">{puzzleSide === 'black' ? 'Black' : 'White'} to play</p><p className="tactic-rating">Puzzle {currentProgress.index + 1} of {target}</p>
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

type TablebaseResult = { category: string; moves?: { uci: string; san?: string; category?: string; dtz?: number }[] };

async function probeTablebase(fen: string): Promise<TablebaseResult> {
  const local = typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(location.hostname);
  const url = local ? `${process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000'}/api/endgames/probe` : `https://tablebase.lichess.ovh/standard?fen=${encodeURIComponent(fen)}`;
  const response = await fetch(url, local ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fen }) } : undefined);
  if (!response.ok) throw new Error('Tablebase unavailable');
  return response.json();
}

function tablebaseCategoryForWhite(fen: string, category: string): 'win' | 'draw' | 'loss' {
  const normalized = category.includes('win') ? 'win' : category.includes('loss') ? 'loss' : 'draw';
  if (new Chess(fen).turn() === 'w') return normalized;
  return normalized === 'win' ? 'loss' : normalized === 'loss' ? 'win' : 'draw';
}

function EndgamesView({ theme, pieceSet, onQueueChanged }: { theme: BoardTheme; pieceSet: PieceSet; onQueueChanged: () => void }) {
  const [selected, setSelected] = useState(1);
  const [classification, setClassification] = useState<'win' | 'draw' | null>(null);
  const [target, setTarget] = useState<'win' | 'draw'>('win');
  const [status, setStatus] = useState('Finding a legal tablebase position…');
  const [fen, setFen] = useState(STANDARD_FEN);
  const [busy,setBusy]=useState(true);
  const [userMoves,setUserMoves]=useState(0);
  const [complete,setComplete]=useState(false);
  const [admitted,setAdmitted]=useState<Record<number,{templateId:string;cardId:string}>>({});

  useEffect(()=>{
    if(!usesLocalApi())return;
    fetch(`${API_URL}/api/endgames/templates`).then((response)=>response.ok?response.json():Promise.reject()).then((data:{templates:Array<{id:string;card_id:string;white_material:string;black_material:string}>})=>{
      const next:Record<number,{templateId:string;cardId:string}>={};
      endgameTemplates.forEach((template,index)=>{const found=data.templates.find((item)=>item.white_material===template.white&&item.black_material===template.black);if(found)next[index]={templateId:found.id,cardId:found.card_id};});
      setAdmitted(next);
    }).catch(()=>undefined);
  },[]);

  async function admitTemplate(){
    if(!usesLocalApi())return;
    const template=endgameTemplates[selected];
    const response=await fetch(`${API_URL}/api/endgames/templates`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:template.name,white_material:template.white,black_material:template.black,trained_color:'white',goal_mix:'both'})});
    if(!response.ok){setStatus('Could not add this material set to training.');return;}
    const data=await response.json() as {id:string;card_id:string};
    setAdmitted((current)=>({...current,[selected]:{templateId:data.id,cardId:data.card_id}}));
    setStatus('Added to your daily training.'); onQueueChanged();
  }

  function recordEndgame(outcome:'correct'|'again'){
    const item=admitted[selected];
    if(!item||!usesLocalApi())return;
    void fetch(`${API_URL}/api/cards/${item.cardId}/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({outcome,guided:outcome==='again'})}).then(()=>onQueueChanged()).catch(()=>undefined);
  }

  const newPosition = useCallback(async (index = selected) => {
    setBusy(true); setClassification(null); setComplete(false); setUserMoves(0); setStatus('Finding a legal tablebase position…');
    for(let attempt=0;attempt<24;attempt+=1){
      const candidate=generateLegalEndgameFen(endgameTemplates[index]);
      try {
        const result=await probeTablebase(candidate);
        const category=tablebaseCategoryForWhite(candidate,result.category);
        if(category==='loss')continue;
        setFen(candidate); setTarget(category); setStatus('Classify the position before playing.'); setBusy(false); return;
      } catch { /* Try another legal position before reporting the service unavailable. */ }
    }
    setStatus('The tablebase could not provide a position. Try again when online.'); setBusy(false);
  },[selected]);

  useEffect(()=>{void newPosition(selected);},[newPosition,selected]);

  function classify(value: 'win' | 'draw') {
    setClassification(value);
    setStatus(value === target ? `Correct · now ${target === 'win' ? 'convert the win' : 'hold the draw'}.` : `This position is a ${target}. Try the classification again.`);
  }

  async function play(from: Square, to: Square) {
    if (classification !== target || busy || complete) return;
    const board = new Chess(fen);
    try { board.move({ from, to, promotion: 'q' }); } catch { return; }
    setFen(board.fen()); setBusy(true);
    if(board.isCheckmate()){setComplete(true);setStatus('Converted · template review complete.');setBusy(false);recordEndgame('correct');return;}
    try {
      const afterUser=await probeTablebase(board.fen());
      const userCategory=tablebaseCategoryForWhite(board.fen(),afterUser.category);
      if((target==='win'&&userCategory!=='win')||(target==='draw'&&userCategory==='loss')){setComplete(true);setStatus(`Failed · the position is now a ${userCategory}.`);setBusy(false);recordEndgame('again');return;}
      const defense=afterUser.moves?.[0]?.uci;
      if(defense){board.move({from:defense.slice(0,2) as Square,to:defense.slice(2,4) as Square,promotion:defense[4]});setFen(board.fen());}
      const count=userMoves+1;setUserMoves(count);
      if(board.isGameOver()){const success=target==='draw'&&!board.isCheckmate();setComplete(true);setStatus(success?'Draw secured · template review complete.':'The defender held the position.');recordEndgame(success?'correct':'again');}
      else if(target==='draw'&&count>=20){setComplete(true);setStatus('Draw held for 20 moves · template review complete.');recordEndgame('correct');}
      else setStatus(`${target==='win'?'Winning':'Drawing'} status preserved · tablebase defense played.`);
    } catch { setStatus('The tablebase response failed. Your move remains on the board.'); }
    setBusy(false);
  }

  return (
    <section className="endgames-page">
      <div className="workspace-title"><div><h1>Endgames</h1><span>Exact seven-piece practice</span></div><button className="primary-button" disabled={Boolean(admitted[selected])||!usesLocalApi()} onClick={()=>void admitTemplate()}>{admitted[selected]?'✓ In daily training':'＋ Add to daily training'}</button></div>
      <div className="endgame-workspace">
        <aside className="template-list">{endgameTemplates.map((template, index) => <button className={selected === index ? 'active' : ''} key={template.name} onClick={() => setSelected(index)}><strong>{template.name}</strong><small>{template.white} vs {template.black} · White</small></button>)}</aside>
        <div className="board-column centered-board"><Chessboard fen={fen} locked={busy || classification !== target || complete} showHint={false} theme={theme} pieceSet={pieceSet} onMove={(from,to)=>void play(from,to)}/><div className="board-tools"><button onClick={() => void newPosition()}>⤨ <span>New position</span></button><button>⚙ <span>Edit material</span></button></div></div>
        <aside className="study-panel endgame-study"><span className="pill">Material template</span><h2>{endgameTemplates[selected].name}</h2><p>White to move · exact tablebase</p><div className="classification"><button className={classification === 'win' ? 'active' : ''} disabled={busy} onClick={() => classify('win')}>Win</button><button className={classification === 'draw' ? 'active' : ''} disabled={busy} onClick={() => classify('draw')}>Draw</button></div><div className={`feedback ${complete&&status.startsWith('Failed')?'wrong':'ready'}`}><span className="feedback-icon">{busy?'…':complete?'✓':'●'}</span><div><strong>{classification === target ? 'Play the position' : 'Win or draw?'}</strong><p>{status}</p></div></div><small>{target==='draw'?`${userMoves} / 20 accurate user moves`:'Checkmate completes the card.'}</small></aside>
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

type GameViewRecord = {
  id: string; source: string; date: string; speed: string; color: string; result: string; opening: string;
  status: string; detail: string; flag: string; flagPly: number; moves: string[]; startFen: string;
};

function importedGameRecord(value: Record<string, unknown>): GameViewRecord | null {
  const startFen = String(value.start_fen || STANDARD_FEN);
  const uciMoves = Array.isArray(value.moves) ? value.moves.map(String) : [];
  const board = new Chess(startFen);
  const moves: string[] = [];
  try {
    for (const uci of uciMoves) moves.push(board.move({ from: uci.slice(0, 2) as Square, to: uci.slice(2, 4) as Square, promotion: uci[4] }).san);
  } catch { return null; }
  const major = typeof value.major_mistake_ply === 'number' ? value.major_mistake_ply : null;
  const missed = typeof value.missed_punishment_ply === 'number' ? value.missed_punishment_ply : null;
  const divergence = typeof value.divergence_ply === 'number' ? value.divergence_ply : null;
  const flagPly = major ?? missed ?? divergence ?? 0;
  const status = String(value.classification || 'no applicable repertoire');
  const flag = major !== null ? `First major mistake · move ${Math.floor(major / 2) + 1}` : missed !== null ? `Missed punishment · move ${Math.floor(missed / 2) + 1}` : divergence !== null ? `First repertoire divergence · move ${Math.floor(divergence / 2) + 1}` : 'No flagged position';
  return {
    id: String(value.id), source: String(value.provider) === 'chess.com' ? 'Chess.com' : 'Lichess',
    date: String(value.played_at || '').slice(0, 10), speed: String(value.speed || 'unknown'),
    color: String(value.color || ''), result: String(value.result || '*'), opening: String(value.opening_name || 'Unclassified opening'),
    status, detail: divergence !== null ? `Diverged at move ${Math.floor(divergence / 2) + 1}` : status,
    flag, flagPly, moves, startFen,
  };
}

function GamesView({ onAnalyze, theme, pieceSet }: { onAnalyze: () => void; theme: BoardTheme; pieceSet: PieceSet }) {
  const [lichess, setLichess] = useState(() => typeof window === 'undefined' ? '' : localStorage.getItem('tempo-lichess-username') ?? '');
  const [chesscom, setChesscom] = useState(() => typeof window === 'undefined' ? '' : localStorage.getItem('tempo-chesscom-username') ?? '');
  const [source, setSource] = useState('All');
  const [status, setStatus] = useState('All');
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState(() => typeof window === 'undefined' ? 'Last 90 days · rated blitz, rapid, and classical' : localStorage.getItem('tempo-last-sync-note') ?? 'Last 90 days · rated blitz, rapid, and classical');
  const [lastSync,setLastSync]=useState(()=>typeof window==='undefined'?'':localStorage.getItem('tempo-last-sync')??'');
  const localApi=typeof window!=='undefined' && ['localhost','127.0.0.1'].includes(location.hostname) ? (process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000') : '';
  const demoRecords = useMemo(() => sampleGames.map((game) => ({ ...game, startFen: STANDARD_FEN })), []);
  const [records,setRecords]=useState<GameViewRecord[]>(demoRecords);
  const [selectedId,setSelectedId]=useState(demoRecords[0].id);
  const [cursor,setCursor]=useState(demoRecords[0].flagPly);
  const [engineOn,setEngineOn]=useState(true);
  const [engineText,setEngineText]=useState('Select a flagged position to analyze.');
  const [gamesLoaded,setGamesLoaded]=useState(false);
  const selected=records.find((game)=>game.id===selectedId) ?? records[0] ?? { id:'',source:'',date:'',speed:'',color:'',result:'',opening:'No synced game selected',status:'',detail:'',flag:'Sync an account to review games.',flagPly:0,moves:[],startFen:STANDARD_FEN };
  const games = records.filter((game) => (source === 'All' || game.source === source) && (status === 'All' || game.status === status));
  const gameFen=fenAfterMoves(selected.moves,Math.min(cursor,selected.moves.length),selected.startFen);
  const gameLast=cursor ? uciLine(selected.moves,selected.startFen)[cursor-1] : undefined;
  const applicable=records.filter((game)=>game.status!=='no applicable repertoire');
  const covered=records.filter((game)=>game.status==='covered').length;
  const opponentGaps=records.filter((game)=>game.status==='opponent repertoire gap').length;
  const deviations=records.filter((game)=>game.status==='player deviation').length;
  const noRepertoire=records.filter((game)=>game.status==='no applicable repertoire').length;

  const loadGames=useCallback(async()=>{
    if(!localApi)return;
    try {
      const response=await fetch(`${localApi}/api/games/summary`);
      if(!response.ok)throw new Error();
      const body=await response.json() as {games:Record<string,unknown>[]};
      const loaded=body.games.map(importedGameRecord).filter((game):game is GameViewRecord=>Boolean(game));
      setRecords(loaded); setSelectedId((current)=>loaded.some((game)=>game.id===current)?current:(loaded[0]?.id??''));
      setCursor(loaded[0]?.flagPly??0); setGamesLoaded(true);
    } catch { setRecords([]); setSelectedId(''); setCursor(0); setGamesLoaded(true); }
  },[localApi]);

  useEffect(()=>{void loadGames();},[loadGames]);

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
      const data = await response.json(); const time=new Date(data.synced_at).toISOString(); setLastSync(time); localStorage.setItem('tempo-last-sync',time); const note=`${data.imported} new games · local cache is up to date`; setSyncNote(note); localStorage.setItem('tempo-last-sync-note',note); await loadGames();
    } catch { setSyncNote('Saved. Start the local service to sync live games; the comparison preview remains available.'); }
    setSyncing(false);
  },[chesscom,lichess,loadGames,localApi,syncing]);
  const syncRef=useRef(sync); syncRef.current=sync;
  useEffect(()=>{ const run=()=>{if((localStorage.getItem('tempo-lichess-username')||localStorage.getItem('tempo-chesscom-username'))&&document.visibilityState==='visible') void syncRef.current()}; const timer=window.setInterval(run,180000); window.addEventListener('focus',run); window.addEventListener('online',run); run(); return()=>{clearInterval(timer);window.removeEventListener('focus',run);window.removeEventListener('online',run)}; },[]); // sync once on mount and then while visible

  useEffect(()=>{let active=true;if(!engineOn||!selected.id){setEngineText('Select a synced game to analyze.');return()=>{active=false;};}setEngineText('Analyzing selected position…');analyzeWithStockfish(gameFen).then(lines=>{if(active)setEngineText(lines[0]?`${lines[0].san} · ${lines[0].score}`:'No line');}).catch(()=>{if(active)setEngineText('Local engine unavailable');});return()=>{active=false;};},[engineOn,gameFen,selected.id]);
  useEffect(()=>{const onKey=(event:KeyboardEvent)=>{if(event.target instanceof HTMLInputElement||event.target instanceof HTMLSelectElement)return;if(event.key==='ArrowLeft'){event.preventDefault();setCursor((value)=>Math.max(0,value-1));}if(event.key==='ArrowRight'){event.preventDefault();setCursor((value)=>Math.min(selected.moves.length,value+1));}if(event.key==='Home'){event.preventDefault();setCursor(0);}if(event.key==='End'){event.preventDefault();setCursor(selected.moves.length);}};window.addEventListener('keydown',onKey);return()=>window.removeEventListener('keydown',onKey);},[selected.moves.length]);

  return <section className="games-page" id="games">
    <div className="page-heading compact"><div><h1>Games</h1><p>{lastSync?`Last synced at ${new Date(lastSync).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}`:'Automatic local sync checks every 3 minutes'}</p></div><button className="primary-button sync-button" onClick={()=>void sync()} disabled={syncing}>{syncing&&<i/>}{syncing ? 'Syncing games' : '↻ Sync games'}</button></div>
    <div className="game-review"><div className="game-board"><Chessboard fen={gameFen} lastMove={gameLast?[gameLast.slice(0,2),gameLast.slice(2,4)]:undefined} locked showHint={false} theme={theme} pieceSet={pieceSet} onMove={()=>undefined}/><div className="board-tools"><button disabled={!selected.id||cursor===0} onClick={()=>setCursor(Math.max(0,cursor-1))}>← Back</button><button disabled={!selected.id||cursor===selected.moves.length} onClick={()=>setCursor(Math.min(selected.moves.length,cursor+1))}>Forward →</button><button disabled={!selected.id} onClick={()=>setCursor(selected.flagPly)}>⚑ First mistake</button><button className={engineOn?'active':''} onClick={()=>setEngineOn(!engineOn)}>Stockfish</button></div></div><div className="game-side-scroll"><aside className="game-inspector"><span className="pill">Quick scan</span><h2>{selected.opening}</h2><strong>{selected.flag}</strong><p>{engineText}</p><div className="game-moves">{selected.moves.map((move,index)=><button className={`${index<cursor?'shown':''}${index===selected.flagPly?' flagged':''}`} onClick={()=>setCursor(index+1)} key={`${move}-${index}`}>{index%2===0?`${Math.floor(index/2)+1}.`:''}{move}</button>)}</div><button className="primary-button" disabled={!selected.id} onClick={onAnalyze}>Open gap in builder</button></aside>
    <section className="account-strip"><label><span>Lichess username</span><input value={lichess} onChange={(e) => setLichess(e.target.value)} placeholder="Optional" /></label><label><span>Chess.com username</span><input value={chesscom} onChange={(e) => setChesscom(e.target.value)} placeholder="Optional" /></label><button onClick={saveAccounts}>Save locally</button><small>{syncNote}</small></section>
    <div className="games-metrics"><article><span>Repertoire adherence</span><strong>{applicable.length?`${Math.round(covered/applicable.length*100)}%`:'—'}</strong><small>{covered} of {applicable.length} applicable games</small></article><article><span>Opponent gaps</span><strong>{opponentGaps}</strong><small>Missing opponent responses</small></article><article><span>Your deviations</span><strong>{deviations}</strong><small>First off-repertoire moves</small></article><article><span>No repertoire</span><strong>{noRepertoire}</strong><small>Games without an applicable line</small></article></div>
    <div className="games-workspace"><aside className="gap-list"><span>Recurring repairs</span>{records.filter((game)=>game.status.includes('gap')||game.status.includes('deviation')).slice(0,4).map((game)=><button key={game.id} onClick={onAnalyze}><b>{game.opening}</b><small>{game.detail} · open builder</small></button>)}{!records.some((game)=>game.status.includes('gap')||game.status.includes('deviation'))&&<p>No recurring repairs yet.</p>}</aside><section className="game-list"><div className="game-filters"><select value={source} onChange={(e) => setSource(e.target.value)}><option>All</option><option>Lichess</option><option>Chess.com</option></select><select value={status} onChange={(e) => setStatus(e.target.value)}><option>All</option><option>covered</option><option>opponent repertoire gap</option><option>player deviation</option><option>no applicable repertoire</option></select><span>90 days · rated · blitz / rapid / classical</span></div>{games.map((game) => <button className={`game-row${selected.id===game.id?' selected':''}`} key={game.id} onClick={()=>{setSelectedId(game.id);setCursor(game.flagPly)}}><span><b>{game.opening}</b><small>{game.source} · {game.date} · {game.speed} · {game.color} · {game.result}</small></span><span><em className={game.status.replaceAll(' ','-')}>{game.status}</em><small>{game.detail}</small></span><i>Review →</i></button>)}{gamesLoaded&&!games.length&&<div className="games-empty"><strong>No matching synced games.</strong><span>Add a username above or change the filters.</span></div>}</section></div></div></div>
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

type TempoSettings = {
  initial_depth: number; timezone: string; new_cards_per_day: number; lichess_username: string; chesscom_username: string;
  auto_sync_minutes: number; engine_line_window_cp: number; major_mistake_cp: number; light_first_interval_days: number; draw_hold_user_moves: number;
  board_theme: BoardTheme; piece_set: PieceSet; sound: boolean; sound_volume: number; coverage_target: number; maia_elo: string; explorer_speeds: string; explorer_ratings: string; arrow_metric: 'stockfish'|'lichess'|'masters';
};

function SettingsView({ theme, pieceSet, sound, onTheme, onPieces, onSound }: { theme: BoardTheme; pieceSet: PieceSet; sound: boolean; onTheme: (value: BoardTheme) => void; onPieces: (value: PieceSet) => void; onSound: (value: boolean) => void }) {
  const [values, setValues] = useState<TempoSettings>({ initial_depth: 6, timezone: 'local', new_cards_per_day: 10, lichess_username: '', chesscom_username: '', auto_sync_minutes: 3, engine_line_window_cp: 30, major_mistake_cp: 100, light_first_interval_days: 7, draw_hold_user_moves: 20, board_theme: theme, piece_set: pieceSet, sound, sound_volume:.72, coverage_target: 90, maia_elo: '1500', explorer_speeds: 'blitz,rapid,classical', explorer_ratings: '1600,1800,2000,2200,2500', arrow_metric:'stockfish' });
  const [status, setStatus] = useState('');

  useEffect(() => {
    setValues((current) => ({ ...current, board_theme: theme, piece_set: pieceSet, sound, sound_volume:Number(localStorage.getItem('tempo-sound-volume')??.72), arrow_metric:(localStorage.getItem('tempo-arrow-metric') as TempoSettings['arrow_metric']|null)??'stockfish', engine_line_window_cp:Number(localStorage.getItem('tempo-engine-window-cp')??30), coverage_target: Number(localStorage.getItem('tempo-coverage-target') ?? 90), maia_elo: localStorage.getItem('tempo-maia-elo') ?? '1500', explorer_speeds: localStorage.getItem('tempo-explorer-speeds') ?? 'blitz,rapid,classical', explorer_ratings: localStorage.getItem('tempo-explorer-ratings') ?? '1600,1800,2000,2200,2500', lichess_username: localStorage.getItem('tempo-lichess-username') ?? '', chesscom_username: localStorage.getItem('tempo-chesscom-username') ?? '' }));
    if (usesLocalApi()) void fetch(`${API_URL}/api/settings`).then((response) => response.ok ? response.json() : Promise.reject()).then((saved) => setValues((current) => ({ ...current, ...saved }))).catch(() => undefined);
  }, [pieceSet, sound, theme]);

  function update<K extends keyof TempoSettings>(key: K, value: TempoSettings[K]) { setValues((current) => ({ ...current, [key]: value })); }

  async function save() {
    onTheme(values.board_theme); onPieces(values.piece_set); onSound(values.sound);
    localStorage.setItem('tempo-coverage-target', String(values.coverage_target)); localStorage.setItem('tempo-maia-elo', values.maia_elo);
    localStorage.setItem('tempo-explorer-speeds', values.explorer_speeds); localStorage.setItem('tempo-explorer-ratings', values.explorer_ratings);
    localStorage.setItem('tempo-engine-window-cp',String(values.engine_line_window_cp)); localStorage.setItem('tempo-arrow-metric',values.arrow_metric); localStorage.setItem('tempo-sound-volume',String(values.sound_volume));
    localStorage.setItem('tempo-lichess-username', values.lichess_username.trim()); localStorage.setItem('tempo-chesscom-username', values.chesscom_username.trim());
    if (usesLocalApi()) {
      const backend = { initial_depth: values.initial_depth, timezone: values.timezone, new_cards_per_day: values.new_cards_per_day, lichess_username: values.lichess_username.trim(), chesscom_username: values.chesscom_username.trim(), auto_sync_minutes: values.auto_sync_minutes, engine_line_window_cp: values.engine_line_window_cp, major_mistake_cp: values.major_mistake_cp, light_first_interval_days: values.light_first_interval_days, draw_hold_user_moves: values.draw_hold_user_moves };
      try { const response = await fetch(`${API_URL}/api/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(backend) }); if (!response.ok) throw new Error(); setStatus('Saved. The new-card limit applies when the next daily queue is created.'); }
      catch { setStatus('Browser settings saved. The local service could not be reached.'); }
    } else setStatus('Saved in this browser.');
  }

  return <section className="settings-page">
    <div className="page-heading compact"><div><h1>Settings</h1><p>Training, boards, analysis, games, and scheduling—all in one place.</p></div><button className="primary-button" onClick={() => void save()}>Save settings</button></div>
    <div className="settings-grid">
      <section className="settings-card"><h2>Training</h2><label><span>Initial prefix length<small>User moves per opening card</small></span><input type="number" min="2" max="20" value={values.initial_depth} onChange={(event) => update('initial_depth', Number(event.target.value))}/></label><label><span>New cards per day<small>Reviews are always shown; only unseen cards are limited</small></span><input type="number" min="0" max="100" value={values.new_cards_per_day} onChange={(event) => update('new_cards_per_day', Number(event.target.value))}/></label><label><span>Light first interval<small>Days after a clean tactics discovery</small></span><input type="number" min="1" max="90" value={values.light_first_interval_days} onChange={(event) => update('light_first_interval_days', Number(event.target.value))}/></label><label><span>Draw hold length<small>User moves required in endgame studies</small></span><input type="number" min="5" max="100" value={values.draw_hold_user_moves} onChange={(event) => update('draw_hold_user_moves', Number(event.target.value))}/></label></section>
      <section className="settings-card"><h2>Board</h2><label><span>Board colors</span><select value={values.board_theme} onChange={(event) => update('board_theme', event.target.value as BoardTheme)}><option value="brown">Brown</option><option value="blue">Blue</option><option value="green">Green</option></select></label><label><span>Piece set</span><select value={values.piece_set} onChange={(event) => update('piece_set', event.target.value as PieceSet)}><option value="cburnett">Cburnett</option><option value="merida">Merida</option></select></label><label><span>Woodland sounds<small>Separate wooden move and capture sounds</small></span><button className={`setting-switch${values.sound ? ' on' : ''}`} onClick={() => update('sound', !values.sound)}>{values.sound ? 'On' : 'Off'}</button></label><label><span>Sound volume</span><input type="range" min="0" max="1" step="0.05" value={values.sound_volume} onChange={(event)=>update('sound_volume',Number(event.target.value))}/></label></section>
      <section className="settings-card"><h2>Analysis</h2><label><span>Coverage target</span><select value={values.coverage_target} onChange={(event) => update('coverage_target', Number(event.target.value))}><option value="80">80%</option><option value="90">90%</option><option value="95">95%</option></select></label><label><span>Candidate colors</span><select value={values.arrow_metric} onChange={(event)=>update('arrow_metric',event.target.value as TempoSettings['arrow_metric'])}><option value="stockfish">Stockfish quality</option><option value="lichess">Lichess practical score</option><option value="masters">Masters practical score</option></select></label><label><span>Engine move window<small>Centipawns from the best move</small></span><input type="number" min="0" max="300" value={values.engine_line_window_cp} onChange={(event) => update('engine_line_window_cp', Number(event.target.value))}/></label><label><span>Maia strength</span><select value={values.maia_elo} onChange={(event) => update('maia_elo', event.target.value)}><option>1100</option><option>1500</option><option>1900</option></select></label><label><span>Explorer games</span><select value={values.explorer_speeds} onChange={(event) => update('explorer_speeds', event.target.value)}><option value="blitz,rapid,classical">Blitz + rapid + classical</option><option value="rapid,classical">Rapid + classical</option><option value="classical">Classical only</option></select></label><label><span>Explorer ratings</span><select value={values.explorer_ratings} onChange={(event) => update('explorer_ratings', event.target.value)}><option value="1600,1800,2000,2200,2500">1600+</option><option value="2000,2200,2500">2000+</option><option value="2200,2500">2200+</option></select></label></section>
      <section className="settings-card"><h2>Games</h2><label><span>Lichess username</span><input value={values.lichess_username} onChange={(event) => update('lichess_username', event.target.value)} placeholder="Optional"/></label><label><span>Chess.com username</span><input value={values.chesscom_username} onChange={(event) => update('chesscom_username', event.target.value)} placeholder="Optional"/></label><label><span>Automatic sync<small>Minutes while Tempo is open</small></span><input type="number" min="2" max="60" value={values.auto_sync_minutes} onChange={(event) => update('auto_sync_minutes', Number(event.target.value))}/></label><label><span>Major mistake threshold<small>Centipawn loss</small></span><input type="number" min="25" max="1000" value={values.major_mistake_cp} onChange={(event) => update('major_mistake_cp', Number(event.target.value))}/></label></section>
    </div>
    {status && <p className="settings-status" role="status">✓ {status}</p>}
  </section>;
}

function ImportDialog({ onClose, onImported, onViewRepertoire, onDatabaseUpdated }: { onClose: () => void; onImported: (repertoire: LocalRepertoire) => void; onViewRepertoire: () => void; onDatabaseUpdated: () => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null);
  const [initialDepth, setInitialDepth] = useState(6);
  const [trainedColor, setTrainedColor] = useState<'white' | 'black'>('white');
  const [finished, setFinished] = useState(false);
  const [summary, setSummary] = useState({ lines: 0, duplicates: 0, admitted: 0, backend: false });
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
      let admitted = 0;
      if (['localhost', '127.0.0.1'].includes(location.hostname)) {
        const data = new FormData();
        data.append('file', file);
        data.append('trained_color', trainedColor);
        data.append('initial_depth', String(initialDepth));
        try {
          const response = await fetch(`${API_URL}/api/imports/pgn`, { method: 'POST', body: data });
          backend = response.ok;
          if (backend) { admitted=((await response.json()) as {cards_admitted_today?:number}).cards_admitted_today??0; await onDatabaseUpdated(); }
        } catch { /* The browser-local import remains usable without the service. */ }
      }
      setSummary({ lines: parsed.cards.length, duplicates: parsed.duplicateLines, admitted, backend });
      setFinished(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Tempo could not read this PGN.');
    } finally { setWorking(false); }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="import-dialog" role="dialog" aria-modal="true" aria-labelledby="import-title" onMouseDown={(event) => event.stopPropagation()}>
        <button className="close-button" onClick={onClose} aria-label="Close import dialog">×</button>
        {finished ? <div className="import-finished"><span>✓</span><h2 id="import-title">Imported</h2><p><strong>{file?.name}</strong> added {summary.lines} unique {summary.lines === 1 ? 'line' : 'lines'}{summary.duplicates ? ` and merged ${summary.duplicates} duplicate ${summary.duplicates === 1 ? 'line' : 'lines'}` : ''}. {summary.backend ? `${summary.admitted} cards are in today’s queue; remaining new cards will follow your daily limit.` : 'This browser’s repertoire and practice queue are updated.'}</p><button className="primary-button" onClick={() => { onClose(); onViewRepertoire(); }}>View imported repertoire</button></div> : <>
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
  const [soundOn, setSoundOn] = useState(true);
  const [databaseQueue, setDatabaseQueue] = useState(false);
  const card = practiceCards[activeCardIndex] ?? practiceCards[0];
  const repertoireLine = card.moves;

  const refreshDatabaseQueue = useCallback(async () => {
    if (!usesLocalApi()) return;
    try {
      const response = await fetch(`${API_URL}/api/queue/today`);
      if (!response.ok) throw new Error();
      const body = await response.json() as { cards: BackendQueueCard[] };
      const playable = body.cards.filter((item) => item.content_type !== 'endgame').map(practiceCardFromQueue);
      setDatabaseQueue(true);
      setPracticeCards(playable.length ? playable : [...demoCards]);
      const queue = playable.map((_, index) => index);
      setDailyQueue(queue);
      setCardsLeft(queue.length);
      setActiveCardIndex(0);
      const first = playable[0];
      if (first) {
        const start = initialTrainingState(first);
        setFen(start.fen); setStep(start.step); setFeedback('ready'); setLastMove(start.lastMove);
        setLocked(false); setShowHint(false); setAttemptFailed(false);
      }
    } catch { setDatabaseQueue(false); }
  }, []);

  useEffect(() => { window.scrollTo({ top: 0, behavior: 'auto' }); }, [view]);

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
    const loadedCards = [...demoCards, ...savedCards];
    setPracticeCards(loadedCards);
    const storedQueue = JSON.parse(localStorage.getItem('tempo-daily-queue') ?? JSON.stringify(Array.from({ length: 12 }, (_, index) => index % demoCards.length))) as number[];
    setDailyQueue(storedQueue); setCardsLeft(storedQueue.length); setActiveCardIndex(storedQueue[0] ?? 0);
    const loadedCard = loadedCards[storedQueue[0] ?? 0];
    if (loadedCard) {
      const start = initialTrainingState(loadedCard);
      setFen(start.fen); setStep(start.step); setLastMove(start.lastMove);
    }
    setBoardTheme((localStorage.getItem('tempo-board-theme') as BoardTheme | null) ?? 'brown');
    setPieceSet((localStorage.getItem('tempo-piece-set') as PieceSet | null) ?? 'cburnett');
    setSoundOn(moveSoundEnabled());
    void refreshDatabaseQueue();
  }, [refreshDatabaseQueue]);

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

  function renameLocalRepertoire(id: string, name: string) {
    const next = importedRepertoires.map((item) => item.id === id ? { ...item, title: name } : item);
    setImportedRepertoires(next);
    localStorage.setItem('tempo-imported-repertoires', JSON.stringify(next));
  }

  function deleteLocalRepertoire(id: string, sourceName?: string) {
    const removed = importedRepertoires.find((item) => item.id === id || (sourceName && item.sourceName === sourceName));
    const nextRepertoires = importedRepertoires.filter((item) => item.id !== id && (!sourceName || item.sourceName !== sourceName));
    setImportedRepertoires(nextRepertoires);
    localStorage.setItem('tempo-imported-repertoires', JSON.stringify(nextRepertoires));
    if (!removed) return;
    const removedIds = new Set(removed.cards.map((item) => item.id));
    const queuedIds = dailyQueue.map((index) => practiceCards[index]?.id).filter((cardId): cardId is string => Boolean(cardId) && !removedIds.has(cardId));
    const nextCards = practiceCards.filter((item) => !removedIds.has(item.id));
    const nextQueue = queuedIds.map((cardId) => nextCards.findIndex((item) => item.id === cardId)).filter((index) => index >= 0);
    setPracticeCards(nextCards); setDailyQueue(nextQueue); setCardsLeft(nextQueue.length); setActiveCardIndex(nextQueue[0] ?? 0);
    localStorage.setItem('tempo-daily-queue', JSON.stringify(nextQueue)); localStorage.setItem('tempo-cards-left', String(nextQueue.length));
    resetLine(nextCards[nextQueue[0] ?? 0] ?? demoCards[0]);
  }

  function resetLine(nextCard = card) {
    const start = initialTrainingState(nextCard);
    setFen(start.fen); setStep(start.step); setFeedback('ready'); setLastMove(start.lastMove); setLocked(false); setShowHint(false); setAttemptFailed(false);
  }

  function changeBoardTheme(value: BoardTheme) {
    setBoardTheme(value);
    localStorage.setItem('tempo-board-theme', value);
  }

  function changePieceSet(value: PieceSet) {
    setPieceSet(value);
    localStorage.setItem('tempo-piece-set', value);
  }

  function changeSound(value: boolean) {
    setSoundOn(value); localStorage.setItem('tempo-move-sound', String(value));
    if (value) playMoveSound(true);
  }

  async function rateCard(outcome: 'again' | 'correct') {
    if (databaseQueue && card.backendId) {
      try {
        const response = await fetch(`${API_URL}/api/cards/${card.backendId}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ outcome, guided: attemptFailed, queue_entry_id: card.queueEntryId }) });
        if (!response.ok) throw new Error();
        setReviewed((count) => count + 1);
        setQueueNotice(outcome === 'again' ? 'Guided review complete · shuffled behind 4 cards' : 'Correct · saved to your schedule');
        await refreshDatabaseQueue();
        return;
      } catch { setQueueNotice('The local database could not save this result.'); return; }
    }
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
    const currentTurn = new Chess(fen).turn() === 'b' ? 'black' : 'white';
    if (locked || step >= repertoireLine.length || currentTurn !== trainedColor(card) || card.kind === 'endgame') return;
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
      playMoveSound();
      if (nextStep >= repertoireLine.length) setTimeout(() => rateCard(attemptFailed ? 'again' : 'correct'), 650);
    }, 420);
  }

  const opponentName = trainedColor(card) === 'white' ? 'Black' : 'White';
  const playerName = trainedColor(card) === 'white' ? 'White' : 'Black';
  const feedbackCopy = {
    ready: { title: 'Your move', body: card.kind === 'puzzle' ? 'Find the strongest continuation.' : `Continue the line for ${playerName}.` },
    correct: { title: 'That’s it', body: `${opponentName} is replying…` },
    branch: { title: 'Also in your repertoire', body: 'That move is valid. Replay the arrowed move for the branch being tested.' },
    wrong: { title: 'Try that position again', body: 'That move is legal, but it isn’t in this repertoire.' },
    complete: { title: attemptFailed ? 'Guided line complete' : 'Line recalled', body: attemptFailed ? 'Again will return after four other cards.' : 'Correct is being recorded automatically.' },
  }[feedback];

  const currentMoveKey = `${card.id}:${step}`;
  const showTeachingArrow = step < repertoireLine.length && new Chess(fen).turn() === (trainedColor(card) === 'white' ? 'w' : 'b') && (showHint || feedback === 'wrong' || !seenMoves.has(currentMoveKey));
  const analysisUrl = lichessAnalysisUrl(repertoireLine.slice(0, step), card.startingFen);
  const revealedMoves = repertoireLine.slice(0, feedback === 'complete' ? repertoireLine.length : step);

  const dateLabel = new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric' }).format(new Date());
  const boardWorkspace = ['train','tactics','endgames','analysis','games'].includes(view);

  return (
    <main className={`app-shell${boardWorkspace ? ' board-workspace-shell' : ''}`}>
      <header className="topbar">
        <button className="brand" onClick={() => setView('train')} aria-label="Tempo home"><span className="brand-mark">T</span><span>Tempo</span></button>
        <nav className="nav" aria-label="Primary navigation">
          {(['train','tactics','endgames','repertoire','analysis','games','progress','settings'] as View[]).map((item) => <button className={view === item ? 'active' : ''} key={item} onClick={() => setView(item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}
        </nav>
        <div className="top-actions">
          <button className="sound-toggle" aria-pressed={soundOn} aria-label={`${soundOn ? 'Turn off' : 'Turn on'} board sounds`} onClick={() => changeSound(!soundOn)}><span aria-hidden="true">{soundOn ? '🔊' : '🔇'}</span><span>Sound</span></button>
          <button className="local-status" onClick={() => setShowImport(true)}><span className="status-dot" /> Saved locally</button>
        </div>
      </header>

      {view === 'train' && <>
        <section className="training-header"><div><p className="eyebrow">Today · {dateLabel}</p><h1>{cardsLeft === 0 ? 'You’re done for today' : 'Daily training'}</h1></div><div className="session-count"><strong>{cardsLeft}</strong><span>cards left</span></div></section>
        <section className="training-grid" id="train">
          <div className="board-column">
            <Chessboard fen={fen} expectedSan={repertoireLine[step]} lastMove={lastMove} locked={locked || step >= repertoireLine.length || cardsLeft === 0} showHint={showTeachingArrow} theme={boardTheme} pieceSet={pieceSet} onMove={tryMove} orientation={card.orientation} />
            <div className="board-tools"><button onClick={() => { if(!attemptFailed){setAttemptFailed(true);setQueueNotice('Again recorded · finish with guidance');} setShowHint((value) => !value); }} disabled={feedback === 'complete' || cardsLeft === 0}>⌁ <span>{showHint ? 'Hide move' : 'Show move'}</span></button><button onClick={() => { resetLine(); setAttemptFailed(true); setShowHint(true); setQueueNotice('Again recorded · restarted in guided mode'); }}>↻ <span>Restart</span></button><a href={analysisUrl} onClick={()=>{if(!attemptFailed) rateCard('again')}} target="_blank" rel="noreferrer">↗ <span>Analyze</span></a><button onClick={()=>setEditorCard(card)}>✎ <span>Edit card</span></button></div>
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
        <TacticsView theme={boardTheme} pieceSet={pieceSet} onQueueChanged={()=>void refreshDatabaseQueue()}/>
      )}
      {view === 'endgames' && (
        <EndgamesView theme={boardTheme} pieceSet={pieceSet} onQueueChanged={()=>void refreshDatabaseQueue()}/>
      )}
      {view === 'repertoire' && <RepertoireView imported={importedRepertoires} onImport={() => setShowImport(true)} onBrowse={() => setShowTree(true)} onDeleteLocal={deleteLocalRepertoire} onRenameLocal={renameLocalRepertoire} onQueueChanged={refreshDatabaseQueue} />}
      {view === 'analysis' && <AnalysisView theme={boardTheme} pieceSet={pieceSet} imported={importedRepertoires} />}
      {view === 'games' && <GamesView onAnalyze={() => setView('analysis')} theme={boardTheme} pieceSet={pieceSet} />}
      {view === 'progress' && <ProgressView reviewed={reviewed} cardsLeft={cardsLeft} totalCards={practiceCards.length} />}
      {view === 'settings' && <SettingsView theme={boardTheme} pieceSet={pieceSet} sound={soundOn} onTheme={changeBoardTheme} onPieces={changePieceSet} onSound={changeSound} />}
      {showImport && <ImportDialog onClose={() => setShowImport(false)} onImported={addImportedRepertoire} onDatabaseUpdated={refreshDatabaseQueue} onViewRepertoire={() => setView('repertoire')} />}
      {showTree && <TreeBrowser onClose={() => setShowTree(false)} theme={boardTheme} pieceSet={pieceSet} />}
      {editorCard && <CardEditor card={editorCard} theme={boardTheme} pieceSet={pieceSet} onClose={()=>setEditorCard(null)} onSave={(updated)=>{setPracticeCards(current=>current.map(item=>item.id===updated.id?updated:item));resetLine(updated);setSuggestShorter(false);}}/>}
      {!boardWorkspace&&<footer className="source-footer">Board interaction by <a href="https://github.com/lichess-org/chessground" target="_blank" rel="noreferrer">Chessground</a> · Woodland sounds and chess assets from <a href="https://github.com/lichess-org/lila" target="_blank" rel="noreferrer">Lichess</a> under AGPL-3.0+ · Puzzle positions from the public-domain <a href="https://database.lichess.org/#puzzles" target="_blank" rel="noreferrer">Lichess database</a></footer>}
    </main>
  );
}
