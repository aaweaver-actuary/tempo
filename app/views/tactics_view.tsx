import { Chess, Square, Move } from "chess.js";
import { useState, useRef, useEffect, useMemo, useCallback } from "react";
import { OutcomeFlash } from "../components/board-controls";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import { API_URL, assetUrl } from "../const";
import { playMoveSound } from "../lib/move-sound";
import {
  readTacticProgress,
  tacticProgressKey,
  advanceTacticProgress,
  writeTacticProgress,
} from "../lib/tactics-progress";
import {
  tacticExamples,
  demoCards,
  alternateTactics,
  tacticMotifs,
} from "../samples";
import { PackagedPuzzle, PracticeCard } from "../types";

export default function TacticsView({
  theme,
  pieceSet,
  onQueueChanged,
}: {
  theme: BoardTheme;
  pieceSet: PieceSet;
  onQueueChanged: () => void;
}) {
  const [motif, setMotif] = useState("hangingPiece");
  const [stage, setStage] = useState("easy");
  const [progress, setProgress] = useState(readTacticProgress);
  const [step, setStep] = useState(0);
  const [hint, setHint] = useState(false);
  const [failed, setFailed] = useState(false);
  const [outcome, setOutcome] = useState<"correct" | "wrong" | null>(null);
  const [boardAttempt, setBoardAttempt] = useState(0);
  const finishingRef = useRef(false);
  const attemptTokenRef = useRef(0);
  const advanceTimerRef = useRef<number | undefined>(undefined);
  const [catalog, setCatalog] = useState<PackagedPuzzle[]>([]);
  useEffect(() => {
    fetch(assetUrl("data/tactics-decks.json"))
      .then((response) => response.json() as Promise<PackagedPuzzle[]>)
      .then((value) => setCatalog(value))
      .catch(() => setCatalog([]));
  }, []);
  const progressKey = tacticProgressKey(motif, stage);
  const currentProgress = progress[progressKey] ?? { clean: 0, index: 0 };
  const packagedDeck = useMemo(
    () =>
      catalog
        .filter((record) => record.DeckId === `${motif}-${stage}`)
        .sort((a, b) => a.DeckPosition - b.DeckPosition)
        .map((record): PracticeCard => ({
          id: record.PuzzleId,
          kind: "puzzle",
          title: record.DeckId,
          subtitle: `${record.DeckId} · ${record.Rating}`,
          startingFen: record.FEN,
          moves: record.Moves.split(/\s+/),
          userMoveTarget: Math.max(1, record.Moves.split(/\s+/).length),
          sourceUrl: `https://lichess.org/training/${record.PuzzleId}`,
          orientation: "white",
        })),
    [catalog, motif, stage],
  );
  const deck = packagedDeck.length
    ? packagedDeck
    : [tacticExamples[motif] ?? demoCards[2], ...alternateTactics];
  const puzzle = deck[currentProgress.index % deck.length];
  const puzzleSide =
    puzzle.orientation ??
    (new Chess(puzzle.startingFen).turn() === "b" ? "black" : "white");
  const packagedRecord = catalog.find(
    (record) => record.PuzzleId === puzzle.id.replace(/^lichess-/, ""),
  );
  const [fen, setFen] = useState(puzzle.startingFen);

  const resetAttempt = useCallback(
    (markFailed = false) => {
      attemptTokenRef.current += 1;
      finishingRef.current = false;
      setFen(puzzle.startingFen);
      setStep(0);
      setHint(markFailed);
      setFailed(markFailed);
      setOutcome(null);
      setBoardAttempt((value) => value + 1);
    },
    [puzzle],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      resetAttempt();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [resetAttempt]);
  useEffect(
    () => () => {
      if (advanceTimerRef.current) window.clearTimeout(advanceTimerRef.current);
    },
    [],
  );

  function finish() {
    if (finishingRef.current) return;
    finishingRef.current = true;
    const clean = !failed;
    const completedKey = progressKey;
    const token = ++attemptTokenRef.current;
    setOutcome(clean ? "correct" : "wrong");
    if (
      packagedRecord &&
      typeof window !== "undefined" &&
      ["localhost", "127.0.0.1"].includes(location.hostname)
    ) {
      void fetch(`${API_URL}/api/tactics/attempt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          puzzle_id: packagedRecord.PuzzleId,
          deck_id: packagedRecord.DeckId,
          correct: clean,
          clean,
          source_fen: packagedRecord.FEN,
          moves: packagedRecord.Moves.split(/\s+/),
          rating: packagedRecord.Rating,
        }),
      })
        .then((response) => {
          if (response.ok) onQueueChanged();
        })
        .catch(() => undefined);
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
    try {
      move = board.move({ from, to, promotion: "q" });
    } catch {
      return;
    }
    if (!board.isCheckmate() && move.san !== puzzle.moves[step]) {
      setFailed(true);
      setHint(true);
      setBoardAttempt((value) => value + 1);
      return;
    }
    setFen(board.fen());
    const replyIndex = step + 1;
    if (replyIndex >= puzzle.moves.length) {
      finish();
      return;
    }
    const replyBoard = new Chess(board.fen());
    replyBoard.move(puzzle.moves[replyIndex]);
    setFen(replyBoard.fen());
    playMoveSound();
    setStep(replyIndex + 1);
    setHint(false);
    if (replyIndex + 1 >= puzzle.moves.length) finish();
  }

  const current = tacticMotifs.find((item) => item[0] === motif)!;
  const target = stage === "focused" ? 250 : 100;
  const previousStages = ["easy", "medium", "hard"];
  return (
    <section className="tactics-page">
      <div className="workspace-title">
        <div>
          <h1>Tactics</h1>
          <span>
            {currentProgress.clean} clean solves in {current[1].toLowerCase()} ·{" "}
            {stage}
          </span>
        </div>
        <div className="stage-tabs">
          {["easy", "medium", "hard", "focused"].map((item, index) => (
            <button
              key={item}
              disabled={
                index > 0 &&
                (progress[tacticProgressKey(motif, previousStages[index - 1])]
                  ?.clean ?? 0) < 100
              }
              className={stage === item ? "active" : ""}
              onClick={() => setStage(item)}
            >
              {item === "focused"
                ? "Focused · 250"
                : `${item[0].toUpperCase() + item.slice(1)} · 100`}
            </button>
          ))}
        </div>
      </div>
      <div className="tactics-workspace">
        <aside className="motif-rail">
          {tacticMotifs.map(([id, name, icon]) => {
            const clean = progress[tacticProgressKey(id, stage)]?.clean ?? 0;
            return (
              <button
                className={motif === id ? "active" : ""}
                key={id}
                onClick={() => setMotif(id)}
              >
                <b>{icon}</b>
                <span>{name}</span>
                <small>
                  {clean
                    ? `${clean} / ${stage === "focused" ? 250 : 100}`
                    : "Not started"}
                </small>
              </button>
            );
          })}
        </aside>
        <div className="board-column centered-board">
          <Chessboard
            key={`${puzzle.id}:${boardAttempt}`}
            fen={fen}
            expectedSan={puzzle.moves[step]}
            locked={Boolean(outcome) || step >= puzzle.moves.length}
            showHint={hint}
            theme={theme}
            pieceSet={pieceSet}
            onMove={movePiece}
            orientation={puzzleSide}
          />
          <div className="board-tools">
            <button
              onClick={() => {
                setFailed(true);
                setHint(true);
              }}
            >
              ⌁ <span>Show move</span>
            </button>
            <button onClick={() => resetAttempt(true)}>
              ↻ <span>Restart</span>
            </button>
            {puzzle.sourceUrl && (
              <a href={puzzle.sourceUrl} target="_blank" rel="noreferrer">
                ↗ <span>Original</span>
              </a>
            )}
          </div>
          {outcome && <OutcomeFlash outcome={outcome} />}
        </div>
        <aside className="study-panel tactic-study">
          <span className="pill puzzle">{stage}</span>
          <h2>{current[1]}</h2>
          <p className="side-to-play">
            {puzzleSide === "black" ? "black" : "white"} to play
          </p>
          <p className="tactic-rating">
            Puzzle {currentProgress.index + 1} of {target}
          </p>
          {!outcome && (
            <div
              className={`feedback ${failed ? "wrong" : "ready"}`}
              role="status"
            >
              <span className="feedback-icon">{failed ? "×" : "●"}</span>
              <div>
                <strong>{failed ? "Follow the arrow" : "Your move"}</strong>
              </div>
            </div>
          )}
          <div className="stage-progress">
            <span
              style={{
                width: `${Math.min(100, (currentProgress.clean / target) * 100)}%`,
              }}
            />
          </div>
          <small>Easy → Medium → Hard → Focused</small>
        </aside>
      </div>
    </section>
  );
}
