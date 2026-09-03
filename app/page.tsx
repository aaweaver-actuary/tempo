'use client';

import { Chess, Move, Square } from 'chess.js';
import { DragEvent, useEffect, useMemo, useState } from 'react';

const repertoireLine = ['e4', 'c5', 'Nf3', 'd6', 'd4', 'cxd4'];
const pieces: Record<string, string> = {
  wp: '♙', wn: '♘', wb: '♗', wr: '♖', wq: '♕', wk: '♔',
  bp: '♟', bn: '♞', bb: '♝', br: '♜', bq: '♛', bk: '♚',
};

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

function Chessboard({
  fen,
  expectedSan,
  lastMove,
  locked,
  showHint,
  onMove,
}: {
  fen: string;
  expectedSan?: string;
  lastMove?: [string, string];
  locked: boolean;
  showHint: boolean;
  onMove: (from: Square, to: Square) => void;
}) {
  const chess = useMemo(() => new Chess(fen), [fen]);
  const [selected, setSelected] = useState<Square | null>(null);
  const legalTargets = selected
    ? chess.moves({ square: selected, verbose: true }).map((move) => move.to)
    : [];
  const hintMove = expectedSan ? moveForSan(chess, expectedSan) : undefined;

  function chooseSquare(square: Square) {
    if (locked) return;
    if (selected && legalTargets.includes(square)) {
      onMove(selected, square);
      setSelected(null);
      return;
    }
    const piece = chess.get(square);
    setSelected(piece?.color === chess.turn() ? square : null);
  }

  function drop(event: DragEvent<HTMLButtonElement>, to: Square) {
    event.preventDefault();
    const from = event.dataTransfer.getData('text/plain') as Square;
    if (from) onMove(from, to);
    setSelected(null);
  }

  return (
    <div className="board-frame" aria-label="Interactive chessboard">
      <div className="board">
        {chess.board().flatMap((rank, rankIndex) =>
          rank.map((piece, fileIndex) => {
            const square = `${String.fromCharCode(97 + fileIndex)}${8 - rankIndex}` as Square;
            const isDark = (rankIndex + fileIndex) % 2 === 1;
            const isTarget = legalTargets.includes(square);
            const isLast = lastMove?.includes(square);
            const isHint = showHint && !!hintMove && [hintMove.from, hintMove.to].includes(square);
            return (
              <button
                className={`square ${isDark ? 'dark' : 'light'}${selected === square ? ' selected' : ''}${isLast ? ' last' : ''}${isHint ? ' hinted' : ''}`}
                key={square}
                onClick={() => chooseSquare(square)}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => drop(event, square)}
                aria-label={`${square}${piece ? `, ${piece.color === 'w' ? 'white' : 'black'} ${piece.type}` : ''}`}
              >
                {fileIndex === 0 && <span className="rank-label">{8 - rankIndex}</span>}
                {rankIndex === 7 && <span className="file-label">{String.fromCharCode(97 + fileIndex)}</span>}
                {isTarget && <span className={`move-dot${piece ? ' capture' : ''}`} />}
                {piece && (
                  <span
                    className={`piece ${piece.color === 'w' ? 'white-piece' : 'black-piece'}`}
                    draggable={!locked && piece.color === chess.turn()}
                    onDragStart={(event) => event.dataTransfer.setData('text/plain', square)}
                  >
                    {pieces[`${piece.color}${piece.type}`]}
                  </span>
                )}
              </button>
            );
          }),
        )}
      </div>
    </div>
  );
}

function ProgressStrip({ current }: { current: number }) {
  return (
    <div className="progress-strip" aria-label={`${current} of ${repertoireLine.length} moves complete`}>
      {repertoireLine.map((move, index) => (
        <div className={`progress-segment${index < current ? ' filled' : ''}`} key={`${move}-${index}`} />
      ))}
    </div>
  );
}

function RepertoireView({ onImport }: { onImport: () => void }) {
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
          </article>
        ))}
        <button className="new-repertoire-card" onClick={onImport}><span>＋</span><strong>Add a repertoire</strong><small>PGN files stay on this computer</small></button>
      </div>
      <div className="unlock-explainer">
        <div><span className="step-number done">1</span><strong>Shared prefix</strong><small>One card for the opening’s first 6 moves</small></div>
        <i />
        <div><span className="step-number active">2</span><strong>Reach maturity</strong><small>Confident recall unlocks the next branch</small></div>
        <i />
        <div><span className="step-number">3</span><strong>Focused response</strong><small>Train only the opponent move and your reply</small></div>
      </div>
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
  const [finished, setFinished] = useState(false);
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="import-dialog" role="dialog" aria-modal="true" aria-labelledby="import-title" onMouseDown={(event) => event.stopPropagation()}>
        <button className="close-button" onClick={onClose} aria-label="Close import dialog">×</button>
        {finished ? <div className="import-finished"><span>✓</span><h2 id="import-title">Ready to train</h2><p><strong>{fileName}</strong> would add 24 unique cards and merge 7 shared prefixes in the full local app.</p><button className="primary-button" onClick={onClose}>View repertoire</button></div> : <>
          <p className="eyebrow">Local import</p><h2 id="import-title">Add PGN repertoire</h2><p className="dialog-copy">Your file is parsed on this computer. Re-uploading the same positions updates the repertoire without duplicating cards.</p>
          <label className={`drop-zone${fileName ? ' has-file' : ''}`}><input type="file" accept=".pgn" onChange={(event) => setFileName(event.target.files?.[0]?.name ?? '')} /><span>{fileName ? '♟' : '⇧'}</span><strong>{fileName || 'Choose a PGN file'}</strong><small>{fileName ? 'Ready to preview' : 'or drop it here · .pgn only'}</small></label>
          <label className="depth-setting"><span><strong>Initial line depth</strong><small>New prefix cards begin at this length</small></span><span className="stepper"><button onClick={() => setInitialDepth(Math.max(2, initialDepth - 1))}>−</button><b>{initialDepth} moves</b><button onClick={() => setInitialDepth(Math.min(20, initialDepth + 1))}>＋</button></span></label>
          <div className="dialog-footer"><span><i className="status-dot" /> Stored locally</span><button className="primary-button" disabled={!fileName} onClick={() => setFinished(true)}>Preview import</button></div>
        </>}
      </section>
    </div>
  );
}

export default function Home() {
  const initialFen = new Chess().fen();
  const [view, setView] = useState<View>('train');
  const [fen, setFen] = useState(initialFen);
  const [step, setStep] = useState(0);
  const [feedback, setFeedback] = useState<Feedback>('ready');
  const [lastMove, setLastMove] = useState<[string, string]>();
  const [locked, setLocked] = useState(false);
  const [showHint, setShowHint] = useState(false);
  const [cardsLeft, setCardsLeft] = useState(12);
  const [reviewed, setReviewed] = useState(0);
  const [showImport, setShowImport] = useState(false);

  useEffect(() => {
    const today = localDayKey();
    if (localStorage.getItem('tempo-day') !== today) {
      localStorage.setItem('tempo-day', today);
      localStorage.setItem('tempo-cards-left', '12');
      localStorage.setItem('tempo-reviewed', '0');
    }
    setCardsLeft(Number(localStorage.getItem('tempo-cards-left') ?? 12));
    setReviewed(Number(localStorage.getItem('tempo-reviewed') ?? 0));
  }, []);

  function resetLine() {
    setFen(initialFen); setStep(0); setFeedback('ready'); setLastMove(undefined); setLocked(false); setShowHint(false);
  }

  function rateCard() {
    setCardsLeft((count) => { const next = Math.max(0, count - 1); localStorage.setItem('tempo-cards-left', String(next)); return next; });
    setReviewed((count) => { const next = count + 1; localStorage.setItem('tempo-reviewed', String(next)); return next; });
    resetLine();
  }

  function tryMove(from: Square, to: Square) {
    if (locked || step >= repertoireLine.length || step % 2 === 1) return;
    const position = new Chess(fen);
    let move: Move | null = null;
    try { move = position.move({ from, to, promotion: 'q' }); } catch { return; }
    if (!move || move.san !== repertoireLine[step]) { setFeedback('wrong'); setShowHint(false); return; }
    setFen(position.fen()); setLastMove([move.from, move.to]); setFeedback('correct'); setShowHint(false);
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
    ready: { title: step === 0 ? 'Your move' : 'Find the continuation', body: step === 0 ? 'Play White’s first move.' : 'Continue the line for White.' },
    correct: { title: 'That’s it', body: 'Black is replying…' },
    wrong: { title: 'Try that position again', body: 'That move is legal, but it isn’t in this repertoire.' },
    complete: { title: 'Line recalled', body: 'Rate how difficult that felt.' },
  }[feedback];

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
            <Chessboard fen={fen} expectedSan={repertoireLine[step]} lastMove={lastMove} locked={locked || step >= repertoireLine.length || cardsLeft === 0} showHint={showHint} onMove={tryMove} />
            <div className="board-tools"><button onClick={() => setShowHint((value) => !value)} disabled={feedback === 'complete' || cardsLeft === 0}>⌁ <span>{showHint ? 'Hide hint' : 'Show hint'}</span></button><button onClick={resetLine}>↻ <span>Restart line</span></button><span className="board-note">Drag a piece or tap two squares</span></div>
          </div>
          <aside className="study-panel">
            <div className="card-meta"><span className="pill">Review</span><span>Prefix card · 6 moves</span></div>
            <div className="opening-title"><p>White repertoire</p><h2>Open Sicilian</h2><span>Najdorf setup</span></div>
            <div className={`feedback ${feedback}`} role="status" aria-live="polite"><span className="feedback-icon">{feedback === 'wrong' ? '×' : feedback === 'complete' ? '✓' : '●'}</span><div><strong>{feedbackCopy.title}</strong><p>{feedbackCopy.body}</p></div></div>
            <div className="move-progress"><div className="progress-label"><span>Card progress</span><strong>{Math.min(step, repertoireLine.length)} / {repertoireLine.length}</strong></div><ProgressStrip current={step} /></div>
            <div className="line-preview" aria-label="Moves in this card"><span className={step === 0 ? 'current' : ''}>1. e4</span><span>c5</span><span className={step === 2 ? 'current' : ''}>2. Nf3</span><span>d6</span><span className={step === 4 ? 'current' : ''}>3. d4</span><span>cxd4</span></div>
            {feedback === 'complete' ? <div className="ratings"><button onClick={rateCard}><strong>Again</strong><span>&lt; 1 min</span></button><button onClick={rateCard}><strong>Hard</strong><span>2 days</span></button><button className="primary" onClick={rateCard}><strong>Good</strong><span>5 days</span></button><button onClick={rateCard}><strong>Easy</strong><span>12 days</span></button></div> : <div className="next-up"><span>Next unlock</span><p>Reach mature status to learn <strong>4. Nxd4</strong> as a focused card.</p></div>}
          </aside>
        </section>
      </>}
      {view === 'repertoire' && <RepertoireView onImport={() => setShowImport(true)} />}
      {view === 'progress' && <ProgressView reviewed={reviewed} />}
      {showImport && <ImportDialog onClose={() => setShowImport(false)} />}
    </main>
  );
}
