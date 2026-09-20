import { BoardTools } from "../components/board-workspace";
import { Chess, Square, Move } from "chess.js";
import {
  useState,
  useRef,
  useEffect,
  useLayoutEffect,
  useCallback,
} from "react";
import { OutcomeFlash } from "../components/board-controls";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import { useBoardPublisher } from "../hooks/use-board-publisher";
import { API_URL, STANDARD_FEN } from "../const";
import { playMoveSound } from "../lib/move-sound";
import {
  readTacticProgress,
  advanceTacticProgress,
  writeTacticProgress,
} from "../lib/tactics-progress";
import { TacticalCatalogPanel, type MotifRecommendation } from "../components/tactical-catalog";
import {
  loadTacticalCatalog,
  setPackActivation,
  migrateDemoProgress,
  type TacticalCatalog,
  type TacticalPack,
} from "../lib/tactical-catalog";
import { asCardId, asFenString, PackagedPuzzle, PracticeCard } from "../types";
import { usesLocalApi } from "../utils/local";
import {
  loadTacticsDeck,
  readWorkspaceData,
  invalidateWorkspaceData,
} from "../lib/workspace-data";
import { motifRecommendationsSchema, tacticProgressSchema } from "../domain/schemas";
import { useTaskTabs } from "../components/task-tabs";

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
  useSharedBoard = false,
}: {
  theme: BoardTheme;
  pieceSet: PieceSet;
  onQueueChanged: () => void;
  useSharedBoard?: boolean;
}) {
  const [retry, setRetry] = useState(0);
  const [selectedPackId, setSelectedPackId] = useState(
    () =>
      localStorage.getItem("tempo-tactic-selected-pack-v1") ??
      "hangingPiece-easy-01",
  );
  const motif = selectedPackId.split("-")[0];
  const stage = selectedPackId.slice(motif.length + 1);
  const [catalog, setCatalog] = useState<TacticalCatalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [activationError, setActivationError] = useState("");
  const [activationBusy, setActivationBusy] = useState(false);
  const [recommendations, setRecommendations] = useState<MotifRecommendation[]>([]);
  const tools = useTaskTabs(["Solve", "Packs"], "Solve", "tempo-tactics-tools");
  const [progress, setProgress] = useState(readTacticProgress);
  const [progressReady, setProgressReady] = useState(() => !usesLocalApi());
  const [prepared, setPrepared] = useState<
    Array<{ record: PackagedPuzzle; card: PracticeCard }>
  >([]);
  const [deckState, setDeckState] = useState("loading");
  const selectPack = (pack: TacticalPack) => {
    setDeckState("loading");
    setSelectedPackId(pack.id);
    localStorage.setItem("tempo-tactic-selected-pack-v1", pack.id);
  };
  const activatePacks = async (ids: string[], active: boolean) => {
    setActivationBusy(true);
    setActivationError("");
    try {
      setCatalog(await setPackActivation(ids, active));
      invalidateWorkspaceData();
      onQueueChanged();
    } catch (error) {
      setActivationError(
        error instanceof Error
          ? error.message
          : "Could not save activation. Retry.",
      );
    } finally {
      setActivationBusy(false);
    }
  };
  useEffect(() => {
    let active = true;
    void loadTacticalCatalog()
      .then(async (value) => {
        if (!usesLocalApi()) {
          const activePacks = new Set(
            JSON.parse(
              localStorage.getItem("tempo-tactic-active-packs-v1") ?? "[]",
            ),
          );
          value = {
            ...value,
            packs: value.packs.map((pack) => ({
              ...pack,
              active: activePacks.has(pack.id),
            })),
          };
          const migrated = await migrateDemoProgress(
            value,
            readTacticProgress(),
          );
          if (active) setProgress(migrated);
        }
        if (active) {
          setCatalog(value);
          setCatalogError("");
        }
      })
      .catch((error) => {
        if (active) setCatalogError(error.message);
      });
    return () => {
      active = false;
    };
  }, [retry]);
  useEffect(() => {
    if (!usesLocalApi()) return;
    let active = true;
    void readWorkspaceData(
      `${API_URL}/api/game-insights/motifs`,
      motifRecommendationsSchema,
    ).then((payload) => {
      if (active) setRecommendations(payload.recommendations);
    }).catch(() => {
      if (active) setRecommendations([]);
    });
    return () => {
      active = false;
    };
  }, [retry]);
  const progressKey = selectedPackId;
  const currentProgress = progress[progressKey] ?? { clean: 0, index: 0 };
  const discoveredIds = new Set(
    currentProgress.discoveredIds ?? currentProgress.cleanIds ?? [],
  );
  const cleanIds = new Set(currentProgress.cleanIds ?? []);
  const selectedPuzzle =
    prepared.find(({ card }) => !discoveredIds.has(card.id)) ??
    prepared.find(({ card }) => !cleanIds.has(card.id));
  const puzzle = selectedPuzzle?.card ?? emptyPuzzle;
  const packagedRecord = selectedPuzzle?.record;
  const entryKey = `${progressKey}:${puzzle.id}`;
  const [attempt, setAttempt] = useState<TacticAttempt>(() => ({
    entryKey,
    discoveryId: crypto.randomUUID(),
    fen: puzzle.startingFen,
    step: 0,
    guided: false,
    phase: "playerTurn",
    positionRevision: 0,
  }));
  const finishingRef = useRef("");
  const [saveError, setSaveError] = useState("");
  const { setShellBoardForOwner, releaseShellBoardForOwner } =
    useBoardPublisher();
  const attemptTokenRef = useRef(0);
  const advanceTimers = useRef(new Set<number>());
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

        setProgressReady(true);
        setSaveError("");
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
  const deckReady =
    deckState === "ready" && prepared[0]?.record.DeckId === `${motif}-${stage}`;
  const puzzleSide =
    puzzle.orientation ??
    (new Chess(puzzle.startingFen).turn() === "b" ? "black" : "white");
  const { fen, step, guided: failed, positionRevision: boardAttempt } = attempt;
  const hint = attempt.guided;
  const outcome =
    attempt.phase === "feedbackPause"
      ? attempt.guided
        ? "wrong"
        : "correct"
      : null;

  const attemptRef = useRef(attempt);
  const entryKeyRef = useRef(entryKey);
  const fenRef = useRef(fen);
  const stepRef = useRef(step);
  const puzzleMovesRef = useRef(puzzle.moves);

  useEffect(() => {
    attemptRef.current = attempt;
    entryKeyRef.current = entryKey;
    fenRef.current = fen;
    stepRef.current = step;
    puzzleMovesRef.current = puzzle.moves;
  }, [attempt, entryKey, fen, puzzle.moves, step]);

  useLayoutEffect(() => {
    setAttempt((current) => {
      if (
        current.entryKey === entryKey &&
        current.fen === puzzle.startingFen &&
        current.phase === "playerTurn" &&
        !current.guided
      ) {
        return current;
      }
      attemptTokenRef.current += 1;
      finishingRef.current = "";
      setSaveError("");
      return {
        entryKey,
        discoveryId: crypto.randomUUID(),
        fen: puzzle.startingFen,
        step: 0,
        guided: false,
        phase: "playerTurn",
        positionRevision: current.positionRevision + 1,
      };
    });
  }, [entryKey, puzzle.startingFen]);
  useEffect(() => {
    const timers = advanceTimers.current;
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, []);

  const guideAttempt = useCallback(
    (restart = false) => {
      if (attemptRef.current.phase === "feedbackPause") return;
      setAttempt((current) => ({
        ...current,
        entryKey: current.entryKey || entryKey,
        discoveryId: current.discoveryId || crypto.randomUUID(),
        fen: restart ? puzzle.startingFen : current.fen,
        step: restart ? 0 : current.step,
        guided: true,
        phase: "guided",
        positionRevision: current.positionRevision + 1,
      }));
    },
    [entryKey, puzzle.startingFen],
  );

  const finish = useCallback(
    async (completedAttempt = attemptRef.current) => {
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
    },
    [onQueueChanged, packagedRecord, progressKey, puzzle.id],
  );

  const movePiece = useCallback(
    (from: Square, to: Square) => {
      const currentAttempt = attemptRef.current;
      const currentEntryKey = entryKeyRef.current;
      const currentFen = fenRef.current;
      const currentStep = stepRef.current;
      const currentPuzzleMoves = puzzleMovesRef.current;
      if (
        currentAttempt.phase === "feedbackPause" ||
        currentAttempt.entryKey !== currentEntryKey ||
        currentStep % 2 ||
        currentStep >= currentPuzzleMoves.length
      )
        return;
      const board = new Chess(currentFen);
      let move: Move;
      try {
        move = board.move({ from, to, promotion: "q" });
      } catch {
        guideAttempt();
        return;
      }
      if (
        !board.isCheckmate() &&
        move.san !== currentPuzzleMoves[currentStep]
      ) {
        guideAttempt();
        return;
      }
      const nextAttempt = {
        ...currentAttempt,
        fen: asFenString(board.fen()),
        step: currentStep + 1,
      };
      if (board.isCheckmate()) {
        void finish({
          ...nextAttempt,
          step: currentPuzzleMoves.length,
        });
        return;
      }
      const replyIndex = currentStep + 1;
      if (replyIndex >= currentPuzzleMoves.length) {
        void finish(nextAttempt);
        return;
      }
      const replyBoard = new Chess(board.fen());
      replyBoard.move(currentPuzzleMoves[replyIndex]);
      nextAttempt.fen = asFenString(replyBoard.fen());
      nextAttempt.step = replyIndex + 1;
      playMoveSound();
      if (replyIndex + 1 >= currentPuzzleMoves.length) void finish(nextAttempt);
      else setAttempt(nextAttempt);
    },
    [finish, guideAttempt],
  );

  useLayoutEffect(() => {
    if (!useSharedBoard) return;
    setShellBoardForOwner("tactics", {
      unavailable:
        !progressReady || !deckReady || !selectedPuzzle
          ? saveError || "Preparing puzzles…"
          : undefined,
      fen,
      expectedSan: puzzle.moves[step],
      interactionMode:
        Boolean(outcome) || step >= puzzle.moves.length ? "readonly" : "legal",
      showHint: hint,
      theme,
      pieceSet,
      orientation: puzzleSide,
      shapes: [],
      drawnShapes: [],
      positionRevision: boardAttempt,
      onMove: movePiece,
      onSquareSelect: undefined,
      onFreeMove: undefined,
      onDrawnShapesChange: undefined,
      onFlip: undefined,
    });
    return () => releaseShellBoardForOwner("tactics");
  }, [
    progressReady,
    deckReady,
    selectedPuzzle,
    saveError,
    boardAttempt,
    fen,
    hint,
    movePiece,
    outcome,
    pieceSet,
    puzzle.moves,
    puzzleSide,
    releaseShellBoardForOwner,
    setShellBoardForOwner,
    step,
    theme,
    useSharedBoard,
  ]);

  const current = [
    motif,
    catalog?.themes.find((item) => item.id === motif)?.name ?? motif,
  ];
  const target = 25;
  if (
    !catalog ||
    !progressReady ||
    !deckReady ||
    (selectedPuzzle && attempt.entryKey !== entryKey)
  )
    return (
      <section className="library-page" role="status">
        {catalogError ||
          saveError ||
          (deckState === "loading" || deckState === "ready"
            ? "Preparing puzzles…"
            : deckState === "empty"
              ? "No validated puzzles are available for this pack. Check the packaged tactics data."
              : deckState)}
        <button
          onClick={() => {
            invalidateWorkspaceData();
            setDeckState("loading");
            setRetry((value) => value + 1);
          }}
        >
          Retry
        </button>
      </section>
    );
  if (!selectedPuzzle)
    return (
      <section className="library-page" aria-live="polite">
        <p>This pack is complete. Select another pack to continue practicing.</p>
        <TacticalCatalogPanel
          catalog={catalog}
          progress={progress}
          selectedPackId={selectedPackId}
          onSelect={selectPack}
          onActivate={(ids, active) => void activatePacks(ids, active)}
          busy={activationBusy}
          recommendations={recommendations}
          onStartSuggested={(recommendation) => {
            const suggestedPack = catalog.packs.find(
              (pack) => pack.id === recommendation.recommended_pack_id,
            );
            if (!suggestedPack) return;
            if (!suggestedPack.active) void activatePacks([suggestedPack.id], true);
            selectPack(suggestedPack);
          }}
        />
      </section>
    );
  return (
    <section className="tactics-page" {...tools.panelProps}>
      <div className="workspace-title">
        <div>
          <h1>Tactics</h1>
          <TacticsSubHeader
            currentProgress={currentProgress}
            current={current}
            stage={stage}
          />
        </div>
      </div>
      <div className="workspace-context-tabs">{tools.tabs}</div>
      {activationError && (
        <div role="alert">
          {activationError}
          <button onClick={() => setActivationError("")}>Dismiss</button>
        </div>
      )}
      <div className="tactics-workspace">
        {tools.activeTab === "Packs" && (
          <TacticalCatalogPanel
            catalog={catalog}
            progress={progress}
            selectedPackId={selectedPackId}
            onSelect={selectPack}
            onActivate={(ids, active) => void activatePacks(ids, active)}
            busy={activationBusy}
            recommendations={recommendations}
            onStartSuggested={(recommendation) => {
              const suggestedPack = catalog.packs.find(
                (pack) => pack.id === recommendation.recommended_pack_id,
              );
              if (!suggestedPack) return;
              if (!suggestedPack.active) {
                void activatePacks([suggestedPack.id], true);
              }
              selectPack(suggestedPack);
            }}
          />
        )}
        <div className="board-column centered-board">
          {!useSharedBoard && (
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
          )}
          <BoardTools>
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
          </BoardTools>
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
          <span className="pill puzzle">{stage.replace(/-\d{2}$/, "")}</span>
          <h2>{current[1]}</h2>
          <p className="side-to-play">{puzzleSide} to play</p>
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
          <small>All packs are directly accessible</small>
        </aside>
      </div>
    </section>
  );
}
