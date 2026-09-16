import { Chess, Square, Move } from "chess.js";
import { useState, useRef, useEffect, useCallback } from "react";
import { OutcomeFlash } from "../components/board-controls";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import { API_URL, STANDARD_FEN } from "../const";
import { playMoveSound } from "../lib/move-sound";
import {
  readTacticProgress,
  tacticProgressKey,
  advanceTacticProgress,
  writeTacticProgress,
} from "../lib/tactics-progress";
import { tacticMotifs } from "../samples";
import { asCardId, asFenString, PackagedPuzzle, PracticeCard } from "../types";
import { usesLocalApi } from "../utils/local";
import { loadTacticsDeck, readWorkspaceData, invalidateWorkspaceData } from "../lib/workspace-data";

const emptyPuzzle: PracticeCard = { id: asCardId("loading"), startingFen: asFenString(STANDARD_FEN), moves: [], kind: "puzzle", title: "", subtitle: "", userMoveTarget: 0 };
function firstUnfinishedStage(progress: ReturnType<typeof readTacticProgress>, motif: string) {
  return ["easy", "medium", "hard", "focused"].find(stage => (progress[tacticProgressKey(motif, stage)]?.clean ?? 0) < (stage === "focused" ? 250 : 100)) ?? "focused";
}

function TacticsSubHeader({
  currentProgress,
  current,
  stage,
}: {
  currentProgress: { clean: number; index: number };
  current: string[];
  stage: string;
}) {
  return (
    <>
      <span>
        {currentProgress.clean} clean solves in {current[1].toLowerCase()} ·{" "}
        {stage}
      </span>
    </>
  );
}

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
  const [activePuzzleId, setActivePuzzleId] = useState("");
  const finishingRef = useRef(false);
  const failedRef = useRef(false);
  const discoveryId = useRef("");
  const [saveError, setSaveError] = useState("");
  const attemptTokenRef = useRef(0);
  const advanceTimerRef = useRef<number | undefined>(undefined);
  const [prepared, setPrepared] = useState<Array<{ record: PackagedPuzzle; card: PracticeCard }>>([]);
  const [deckState, setDeckState] = useState("loading");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    void loadTacticsDeck(motif, stage).then(deck => {
      if (active) { setPrepared(deck); setDeckState(deck.length ? "ready" : "empty"); }
    }).catch(error => { if (active) setDeckState(error.message); });
    return () => { active = false; };
  }, [motif, stage, retry]);
  useEffect(() => {
    if (!usesLocalApi()) return;
    void readWorkspaceData<ReturnType<typeof readTacticProgress>>(`${API_URL}/api/tactics/progress`)
      .then(value => {
        setProgress(value);
        setStage(firstUnfinishedStage(value, "hangingPiece"));
      })
      .catch(() =>
        setSaveError(
          "Could not load discovery progress. Check the local service.",
        ),
      );
  }, []);
  const progressKey = tacticProgressKey(motif, stage);
  const currentProgress = progress[progressKey] ?? { clean: 0, index: 0 };
  const selectedPuzzle = prepared[currentProgress.index % prepared.length];
  const puzzle = selectedPuzzle?.card ?? emptyPuzzle;
  const puzzleSide =
    puzzle.orientation ??
    (new Chess(puzzle.startingFen).turn() === "b" ? "black" : "white");
  const packagedRecord = selectedPuzzle?.record;
  const [fen, setFen] = useState(puzzle.startingFen);

  const resetAttempt = useCallback(
    (markFailed = false) => {
      window.clearTimeout(advanceTimerRef.current);
      attemptTokenRef.current += 1;
      finishingRef.current = false;
      discoveryId.current = crypto.randomUUID();
      setSaveError("");
      failedRef.current = markFailed;
      setFen(puzzle.startingFen);
      setActivePuzzleId(puzzle.id);
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

  async function finish() {
    if (finishingRef.current) return;
    finishingRef.current = true;
    const clean = !failedRef.current;
    const completedKey = progressKey;
    const token = ++attemptTokenRef.current;
    setOutcome(clean ? "correct" : "wrong");
    if (packagedRecord && usesLocalApi()) {
      try {
        const response = await fetch(`${API_URL}/api/tactics/attempt`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            attempt_id: discoveryId.current,
            puzzle_id: packagedRecord.PuzzleId,
            deck_id: packagedRecord.DeckId,
            correct: clean,
            clean,
            source_fen: packagedRecord.FEN,
            moves: packagedRecord.Moves.split(/\s+/),
            rating: packagedRecord.Rating,
          }),
        });
        if (!response.ok) throw new Error();
        invalidateWorkspaceData();
        onQueueChanged();
      } catch {
        setSaveError(
          "Could not save this attempt. Retry to save it before continuing.",
        );
        finishingRef.current = false;
        return;
      }
    }
    advanceTimerRef.current = window.setTimeout(() => {
      if (attemptTokenRef.current !== token) return;
      setProgress((current) => {
        const next = advanceTacticProgress(
          current,
          completedKey,
          clean,
          puzzle.id,
        );
        writeTacticProgress(next);
        return next;
      });
    }, 750);
  }

  function movePiece(from: Square, to: Square) {
    if (finishingRef.current || step % 2 || step >= puzzle.moves.length) return;
    const board = new Chess(fen);
    let move: Move;
    try {
      move = board.move({ from, to, promotion: "q" });
    } catch {
      return;
    }
    if (!board.isCheckmate() && move.san !== puzzle.moves[step]) {
      failedRef.current = true;
      setFailed(true);
      setHint(true);
      setBoardAttempt((value) => value + 1);
      return;
    }
    setFen(asFenString(board.fen()));
    if (board.isCheckmate()) {
      setStep(puzzle.moves.length);
      void finish();
      return;
    }
    const replyIndex = step + 1;
    if (replyIndex >= puzzle.moves.length) {
      setStep(replyIndex);
      void finish();
      return;
    }
    const replyBoard = new Chess(board.fen());
    replyBoard.move(puzzle.moves[replyIndex]);
    setFen(asFenString(replyBoard.fen()));
    playMoveSound();
    setStep(replyIndex + 1);
    setHint(failedRef.current);
    if (replyIndex + 1 >= puzzle.moves.length) void finish();
  }

  const current = tacticMotifs.find((item) => item[0] === motif)!;
  const target = stage === "focused" ? 250 : 100;
  const previousStages = ["easy", "medium", "hard"];
  if (deckState !== "ready" || activePuzzleId !== puzzle.id)
    return (
      <section className="library-page" role="status">
        {deckState === "loading" || deckState === "ready" ? "Preparing puzzles…" : deckState === "empty" ? "No validated puzzles are available for this stage. Check the packaged tactics data." : deckState}
        {deckState !== "loading" && <button onClick={() => { setDeckState("loading"); setRetry(value => value + 1); }}>Retry</button>}
      </section>
    );
  return (
    <section className="tactics-page">
      <div className="workspace-title">
        <div>
          <h1>Tactics</h1>
          <TacticsSubHeader
            currentProgress={
              progress[tacticProgressKey(motif, stage)] ?? {
                clean: 0,
                index: 0,
              }
            }
            current={current}
            stage={stage}
          />
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
              onClick={() => { setDeckState("loading"); setStage(item); }}
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
                onClick={() => { setDeckState("loading"); setMotif(id); setStage(firstUnfinishedStage(progress, id)); }}
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
                failedRef.current = true;
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
          {(outcome || failed) && <OutcomeFlash outcome={outcome ?? "wrong"} />}
          {saveError && (
            <div role="alert">
              {saveError}
              <button onClick={() => void finish()}>Retry save</button>
            </div>
          )}
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
