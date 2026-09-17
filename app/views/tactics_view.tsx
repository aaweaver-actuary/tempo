import { Chess, Square, Move } from "chess.js";
import { useState, useRef, useEffect } from "react";
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
import {
  loadTacticsDeck,
  readWorkspaceData,
  invalidateWorkspaceData,
} from "../lib/workspace-data";
import { tacticProgressSchema } from "../domain/schemas";

const emptyPuzzle: PracticeCard = {
  id: asCardId("loading"),
  startingFen: asFenString(STANDARD_FEN),
  moves: [],
  kind: "puzzle",
  title: "",
  subtitle: "",
  userMoveTarget: 0,
};
type TacticAttempt = {
  entryKey: string;
  discoveryId: string;
  fen: PracticeCard["startingFen"];
  step: number;
  guided: boolean;
  phase: "playerTurn" | "guided" | "feedbackPause";
  positionRevision: number;
};
function firstUnfinishedStage(
  progress: ReturnType<typeof readTacticProgress>,
  motif: string,
) {
  return (
    ["easy", "medium", "hard", "focused"].find(
      (stage) =>
        (progress[tacticProgressKey(motif, stage)]?.clean ?? 0) <
        (stage === "focused" ? 250 : 100),
    ) ?? "focused"
  );
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
  const [progressReady, setProgressReady] = useState(() => !usesLocalApi());
  const [attempt, setAttempt] = useState<TacticAttempt>({
    entryKey: "",
    discoveryId: "",
    fen: emptyPuzzle.startingFen,
    step: 0,
    guided: false,
    phase: "playerTurn",
    positionRevision: 0,
  });
  const finishingRef = useRef("");
  const [saveError, setSaveError] = useState("");
  const attemptTokenRef = useRef(0);
  const advanceTimers = useRef(new Set<number>());
  const [prepared, setPrepared] = useState<
    Array<{ record: PackagedPuzzle; card: PracticeCard }>
  >([]);
  const [deckState, setDeckState] = useState("loading");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    void loadTacticsDeck(motif, stage)
      .then((deck) => {
        if (active) {
          setPrepared(
            [...deck].sort(
              (left, right) =>
                left.record.DeckPosition - right.record.DeckPosition,
            ),
          );
          setDeckState(deck.length ? "ready" : "empty");
        }
      })
      .catch((error) => {
        if (active) setDeckState(error.message);
      });
    return () => {
      active = false;
    };
  }, [motif, stage, retry]);
  useEffect(() => {
    if (!usesLocalApi()) return;
    let active = true;
    void readWorkspaceData(
      `${API_URL}/api/tactics/progress`,
      tacticProgressSchema,
    )
      .then((value) => {
        if (!active) return;
        setProgress(value);
        setStage(firstUnfinishedStage(value, "hangingPiece"));
        setProgressReady(true);
      })
      .catch(() => {
        if (active)
          setSaveError(
            "Could not load discovery progress. Check the local service.",
          );
      });
    return () => {
      active = false;
    };
  }, [retry]);
  const progressKey = tacticProgressKey(motif, stage);
  const deckReady =
    deckState === "ready" && prepared[0]?.record.DeckId === `${motif}-${stage}`;
  const currentProgress = progress[progressKey] ?? { clean: 0, index: 0 };
  const discoveredIds = new Set(
    currentProgress.discoveredIds ?? currentProgress.cleanIds ?? [],
  );
  const cleanIds = new Set(currentProgress.cleanIds ?? []);
  const selectedPuzzle =
    prepared.find(({ card }) => !discoveredIds.has(card.id)) ??
    prepared.find(({ card }) => !cleanIds.has(card.id));
  const puzzle = selectedPuzzle?.card ?? emptyPuzzle;
  const puzzleSide =
    puzzle.orientation ??
    (new Chess(puzzle.startingFen).turn() === "b" ? "black" : "white");
  const packagedRecord = selectedPuzzle?.record;
  const entryKey = `${progressKey}:${puzzle.id}`;
  const { fen, step, guided: failed, positionRevision: boardAttempt } = attempt;
  const hint = attempt.guided;
  const outcome =
    attempt.phase === "feedbackPause"
      ? attempt.guided
        ? "wrong"
        : "correct"
      : null;

  useEffect(() => {
    const timer = window.setTimeout(() => {
      attemptTokenRef.current += 1;
      finishingRef.current = "";
      setSaveError("");
      setAttempt({
        entryKey,
        discoveryId: crypto.randomUUID(),
        fen: puzzle.startingFen,
        step: 0,
        guided: false,
        phase: "playerTurn",
        positionRevision: 0,
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [entryKey, puzzle.startingFen]);
  useEffect(() => {
    const timers = advanceTimers.current;
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, []);

  async function finish(completedAttempt = attempt) {
    if (finishingRef.current === completedAttempt.discoveryId) return;
    finishingRef.current = completedAttempt.discoveryId;
    const clean = !completedAttempt.guided;
    const completedKey = progressKey;
    const token = attemptTokenRef.current;
    setAttempt({ ...completedAttempt, phase: "feedbackPause" });
    if (packagedRecord && usesLocalApi()) {
      try {
        const response = await fetch(`${API_URL}/api/tactics/attempt`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            attempt_id: completedAttempt.discoveryId,
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
        if (attemptTokenRef.current === token)
          setSaveError(
            "Could not save this attempt. Retry to save it before continuing.",
          );
        if (finishingRef.current === completedAttempt.discoveryId)
          finishingRef.current = "";
        return;
      }
    }
    const timer = window.setTimeout(() => {
      advanceTimers.current.delete(timer);
      // Commit only this deck's puzzle identity. A stale completion must never
      // mutate another deck's active attempt or its feedback.
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
    advanceTimers.current.add(timer);
  }

  function guideAttempt(restart = false) {
    if (attempt.phase === "feedbackPause") return;
    setAttempt((current) => ({
      ...current,
      fen: restart ? puzzle.startingFen : current.fen,
      step: restart ? 0 : current.step,
      guided: true,
      phase: "guided",
      positionRevision: current.positionRevision + 1,
    }));
  }

  function movePiece(from: Square, to: Square) {
    if (
      attempt.phase === "feedbackPause" ||
      attempt.entryKey !== entryKey ||
      step % 2 ||
      step >= puzzle.moves.length
    )
      return;
    const board = new Chess(fen);
    let move: Move;
    try {
      move = board.move({ from, to, promotion: "q" });
    } catch {
      return;
    }
    if (!board.isCheckmate() && move.san !== puzzle.moves[step]) {
      guideAttempt();
      return;
    }
    const nextAttempt = {
      ...attempt,
      fen: asFenString(board.fen()),
      step: step + 1,
    };
    if (board.isCheckmate()) {
      void finish({ ...nextAttempt, step: puzzle.moves.length });
      return;
    }
    const replyIndex = step + 1;
    if (replyIndex >= puzzle.moves.length) {
      void finish(nextAttempt);
      return;
    }
    const replyBoard = new Chess(board.fen());
    replyBoard.move(puzzle.moves[replyIndex]);
    nextAttempt.fen = asFenString(replyBoard.fen());
    nextAttempt.step = replyIndex + 1;
    playMoveSound();
    if (replyIndex + 1 >= puzzle.moves.length) void finish(nextAttempt);
    else setAttempt(nextAttempt);
  }

  const current = tacticMotifs.find((item) => item[0] === motif)!;
  const target = stage === "focused" ? 250 : 100;
  const previousStages = ["easy", "medium", "hard"];
  if (progressReady && deckReady && !selectedPuzzle)
    return (
      <section className="library-page" role="status">
        <h1 className="sr-only">Tactics</h1>All packaged puzzles in this stage
        have a clean solve.
        <div className="stage-tabs">
          {tacticMotifs.map(([id, name]) => (
            <button
              key={id}
              onClick={() => {
                setDeckState("loading");
                setMotif(id);
                setStage(firstUnfinishedStage(progress, id));
              }}
            >
              {name}
            </button>
          ))}
        </div>
      </section>
    );
  if (!progressReady || !deckReady || attempt.entryKey !== entryKey)
    return (
      <section className="library-page" role="status">
        {saveError ||
          (deckState === "loading" || deckState === "ready"
            ? "Preparing puzzles…"
            : deckState === "empty"
              ? "No validated puzzles are available for this stage. Check the packaged tactics data."
              : deckState)}
        {deckState !== "loading" && (
          <button
            onClick={() => {
              setDeckState("loading");
              setRetry((value) => value + 1);
            }}
          >
            Retry
          </button>
        )}
      </section>
    );
  return (
    <section className="tactics-page">
      <div className="workspace-title">
        <div>
          <h1 className="sr-only">Tactics</h1>
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
              onClick={() => {
                setDeckState("loading");
                setStage(item);
              }}
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
                onClick={() => {
                  setDeckState("loading");
                  setMotif(id);
                  setStage(firstUnfinishedStage(progress, id));
                }}
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
            key={entryKey}
            positionRevision={boardAttempt}
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
              disabled={attempt.phase === "feedbackPause"}
              onClick={() => guideAttempt()}
            >
              ⌁ <span>Show move</span>
            </button>
            <button
              disabled={attempt.phase === "feedbackPause"}
              onClick={() => guideAttempt(true)}
            >
              ↻ <span>Restart</span>
            </button>
            {puzzle.sourceUrl && (
              <a href={puzzle.sourceUrl} target="_blank" rel="noreferrer">
                ↗ <span>Original</span>
              </a>
            )}
          </div>
          {(outcome || failed) && (
            <OutcomeFlash
              key={`${attempt.discoveryId}:${outcome ?? "wrong"}`}
              outcome={outcome ?? "wrong"}
            />
          )}
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
            Puzzle {packagedRecord?.DeckPosition} of {target}
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
