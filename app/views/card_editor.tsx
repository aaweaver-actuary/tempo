import { Square, Chess } from "chess.js";
import { useEffect, useMemo, useState } from "react";
import { MoveNavigator } from "../components/board-controls";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import { pieceSymbols } from "../const";
import { PracticeCard } from "../types";
import { editFenSquare, fenAfterMoves, moveFenPiece } from "../utils/fen";

export default function CardEditor({ card, theme, pieceSet, onClose, onSave }: { card: PracticeCard; theme: BoardTheme; pieceSet: PieceSet; onClose: () => void; onSave: (card: PracticeCard) => void }) {
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