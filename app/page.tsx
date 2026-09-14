'use client';

import { Chessground } from '@lichess-org/chessground';
import type { Api } from '@lichess-org/chessground/api';
import type { DrawShape } from '@lichess-org/chessground/draw';
import type { Key } from '@lichess-org/chessground/types';
import { Chess, Move, Square } from 'chess.js';
import { useEffect, useMemo, useRef, useState } from 'react';
import { analyzeWithMaia, analyzeWithStockfish, type EngineMove } from './lib/analysis-engines';

const USER_MOVES_PER_PREFIX = 6;
const STANDARD_FEN = new Chess().fen();
type BoardTheme = 'brown' | 'blue' | 'green';
type PieceSet = 'cburnett' | 'merida';
type PracticeCard = {
  id: string;
  kind: 'opening' | 'puzzle';
  title: string;
  subtitle: string;
  startingFen: string;
  moves: string[];
  userMoveTarget: number;
  nextMove?: string;
  sourceUrl?: string;
};

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
    startingFen: 'q5nr/1ppknQpp/3p4/1P2p3/4P3/B1PP1b2/6PP/5K2 w - - 1 18',
    moves: ['Be6+', 'Kd8', 'Qf8#'],
    userMoveTarget: 2,
    sourceUrl: 'https://lichess.org/training/00sHx',
  },
] satisfies PracticeCard[];

type Feedback = 'ready' | 'correct' | 'branch' | 'wrong' | 'complete';
type View = 'train' | 'repertoire' | 'analysis' | 'games' | 'progress';

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

function uciLine(sanMoves: string[]) {
  const chess = new Chess();
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

function moveForSan(chess: Chess, san: string): Move | undefined {
  return chess.moves({ verbose: true }).find((move) => move.san === san);
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

function Chessboard({
  fen,
  expectedSan,
  lastMove,
  locked,
  showHint,
  theme,
  pieceSet,
  shapes = [],
  onMove,
}: {
  fen: string;
  expectedSan?: string;
  lastMove?: [string, string];
  locked: boolean;
  showHint: boolean;
  theme: BoardTheme;
  pieceSet: PieceSet;
  shapes?: DrawShape[];
  onMove: (from: Square, to: Square) => void;
}) {
  const elementRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<Api | null>(null);
  const onMoveRef = useRef(onMove);
  onMoveRef.current = onMove;
  const chess = useMemo(() => new Chess(fen), [fen]);
  const hintMove = expectedSan ? moveForSan(chess, expectedSan) : undefined;

  useEffect(() => {
    if (!elementRef.current) return;
    apiRef.current = Chessground(elementRef.current);
    return () => { apiRef.current?.destroy(); apiRef.current = null; };
  }, []);

  useEffect(() => {
    const destinations = new Map<Key, Key[]>();
    for (const move of chess.moves({ verbose: true })) {
      const from = move.from as Key;
      destinations.set(from, [...(destinations.get(from) ?? []), move.to as Key]);
    }
    const autoShapes: DrawShape[] = [
      ...shapes,
      ...(showHint && hintMove ? [{ orig: hintMove.from as Key, dest: hintMove.to as Key, brush: 'yellow' }] : []),
    ];
    apiRef.current?.set({
      fen,
      orientation: 'white',
      turnColor: chess.turn() === 'w' ? 'white' : 'black',
      lastMove: lastMove as Key[] | undefined,
      coordinates: true,
      viewOnly: locked,
      animation: { enabled: true, duration: 180 },
      movable: {
        free: false,
        color: locked ? undefined : chess.turn() === 'w' ? 'white' : 'black',
        dests: destinations,
        showDests: true,
        events: { after: (from, to) => onMoveRef.current(from as Square, to as Square) },
      },
      draggable: { enabled: !locked, showGhost: true },
      selectable: { enabled: !locked },
      drawable: {
        enabled: true,
        visible: true,
        autoShapes,
        brushes: {
          green: { key: 'g', color: '#4f8a59', opacity: .88, lineWidth: 10 },
          red: { key: 'r', color: '#b45f50', opacity: .88, lineWidth: 10 },
          blue: { key: 'b', color: '#4e7ca8', opacity: .88, lineWidth: 10 },
          yellow: { key: 'y', color: '#d0a83f', opacity: .92, lineWidth: 11 },
          maia: { key: 'm', color: '#8a62a5', opacity: .9, lineWidth: 10 },
        },
      },
    });
  }, [chess, fen, hintMove, lastMove, locked, shapes, showHint]);

  return (
    <div className="board-frame" aria-label="Interactive chessboard">
      <div className={`chessground-shell theme-${theme} pieces-${pieceSet}`}>
        <div className="cg-wrap" ref={elementRef} />
      </div>
    </div>
  );
}

function ProgressStrip({ current, total = USER_MOVES_PER_PREFIX }: { current: number; total?: number }) {
  return (
    <div className="progress-strip" style={{ gridTemplateColumns: `repeat(${total}, 1fr)` }} aria-label={`${current} of ${total} user moves complete`}>
      {Array.from({ length: total }).map((_, index) => (
        <div className={`progress-segment${index < current ? ' filled' : ''}`} key={index} />
      ))}
    </div>
  );
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

function TacticsDecks() {
  const [activeDecks, setActiveDecks] = useState<Set<string>>(new Set(['fork-easy']));
  const motifs = [
    { id: 'fork', icon: '♘', title: 'Forks' },
    { id: 'pin', icon: '⌖', title: 'Pins' },
    { id: 'skewer', icon: '⇥', title: 'Skewers' },
    { id: 'discoveredAttack', icon: '✦', title: 'Discoveries' },
  ];
  const tiers = [
    { id: 'easy', label: 'Easy', range: '700–1100' },
    { id: 'medium', label: 'Medium', range: '1101–1500' },
    { id: 'hard', label: 'Hard', range: '1501–2000' },
  ];

  useEffect(() => {
    const saved = localStorage.getItem('tempo-tactics-decks');
    if (saved) setActiveDecks(new Set(JSON.parse(saved)));
  }, []);

  function toggleDeck(id: string) {
    setActiveDecks((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      localStorage.setItem('tempo-tactics-decks', JSON.stringify([...next]));
      return next;
    });
  }

  return (
    <section className="tactics-section" aria-labelledby="tactics-title">
      <div className="tactics-heading">
        <div><p className="eyebrow">Puzzle practice</p><h2 id="tactics-title">Tactical fundamentals</h2><p>Four motifs, three stages, and 100 real positions per deck. Pick only what you want to learn; the daily session handles the mixing.</p></div>
        <span className="pack-count">12 decks · 1,200 cards</span>
      </div>
      <div className="motif-grid">
        {motifs.map((motif) => <article className="motif-card" key={motif.id}>
          <div className="motif-title"><span className="tactic-icon">{motif.icon}</span><div><strong>{motif.title}</strong><small>Learn the pattern, then raise the difficulty.</small></div></div>
          <div className="deck-tiers">{tiers.map((tier) => {
            const id = `${motif.id}-${tier.id}`;
            const active = activeDecks.has(id);
            return <button className={active ? 'active' : ''} key={id} onClick={() => toggleDeck(id)}><span><b>{tier.label}</b><small>{tier.range}</small></span><em>{active ? '✓ In queue' : '＋ Add'}</em><i>100</i></button>;
          })}</div>
        </article>)}
      </div>
      <p className="source-note">The selected puzzle IDs, positions, solutions, and themes are packaged locally. Popular, well-played puzzles were chosen deterministically from the public-domain Lichess database.</p>
    </section>
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

function AnalysisView({ theme, pieceSet, onTheme, onPieces }: { theme: BoardTheme; pieceSet: PieceSet; onTheme: (value: BoardTheme) => void; onPieces: (value: PieceSet) => void }) {
  const [history, setHistory] = useState<{ san: string; uci: string; fen: string }[]>([]);
  const [cursor, setCursor] = useState(0);
  const [explorerOn, setExplorerOn] = useState(() => typeof window === 'undefined' || localStorage.getItem('tempo-explorer-on') !== 'false');
  const [stockfishOn, setStockfishOn] = useState(() => typeof window !== 'undefined' && localStorage.getItem('tempo-stockfish-on') === 'true');
  const [maiaOn, setMaiaOn] = useState(() => typeof window !== 'undefined' && localStorage.getItem('tempo-maia-on') === 'true');
  const [maiaElo, setMaiaElo] = useState(() => typeof window === 'undefined' ? '1500' : localStorage.getItem('tempo-maia-elo') ?? '1500');
  const [coverageTarget, setCoverageTarget] = useState(() => typeof window === 'undefined' ? 90 : Number(localStorage.getItem('tempo-coverage-target') ?? 90));
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
  const lineMatches = analysisLines.filter((line) => playedUci.every((move, index) => uciLine(line.moves)[index] === move));
  const coveredReplies = new Set(lineMatches.flatMap((line) => {
    const uci = uciLine(line.moves)[cursor];
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
  const arrowSources = new Map<string, Set<string>>();
  for (const move of explorerCandidates) arrowSources.set(move.uci, new Set([...(arrowSources.get(move.uci) ?? []), 'L']));
  for (const move of stockfishMoves.slice(0, 3)) arrowSources.set(move.uci, new Set([...(arrowSources.get(move.uci) ?? []), 'S']));
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
    <div className="analysis-heading"><div><p className="eyebrow">Explore and repair</p><h1>Analysis board</h1><p>Gold arrows are already covered. Blue arrows are candidate moves that still need a repertoire response.</p></div><div className="analysis-switches"><button className={stockfishOn ? 'on' : ''} onClick={() => rememberToggle('tempo-stockfish-on', !stockfishOn, setStockfishOn)}><i /> Stockfish 19</button><button className={maiaOn ? 'on' : ''} onClick={() => rememberToggle('tempo-maia-on', !maiaOn, setMaiaOn)}><i /> Maia 3</button></div></div>
    <div className="analysis-layout">
      <div className="analysis-board-column">
        <Chessboard fen={fen} lastMove={lastMove} locked={false} showHint={false} theme={theme} pieceSet={pieceSet} shapes={shapes} onMove={playMove} />
        <div className="arrow-legend"><span><i className="known" /> In repertoire</span><span><i className="candidate" /> Coverage candidate</span><span><b>L</b> Lichess</span><span><b>S</b> Stockfish</span><span><b>M</b> Maia</span></div>
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
        <section className="analysis-panel engine-panel"><div className="panel-heading"><div><span>Stockfish 19</span><strong>Objective engine choices</strong></div><b className={`engine-badge ${stockfishState}`}>{stockfishState === 'loading' ? 'Analyzing…' : stockfishState === 'ready' ? 'Local' : stockfishState === 'error' ? 'Could not start' : 'Off'}</b></div>{stockfishState === 'ready' && <MoveRows moves={stockfishMoves} covered={coveredReplies} detail="score" onPlay={playUci} />}</section>
        <section className="analysis-panel engine-panel"><div className="panel-heading"><div><span>Maia 3</span><strong>Likely moves at your level</strong></div><label className="elo-select">Elo<select value={maiaElo} onChange={(event) => { setMaiaElo(event.target.value); localStorage.setItem('tempo-maia-elo', event.target.value); }}><option>1100</option><option>1500</option><option>1900</option></select></label></div>{maiaState === 'loading' ? <p className="panel-message">{maiaProgress ? `Loading local model · ${maiaProgress}%` : 'Starting local Maia model…'}</p> : maiaState === 'error' ? <p className="panel-message error">Maia could not start in this browser.</p> : maiaState === 'ready' ? <MoveRows moves={maiaCandidates} covered={coveredReplies} detail="probability" onPlay={playUci} /> : <p className="panel-message">Maia is off.</p>}</section>
      </aside>
    </div>
  </section>;
}

function MoveRows({ moves, covered, detail, onPlay }: { moves: EngineMove[]; covered: Set<string>; detail: 'probability' | 'score'; onPlay?: (uci: string) => void }) {
  return <div className="candidate-list">{moves.map((move, index) => <button className="candidate-row" key={move.uci} onClick={() => onPlay?.(move.uci)}><span>{index + 1}</span><strong>{move.san}</strong><small>{detail === 'probability' ? `${Math.round((move.probability ?? 0) * 100)}%` : move.score}</small><em className={covered.has(move.uci) ? 'covered' : 'gap'}>{covered.has(move.uci) ? 'Covered' : 'Gap'}</em></button>)}</div>;
}

function RepertoireView({ onImport, onBrowse }: { onImport: () => void; onBrowse: () => void }) {
  const repertoires = [
    { side: 'White', title: '1. e4 Main Lines', detail: '842 positions · 168 cards', progress: 76, due: 8 },
    { side: 'Black', title: 'Sicilian Defense', detail: '516 positions · 103 cards', progress: 58, due: 4 },
    { side: 'Black', title: 'King’s Indian', detail: '284 positions · 61 cards', progress: 33, due: 0 },
  ];
  return (
    <section className="library-page" id="repertoire">
      <div className="page-heading">
        <div><p className="eyebrow">Your source material</p><h1>Repertoire</h1><p>Upload PGNs once. Tempo turns transpositions and shared prefixes into one clean set of cards.</p></div>
        <button className="primary-button" onClick={onImport}>＋ Import PGN</button>
      </div>
      <div className="library-grid">
        {repertoires.map((item) => (
          <article className="repertoire-card" key={item.title}>
            <div className="repertoire-top"><span className="side-badge">{item.side}</span><span>{item.due ? `${item.due} due` : 'Up to date'}</span></div>
            <div className="mini-board" aria-hidden="true">{Array.from({ length: 16 }).map((_, index) => <i key={index} />)}</div>
            <h2>{item.title}</h2><p>{item.detail}</p>
            <div className="maturity-row"><span>Maturity</span><strong>{item.progress}%</strong></div>
            <div className="maturity-track"><span style={{ width: `${item.progress}%` }} /></div>
            <button className="browse-button" onClick={onBrowse}>Browse tree</button>
          </article>
        ))}
        <button className="new-repertoire-card" onClick={onImport}><span>＋</span><strong>Add a repertoire</strong><small>PGN files stay on this computer</small></button>
      </div>
      <div className="unlock-explainer">
        <div><span className="step-number done">1</span><strong>Shared prefix</strong><small>One card for the opening’s first 6 moves</small></div>
        <i />
        <div><span className="step-number active">2</span><strong>Reach maturity</strong><small>3 successful days · 14-day interval</small></div>
        <i />
        <div><span className="step-number">3</span><strong>Focused response</strong><small>Train only the opponent move and your reply</small></div>
      </div>
      <TacticsDecks />
    </section>
  );
}

const sampleGames = [
  { id: 'g1', source: 'Lichess', date: 'Sep 12', speed: 'Rapid', color: 'White', result: 'Won', opening: 'Open Sicilian', status: 'covered', detail: 'Covered through 12… Be7' },
  { id: 'g2', source: 'Chess.com', date: 'Sep 10', speed: 'Blitz', color: 'Black', result: 'Lost', opening: 'King’s Indian', status: 'opponent gap', detail: 'New opponent move 7. d5' },
  { id: 'g3', source: 'Lichess', date: 'Sep 8', speed: 'Blitz', color: 'White', result: 'Draw', opening: 'French Defense', status: 'player deviation', detail: 'You played 8. Bd3 instead of 8. Qd2' },
  { id: 'g4', source: 'Lichess', date: 'Sep 2', speed: 'Classical', color: 'Black', result: 'Won', opening: 'English Opening', status: 'no repertoire', detail: 'No applicable Black repertoire' },
];

function GamesView({ onAnalyze }: { onAnalyze: () => void }) {
  const [lichess, setLichess] = useState(() => typeof window === 'undefined' ? '' : localStorage.getItem('tempo-lichess-username') ?? '');
  const [chesscom, setChesscom] = useState(() => typeof window === 'undefined' ? '' : localStorage.getItem('tempo-chesscom-username') ?? '');
  const [source, setSource] = useState('All');
  const [status, setStatus] = useState('All');
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState('Last 90 days · rated blitz, rapid, and classical');
  const games = sampleGames.filter((game) => (source === 'All' || game.source === source) && (status === 'All' || game.status === status));

  function saveAccounts() {
    localStorage.setItem('tempo-lichess-username', lichess.trim());
    localStorage.setItem('tempo-chesscom-username', chesscom.trim());
    setSyncNote('Accounts saved locally');
  }
  async function sync() {
    saveAccounts(); setSyncing(true);
    try {
      const response = await fetch('http://127.0.0.1:8000/api/games/sync', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lichess_username: lichess, chesscom_username: chesscom, days: 90, speeds: ['blitz', 'rapid', 'classical'], rated_only: true }) });
      if (!response.ok) throw new Error();
      const data = await response.json(); setSyncNote(`${data.imported} new games · existing games kept in local cache`);
    } catch { setSyncNote('Saved. Start the local service to sync live games; the comparison preview remains available.'); }
    setSyncing(false);
  }

  return <section className="games-page" id="games">
    <div className="page-heading"><div><p className="eyebrow">Your games against your prep</p><h1>Games</h1><p>Find the first place each game left your repertoire, then repair recurring gaps.</p></div><button className="primary-button" onClick={sync} disabled={syncing}>{syncing ? 'Syncing…' : '↻ Sync games'}</button></div>
    <section className="account-strip"><label><span>Lichess username</span><input value={lichess} onChange={(e) => setLichess(e.target.value)} placeholder="Optional" /></label><label><span>Chess.com username</span><input value={chesscom} onChange={(e) => setChesscom(e.target.value)} placeholder="Optional" /></label><button onClick={saveAccounts}>Save locally</button><small>{syncNote}</small></section>
    <div className="games-metrics"><article><span>Repertoire adherence</span><strong>78%</strong><small>31 of 40 applicable games</small></article><article><span>Opponent gaps</span><strong>6</strong><small>3 recurring moves</small></article><article><span>Your deviations</span><strong>3</strong><small>French occurs twice</small></article><article><span>No repertoire</span><strong>4</strong><small>Mostly English openings</small></article></div>
    <div className="games-workspace"><aside className="gap-list"><span>Recurring repairs</span><button onClick={onAnalyze}><b>French · 7… Nc6</b><small>3 games · add a response</small></button><button onClick={onAnalyze}><b>King’s Indian · 7. d5</b><small>2 games · opponent gap</small></button><button onClick={onAnalyze}><b>Sicilian · 6… e5</b><small>2 personal deviations</small></button></aside><section className="game-list"><div className="game-filters"><select value={source} onChange={(e) => setSource(e.target.value)}><option>All</option><option>Lichess</option><option>Chess.com</option></select><select value={status} onChange={(e) => setStatus(e.target.value)}><option>All</option><option>covered</option><option>opponent gap</option><option>player deviation</option><option>no repertoire</option></select><span>90 days · rated · blitz / rapid / classical</span></div>{games.map((game) => <button className="game-row" key={game.id} onClick={onAnalyze}><span><b>{game.opening}</b><small>{game.source} · {game.date} · {game.speed} · {game.color} · {game.result}</small></span><span><em className={game.status.replace(' ','-')}>{game.status}</em><small>{game.detail}</small></span><i>Open position →</i></button>)}</section></div>
  </section>;
}

function ProgressView({ reviewed }: { reviewed: number }) {
  const days = [
    ['Thu', 16], ['Fri', 22], ['Sat', 8], ['Sun', 28], ['Mon', 19], ['Tue', 31], ['Today', Math.max(8, reviewed)],
  ];
  return (
    <section className="progress-page" id="progress">
      <div className="page-heading compact"><div><p className="eyebrow">Quiet consistency</p><h1>Progress</h1><p>Review volume matters less than returning when each card is due.</p></div><span className="streak">12 day streak</span></div>
      <div className="metric-grid">
        <article><span>Due today</span><strong>12</strong><small>One focused session</small></article>
        <article><span>Mature cards</span><strong>184</strong><small>＋18 this month</small></article>
        <article><span>Recall rate</span><strong>91%</strong><small>Last 30 days</small></article>
        <article><span>Next unlock</span><strong>2</strong><small>Cards near maturity</small></article>
      </div>
      <div className="analytics-grid">
        <article className="chart-card">
          <div className="chart-heading"><div><span>Review activity</span><strong>132 cards this week</strong></div><small>7 days</small></div>
          <div className="bar-chart">{days.map(([label, value]) => <div className="bar-column" key={label}><span style={{ height: `${Number(value) * 3.3}px` }} /><small>{label}</small></div>)}</div>
        </article>
        <article className="maturity-card"><span>Maturity</span><h2>Most of your repertoire is becoming stable.</h2><div className="donut"><strong>72%</strong><small>learning or mature</small></div><ul><li><i className="new" />New <strong>96</strong></li><li><i className="learning" />Learning <strong>88</strong></li><li><i className="mature" />Mature <strong>184</strong></li></ul></article>
      </div>
    </section>
  );
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const [fileName, setFileName] = useState('');
  const [initialDepth, setInitialDepth] = useState(6);
  const [trainedColor, setTrainedColor] = useState<'white' | 'black'>('white');
  const [finished, setFinished] = useState(false);
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="import-dialog" role="dialog" aria-modal="true" aria-labelledby="import-title" onMouseDown={(event) => event.stopPropagation()}>
        <button className="close-button" onClick={onClose} aria-label="Close import dialog">×</button>
        {finished ? <div className="import-finished"><span>✓</span><h2 id="import-title">Ready to train</h2><p><strong>{fileName}</strong> would add 24 unique cards and merge 7 shared prefixes in the full local app.</p><button className="primary-button" onClick={onClose}>View repertoire</button></div> : <>
          <p className="eyebrow">Local import</p><h2 id="import-title">Add PGN repertoire</h2><p className="dialog-copy">Your file is parsed on this computer. Re-uploading the same positions updates the repertoire without duplicating cards.</p>
          <label className={`drop-zone${fileName ? ' has-file' : ''}`}><input type="file" accept=".pgn" onChange={(event) => setFileName(event.target.files?.[0]?.name ?? '')} /><span>{fileName ? '♟' : '⇧'}</span><strong>{fileName || 'Choose a PGN file'}</strong><small>{fileName ? 'Ready to preview' : 'or drop it here · .pgn only'}</small></label>
          <div className="color-setting"><span><strong>Side to train</strong><small>Only your moves count toward line depth</small></span><span className="color-toggle"><button className={trainedColor === 'white' ? 'active' : ''} onClick={() => setTrainedColor('white')}>White</button><button className={trainedColor === 'black' ? 'active' : ''} onClick={() => setTrainedColor('black')}>Black</button></span></div>
          <label className="depth-setting"><span><strong>Initial line depth</strong><small>New prefix cards test this many user moves</small></span><span className="stepper"><button onClick={() => setInitialDepth(Math.max(2, initialDepth - 1))}>−</button><b>{initialDepth} user moves</b><button onClick={() => setInitialDepth(Math.min(20, initialDepth + 1))}>＋</button></span></label>
          <div className="dialog-footer"><span><i className="status-dot" /> Stored locally</span><button className="primary-button" disabled={!fileName} onClick={() => setFinished(true)}>Preview import</button></div>
        </>}
      </section>
    </div>
  );
}

export default function Home() {
  const [view, setView] = useState<View>('train');
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
  const [seenMoves, setSeenMoves] = useState<Set<string>>(new Set());
  const [firstCleanPasses, setFirstCleanPasses] = useState<Set<string>>(new Set());
  const [attemptFailed, setAttemptFailed] = useState(false);
  const [dailyQueue, setDailyQueue] = useState<number[]>(Array.from({ length: 12 }, (_, index) => index % demoCards.length));
  const [queueNotice, setQueueNotice] = useState('');
  const [boardTheme, setBoardTheme] = useState<BoardTheme>('brown');
  const [pieceSet, setPieceSet] = useState<PieceSet>('cburnett');
  const card = demoCards[activeCardIndex];
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
    const storedQueue = JSON.parse(localStorage.getItem('tempo-daily-queue') ?? JSON.stringify(Array.from({ length: 12 }, (_, index) => index % demoCards.length))) as number[];
    setDailyQueue(storedQueue); setCardsLeft(storedQueue.length); setActiveCardIndex(storedQueue[0] ?? 0);
    setBoardTheme((localStorage.getItem('tempo-board-theme') as BoardTheme | null) ?? 'brown');
    setPieceSet((localStorage.getItem('tempo-piece-set') as PieceSet | null) ?? 'cburnett');
  }, []);

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
    let nextQueue = dailyQueue.slice(1);
    if (outcome === 'again') {
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
    resetLine(demoCards[nextIndex]);
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

  const userMovesComplete = Math.min(card.userMoveTarget, Math.ceil(step / 2));
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
          {(['train', 'repertoire', 'analysis', 'games', 'progress'] as View[]).map((item) => <button className={view === item ? 'active' : ''} key={item} onClick={() => setView(item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}
        </nav>
        <button className="local-status" onClick={() => setShowImport(true)}><span className="status-dot" /> Saved locally</button>
      </header>

      {view === 'train' && <>
        <section className="training-header"><div><p className="eyebrow">Today · {dateLabel}</p><h1>{cardsLeft === 0 ? 'You’re done for today' : 'Daily training'}</h1></div><div className="session-count"><strong>{cardsLeft}</strong><span>cards left</span></div></section>
        <section className="training-grid" id="train">
          <div className="board-column">
            <Chessboard fen={fen} expectedSan={repertoireLine[step]} lastMove={lastMove} locked={locked || step >= repertoireLine.length || cardsLeft === 0} showHint={showTeachingArrow} theme={boardTheme} pieceSet={pieceSet} onMove={tryMove} />
            <div className="board-tools"><button onClick={() => setShowHint((value) => !value)} disabled={feedback === 'complete' || cardsLeft === 0}>⌁ <span>{showHint ? 'Hide move' : 'Show move'}</span></button><button onClick={() => resetLine()}>↻ <span>Restart</span></button><a href={analysisUrl} target="_blank" rel="noreferrer">↗ <span>Analyze</span></a><label>Board<select value={boardTheme} onChange={(event) => changeBoardTheme(event.target.value as BoardTheme)}><option value="brown">Brown</option><option value="blue">Blue</option><option value="green">Green</option></select></label><label>Pieces<select value={pieceSet} onChange={(event) => changePieceSet(event.target.value as PieceSet)}><option value="cburnett">Cburnett</option><option value="merida">Merida</option></select></label></div>
          </div>
          <aside className="study-panel">
            <div className="card-meta"><span className={`pill${card.kind === 'puzzle' ? ' puzzle' : ''}`}>{card.kind === 'puzzle' ? 'Puzzle' : 'Review'}</span><span>{card.kind === 'puzzle' ? `${card.userMoveTarget}-move tactic` : 'Prefix card · 6 user moves'}</span>{queueNotice && <em>{queueNotice}</em>}</div>
            <div className="opening-title"><p>{card.kind === 'puzzle' ? 'Tactics · today’s queue' : 'White repertoire'}</p><h2>{card.title}</h2><span>{card.subtitle}</span></div>
            <div className={`feedback ${feedback}`} role="status" aria-live="polite"><span className="feedback-icon">{feedback === 'wrong' ? '×' : feedback === 'complete' ? '✓' : '●'}</span><div><strong>{feedbackCopy.title}</strong><p>{feedbackCopy.body}</p></div></div>
            <div className="move-progress"><div className="progress-label"><span>Card progress</span><strong>{userMovesComplete} / {card.userMoveTarget} user moves</strong></div><ProgressStrip current={userMovesComplete} total={card.userMoveTarget} /></div>
            <div className={`move-trail${revealedMoves.length ? '' : ' empty'}`} aria-live="polite"><span>Moves played</span>{revealedMoves.length ? <ol>{revealedMoves.map((move, index) => <li key={`${move}-${index}`}><b>{index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : '…'}</b>{move}</li>)}</ol> : <p>Nothing is revealed until you play it.</p>}</div>
            <div className="ratings binary"><button onClick={() => { setAttemptFailed(true); setShowHint(true); setQueueNotice('Again recorded · finish the line with guidance'); }}><strong>Again</strong><span>guide me, then retry today</span></button><button className="primary" onClick={() => rateCard('correct')}><strong>Correct</strong><span>same result as a clean solve</span></button></div>
            {card.kind === 'opening' ? <div className="next-up"><span>Next unlock</span><p>When FSRS stability reaches 14 days with 3 clean review days and no recent lapse, learn <strong>{card.nextMove}</strong> as a focused response card.</p></div> : <div className="next-up puzzle-note"><span>Motif deck</span><p>This puzzle is interleaved with opening reviews. Its rating and interval are tracked independently.</p>{card.sourceUrl && <a href={card.sourceUrl} target="_blank" rel="noreferrer">View original puzzle ↗</a>}</div>}
          </aside>
        </section>
      </>}
      {view === 'repertoire' && <RepertoireView onImport={() => setShowImport(true)} onBrowse={() => setShowTree(true)} />}
      {view === 'analysis' && <AnalysisView theme={boardTheme} pieceSet={pieceSet} onTheme={changeBoardTheme} onPieces={changePieceSet} />}
      {view === 'games' && <GamesView onAnalyze={() => setView('analysis')} />}
      {view === 'progress' && <ProgressView reviewed={reviewed} />}
      {showImport && <ImportDialog onClose={() => setShowImport(false)} />}
      {showTree && <TreeBrowser onClose={() => setShowTree(false)} theme={boardTheme} pieceSet={pieceSet} />}
      <footer className="source-footer">Board interaction by <a href="https://github.com/lichess-org/chessground" target="_blank" rel="noreferrer">Chessground</a> · Cburnett and Merida pieces from Lichess · Puzzle positions from the public-domain <a href="https://database.lichess.org/#puzzles" target="_blank" rel="noreferrer">Lichess database</a></footer>
    </main>
  );
}
