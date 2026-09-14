'use client';

import { Chessground } from '@lichess-org/chessground';
import type { Api } from '@lichess-org/chessground/api';
import type { DrawShape } from '@lichess-org/chessground/draw';
import type { Key } from '@lichess-org/chessground/types';
import { Chess, Move, Square } from 'chess.js';
import { useEffect, useMemo, useRef, useState } from 'react';

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
    moves: ['e4', 'c5', 'Nf3', 'd6', 'd4', 'cxd4', 'Nxd4', 'Nf6', 'Nc3', 'a6', 'Be3', 'e6'],
    userMoveTarget: 6,
    nextMove: '7. Qd2',
  },
  {
    id: 'french-classical-prefix',
    kind: 'opening',
    title: 'French Defense',
    subtitle: 'Classical variation',
    startingFen: STANDARD_FEN,
    moves: ['e4', 'e6', 'd4', 'd5', 'Nc3', 'Nf6', 'e5', 'Nfd7', 'f4', 'c5', 'Nf3', 'Nc6'],
    userMoveTarget: 6,
    nextMove: '7. Be3',
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

type Feedback = 'ready' | 'correct' | 'wrong' | 'complete';
type View = 'train' | 'repertoire' | 'progress';

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
  onMove,
}: {
  fen: string;
  expectedSan?: string;
  lastMove?: [string, string];
  locked: boolean;
  showHint: boolean;
  theme: BoardTheme;
  pieceSet: PieceSet;
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
    const autoShapes: DrawShape[] = showHint && hintMove
      ? [{ orig: hintMove.from as Key, dest: hintMove.to as Key, brush: 'yellow' }]
      : [];
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
      drawable: { enabled: true, visible: true, autoShapes },
    });
  }, [chess, fen, hintMove, lastMove, locked, showHint]);

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
  const [included, setIncluded] = useState(true);
  return (
    <section className="tactics-section" aria-labelledby="tactics-title">
      <div className="tactics-heading">
        <div><p className="eyebrow">Puzzle practice</p><h2 id="tactics-title">Tactics decks</h2><p>Curated from the public-domain Lichess puzzle database, then scheduled beside opening cards.</p></div>
        <button className={included ? 'included' : ''} onClick={() => setIncluded((value) => !value)}>{included ? 'Included in daily queue' : 'Add to daily queue'}</button>
      </div>
      <div className="tactics-grid">
        <article><span className="tactic-icon">⌁</span><div><strong>Healthy mix</strong><small>650–1600 rating · 18 motifs</small></div><b>6 due</b></article>
        <article><span className="tactic-icon">♘</span><div><strong>Forks & discoveries</strong><small>Pattern-focused · 240 puzzles</small></div><b>3 due</b></article>
        <article><span className="tactic-icon">#</span><div><strong>Mate patterns</strong><small>Mate in 1–3 · 180 puzzles</small></div><b>2 due</b></article>
        <button className="new-tactics-deck"><span>＋</span><div><strong>Build a motif deck</strong><small>Choose themes, rating, and mix</small></div></button>
      </div>
      <p className="source-note">Puzzle positions remain local after import. The daily queue can cap puzzle cards separately so opening recall keeps priority.</p>
    </section>
  );
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
  const [queueNotice, setQueueNotice] = useState('');
  const [boardTheme, setBoardTheme] = useState<BoardTheme>('brown');
  const [pieceSet, setPieceSet] = useState<PieceSet>('cburnett');
  const card = demoCards[activeCardIndex];
  const repertoireLine = card.moves;

  useEffect(() => {
    const today = localDayKey();
    if (localStorage.getItem('tempo-day') !== today) {
      localStorage.setItem('tempo-day', today);
      localStorage.setItem('tempo-cards-left', '12');
      localStorage.setItem('tempo-reviewed', '0');
    }
    setCardsLeft(Number(localStorage.getItem('tempo-cards-left') ?? 12));
    setReviewed(Number(localStorage.getItem('tempo-reviewed') ?? 0));
    setSeenMoves(new Set(JSON.parse(localStorage.getItem('tempo-seen-moves') ?? '[]')));
    setBoardTheme((localStorage.getItem('tempo-board-theme') as BoardTheme | null) ?? 'brown');
    setPieceSet((localStorage.getItem('tempo-piece-set') as PieceSet | null) ?? 'cburnett');
  }, []);

  function resetLine(nextCard = card) {
    setFen(nextCard.startingFen); setStep(0); setFeedback('ready'); setLastMove(undefined); setLocked(false); setShowHint(false);
  }

  function changeBoardTheme(value: BoardTheme) {
    setBoardTheme(value);
    localStorage.setItem('tempo-board-theme', value);
  }

  function changePieceSet(value: PieceSet) {
    setPieceSet(value);
    localStorage.setItem('tempo-piece-set', value);
  }

  function rateCard(rating: 'again' | 'hard' | 'good' | 'easy') {
    if (rating === 'again') {
      setQueueNotice('Shuffled behind 4 other reviews');
    } else {
      setCardsLeft((count) => { const next = Math.max(0, count - 1); localStorage.setItem('tempo-cards-left', String(next)); return next; });
      setQueueNotice('');
    }
    setReviewed((count) => { const next = count + 1; localStorage.setItem('tempo-reviewed', String(next)); return next; });
    const nextIndex = (activeCardIndex + 1) % demoCards.length;
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
      setFeedback('wrong'); setShowHint(true); setQueueNotice(''); return;
    }
    if (!move || move.san !== repertoireLine[step]) { setFeedback('wrong'); setShowHint(false); return; }
    markMoveSeen(step);
    setFen(position.fen()); setLastMove([move.from, move.to]); setFeedback('correct'); setShowHint(false);
    setQueueNotice('');
    const opponentStep = step + 1;
    setStep(opponentStep);
    if (opponentStep >= repertoireLine.length) { setFeedback('complete'); return; }
    setLocked(true);
    window.setTimeout(() => {
      const replyPosition = new Chess(position.fen());
      const reply = replyPosition.move(repertoireLine[opponentStep]);
      const nextStep = opponentStep + 1;
      setFen(replyPosition.fen()); setLastMove([reply.from, reply.to]); setStep(nextStep); setLocked(false); setFeedback(nextStep >= repertoireLine.length ? 'complete' : 'ready');
    }, 420);
  }

  const feedbackCopy = {
    ready: { title: step === 0 ? 'Your move' : 'Find the continuation', body: card.kind === 'puzzle' ? 'Find the strongest continuation.' : step === 0 ? 'Recall White’s first move.' : 'Continue the line for White.' },
    correct: { title: 'That’s it', body: 'Black is replying…' },
    wrong: { title: 'Try that position again', body: 'That move is legal, but it isn’t in this repertoire.' },
    complete: { title: 'Line recalled', body: 'Rate how difficult that felt.' },
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
          {(['train', 'repertoire', 'progress'] as View[]).map((item) => <button className={view === item ? 'active' : ''} key={item} onClick={() => setView(item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}
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
            <div className="opening-title"><p>{card.kind === 'puzzle' ? 'Tactics · mixed into today’s queue' : 'White repertoire'}</p><h2>{card.title}</h2><span>{card.subtitle}</span></div>
            <div className={`feedback ${feedback}`} role="status" aria-live="polite"><span className="feedback-icon">{feedback === 'wrong' ? '×' : feedback === 'complete' ? '✓' : '●'}</span><div><strong>{feedbackCopy.title}</strong><p>{feedbackCopy.body}</p></div></div>
            <div className="move-progress"><div className="progress-label"><span>Card progress</span><strong>{userMovesComplete} / {card.userMoveTarget} user moves</strong></div><ProgressStrip current={userMovesComplete} total={card.userMoveTarget} /></div>
            <div className={`move-trail${revealedMoves.length ? '' : ' empty'}`} aria-live="polite"><span>Moves played</span>{revealedMoves.length ? <ol>{revealedMoves.map((move, index) => <li key={`${move}-${index}`}><b>{index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : '…'}</b>{move}</li>)}</ol> : <p>Nothing is revealed until you play it.</p>}</div>
            {feedback === 'complete' ? <div className="ratings"><button onClick={() => rateCard('again')}><strong>Again</strong><span>later today</span></button><button onClick={() => rateCard('hard')}><strong>Hard</strong><span>2 days</span></button><button className="primary" onClick={() => rateCard('good')}><strong>Good</strong><span>5 days</span></button><button onClick={() => rateCard('easy')}><strong>Easy</strong><span>12 days</span></button></div> : card.kind === 'opening' ? <div className="next-up"><span>Next unlock</span><p>After 3 successful days and a 14-day interval, learn <strong>{card.nextMove}</strong> as a focused card.</p></div> : <div className="next-up puzzle-note"><span>Motif deck</span><p>This puzzle is interleaved with opening reviews. Its rating and interval are tracked independently.</p>{card.sourceUrl && <a href={card.sourceUrl} target="_blank" rel="noreferrer">View original puzzle ↗</a>}</div>}
          </aside>
        </section>
      </>}
      {view === 'repertoire' && <RepertoireView onImport={() => setShowImport(true)} onBrowse={() => setShowTree(true)} />}
      {view === 'progress' && <ProgressView reviewed={reviewed} />}
      {showImport && <ImportDialog onClose={() => setShowImport(false)} />}
      {showTree && <TreeBrowser onClose={() => setShowTree(false)} theme={boardTheme} pieceSet={pieceSet} />}
      <footer className="source-footer">Board interaction by <a href="https://github.com/lichess-org/chessground" target="_blank" rel="noreferrer">Chessground</a> · Cburnett and Merida pieces from Lichess</footer>
    </main>
  );
}
