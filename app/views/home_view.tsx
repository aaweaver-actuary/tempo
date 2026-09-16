import { useState, useCallback, useEffect, useRef } from "react";
import { BoardTheme, PieceSet } from "../components/chessboard";
import { API_URL } from "../const";
import { ImportDialogBox } from "../import_dialog_box";
import { moveSoundEnabled, playMoveSound } from "../lib/move-sound";
import { bundledRepertoires, demoCards } from "../samples";
import {
  View,
  LocalRepertoire,
  AnalysisLine,
  CardId,
  asRepertoireId,
} from "../types";
import { canonicalFenKey, canonicalizeLine } from "../utils/canonical-line";
import {
  indexRepertoirePositions,
  IndexedPosition,
} from "../lib/position-similarity";
import { usesLocalApi, localDayKey } from "../utils/local";
import { trainedColor } from "../utils/cards";
import BuilderView from "./analysis_view";
import CardEditor from "./card_editor";
import EndgamesView from "./endgames_view";
import { GamesView } from "./games_view";
import ProgressView from "./progress_view";
import RepertoireView from "./repertoire_view";
import SettingsView from "./settings_view";
import TacticsView from "./tactics_view";
import { loadPositionAnnotation } from "../utils/position-annotations";
import { useGameSync } from "../hooks/use-game-sync";
import { Chess, Move, Square } from "chess.js";
import {
  useTrainingStore,
  selectHomeViewState,
  selectTrainingActions,
} from "../state/training-store";
import Footer from "../components/Footer";
import Navbar from "../components/Navbar";
import BrandButton from "../components/buttons/BrandButton";
import SoundToggleButton from "../components/buttons/SoundToggleButton";
import SavedLocallyButton from "../components/buttons/SavedLocallyButton";
import DemoBanner from "../components/DemoBanner";
import TrainingView from "./training_view";
import { fetchAndInitializeQueue } from "./fetchAndInitializeQueue";
import { Settings } from "../utils/settings";
import { TreeBrowser } from "./tree_browser";

export default function Home() {
  const gameSync = useGameSync();
  const [currentView, setCurrentView] = useState<View>("train");
  const branchPositions = useRef<IndexedPosition[]>([]);
  const {
    practiceCards,
    importedRepertoires,
    activeCardIndex,
    currentFenString,
    step,
    isLocked,
    cardsLeft,
    reviewed,
    showImport,
    showTree,
    editorCard,
    seenMoves,
    teachingEncounterKey,
    teachingReadyCard,
    firstCleanPasses,
    attemptFailed,
    failureFen,
    dailyQueue,
    boardTheme,
    pieceSet,
    soundOn,
    databaseQueue,
    serviceError,
  } = useTrainingStore(selectHomeViewState);
  const {
    setPracticeCards,
    setImportedRepertoires,
    setActiveCardIndex,
    setCurrentFenString,
    setStep,
    setFeedback,
    setLastMove,
    setOpponentLastMove,
    setIsLocked,
    setBoardAttempt,
    setShowHint,
    setCardsLeft,
    setReviewed,
    setShowImport,
    setShowTree,
    setEditorCard,
    setSuggestShorter,
    setSeenMoves,
    setTeachingEncounterKey,
    setTeachingReadyCard,
    setFirstCleanPasses,
    setAttemptFailed,
    setFailureAnnotation,
    setFailureFen,
    setDailyQueue,
    setQueueNotice,
    setBoardTheme,
    setPieceSet,
    setSoundOn,
    setDatabaseQueue,
    setServiceError,
    initializeCardState,
    resetTrainingLine,
  } = useTrainingStore(selectTrainingActions);
  const reviewPending = useRef(false);
  const attemptGeneration = useRef(0);
  const completionTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const activeQueueEntry = useRef<number | undefined>(undefined);
  const card = practiceCards[activeCardIndex] ?? demoCards[0];
  const repertoireLine = card.moves;

  const refreshDatabaseQueue = useCallback(async () => {
    await fetchAndInitializeQueue(
      setDatabaseQueue,
      setServiceError,
      setPracticeCards,
      setDailyQueue,
      setCardsLeft,
      reviewPending,
      activeQueueEntry,
      setActiveCardIndex,
      attemptGeneration,
      setCurrentFenString,
      setStep,
      setFeedback,
      setLastMove,
      setOpponentLastMove,
      setIsLocked,
      setShowHint,
      setTeachingEncounterKey,
      setAttemptFailed,
      setFailureAnnotation,
      setFailureFen,
    )();
  }, [
    activeQueueEntry,
    attemptGeneration,
    reviewPending,
    setActiveCardIndex,
    setAttemptFailed,
    setCardsLeft,
    setCurrentFenString,
    setDailyQueue,
    setDatabaseQueue,
    setFailureAnnotation,
    setFailureFen,
    setFeedback,
    setIsLocked,
    setLastMove,
    setOpponentLastMove,
    setPracticeCards,
    setServiceError,
    setShowHint,
    setStep,
    setTeachingEncounterKey,
  ]);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
    if (usesLocalApi() && currentView === "train") {
      queueMicrotask(() => void refreshDatabaseQueue());
      void fetch(`${API_URL}/api/repertoire/lines`)
        .then(async (response) => {
          if (!response.ok) return;
          const body = (await response.json()) as {
            lines: Record<string, unknown>[];
          };
          const lines: AnalysisLine[] = body.lines.map((line) =>
            canonicalizeLine({
              id: String(line.id),
              repertoireId: asRepertoireId(String(line.repertoire_id)),
              repertoireName: String(line.repertoire_name),
              title: String(line.name ?? ""),
              side: line.trained_color === "black" ? "black" : "white",
              startingFen: String(line.start_fen),
              moves: line.moves as string[],
            }),
          );
          branchPositions.current = indexRepertoirePositions(lines);
        })
        .catch(() => {
          branchPositions.current = [];
        });
    }
  }, [currentView, refreshDatabaseQueue]);
  useEffect(() => {
    if (!usesLocalApi() || !attemptFailed || !card.queueEntryId) return;
    void fetch(`${API_URL}/api/queue/entries/${card.queueEntryId}/fail`, {
      method: "POST",
    }).catch(() =>
      setQueueNotice(
        "Could not save guided-attempt state. Keep this page open and retry.",
      ),
    );
  }, [attemptFailed, card.queueEntryId, setQueueNotice]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams(window.location.search);
      if (
        params.has("code") ||
        ["analysis", "builder"].includes(
          sessionStorage.getItem("tempo-return-view") ?? "",
        )
      ) {
        setCurrentView("builder");
        sessionStorage.removeItem("tempo-return-view");
      }
      if (usesLocalApi()) {
        setPracticeCards([]);
        setDailyQueue([]);
        setImportedRepertoires([]);
        setSeenMoves(
          new Set(JSON.parse(localStorage.getItem("tempo-seen-moves") ?? "[]")),
        );
        setBoardTheme(
          (localStorage.getItem("tempo-board-theme") as BoardTheme) ?? "brown",
        );
        setPieceSet(
          (localStorage.getItem("tempo-piece-set") as PieceSet) ?? "cburnett",
        );
        setSoundOn(moveSoundEnabled());
        void refreshDatabaseQueue();
        return;
      }
      const today = localDayKey();
      if (localStorage.getItem("tempo-day") !== today) {
        localStorage.setItem("tempo-day", today);
        localStorage.setItem("tempo-cards-left", "12");
        localStorage.setItem("tempo-reviewed", "0");
        localStorage.setItem(
          "tempo-daily-queue",
          JSON.stringify(
            Array.from({ length: 12 }, (_, index) => index % demoCards.length),
          ),
        );
      }
      setCardsLeft(Number(localStorage.getItem("tempo-cards-left") ?? 12));
      setReviewed(Number(localStorage.getItem("tempo-reviewed") ?? 0));
      setSeenMoves(
        new Set(JSON.parse(localStorage.getItem("tempo-seen-moves") ?? "[]")),
      );
      setFirstCleanPasses(
        new Set(
          JSON.parse(localStorage.getItem("tempo-first-clean-passes") ?? "[]"),
        ),
      );
      const savedRepertoires = JSON.parse(
        localStorage.getItem("tempo-imported-repertoires") ?? "[]",
      ) as LocalRepertoire[];
      const tombstones = new Set<string>(
        JSON.parse(localStorage.getItem("tempo-repertoire-tombstones") ?? "[]"),
      );
      const initialized =
        localStorage.getItem("tempo-repertoires-initialized") === "true";
      const seededRepertoires = initialized
        ? savedRepertoires
        : [
            ...bundledRepertoires.filter((item) => !tombstones.has(item.id)),
            ...savedRepertoires.filter(
              (saved) =>
                !bundledRepertoires.some((sample) => sample.id === saved.id),
            ),
          ];
      localStorage.setItem("tempo-repertoires-initialized", "true");
      localStorage.setItem(
        "tempo-imported-repertoires",
        JSON.stringify(seededRepertoires),
      );
      setImportedRepertoires(seededRepertoires);
      const savedCards = [
        ...new Map(
          seededRepertoires
            .flatMap((repertoire) => repertoire.cards)
            .map((savedCard) => [savedCard.id, savedCard]),
        ).values(),
      ];
      const loadedCards = [...demoCards, ...savedCards];
      setPracticeCards(loadedCards);
      const storedQueue = JSON.parse(
        localStorage.getItem("tempo-daily-queue") ??
          JSON.stringify(
            Array.from({ length: 12 }, (_, index) => index % demoCards.length),
          ),
      ) as number[];
      setDailyQueue(storedQueue);
      setCardsLeft(storedQueue.length);
      setActiveCardIndex(storedQueue[0] ?? 0);
      const loadedCard = loadedCards[storedQueue[0] ?? 0];
      if (loadedCard) {
        initializeCardState(loadedCard);
      }
      setBoardTheme(
        (localStorage.getItem("tempo-board-theme") as BoardTheme | null) ??
          "brown",
      );
      setPieceSet(
        (localStorage.getItem("tempo-piece-set") as PieceSet | null) ??
          "cburnett",
      );
      setSoundOn(moveSoundEnabled());
      void refreshDatabaseQueue();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [
    initializeCardState,
    refreshDatabaseQueue,
    setActiveCardIndex,
    setBoardTheme,
    setCardsLeft,
    setDailyQueue,
    setFirstCleanPasses,
    setImportedRepertoires,
    setPieceSet,
    setPracticeCards,
    setReviewed,
    setSeenMoves,
    setSoundOn,
  ]);

  function addImportedRepertoire(repertoire: LocalRepertoire) {
    if (usesLocalApi()) return;
    const repertoires = [
      ...importedRepertoires.filter((item) => item.id !== repertoire.id),
      repertoire,
    ];
    setImportedRepertoires(repertoires);
    localStorage.setItem(
      "tempo-imported-repertoires",
      JSON.stringify(repertoires),
    );
    const existingIds = new Set(practiceCards.map((item) => item.id));
    const additions = repertoire.cards.filter(
      (item) => !existingIds.has(item.id),
    );
    const cards = [...practiceCards, ...additions];
    setPracticeCards(cards);
    const addedIndexes = additions.map((item) =>
      cards.findIndex((cardItem) => cardItem.id === item.id),
    );
    const queue = [...dailyQueue, ...addedIndexes];
    setDailyQueue(queue);
    setCardsLeft(queue.length);
    localStorage.setItem("tempo-daily-queue", JSON.stringify(queue));
    localStorage.setItem("tempo-cards-left", String(queue.length));
  }

  function renameLocalRepertoire(id: string, name: string) {
    const next = importedRepertoires.map((item) =>
      item.id === id ? { ...item, title: name } : item,
    );
    setImportedRepertoires(next);
    localStorage.setItem("tempo-imported-repertoires", JSON.stringify(next));
  }

  function deleteLocalRepertoire(id: string) {
    if (usesLocalApi()) return;
    const removed = importedRepertoires.find((item) => item.id === id);
    const nextRepertoires = importedRepertoires.filter(
      (item) => item.id !== id,
    );
    setImportedRepertoires(nextRepertoires);
    localStorage.setItem(
      "tempo-imported-repertoires",
      JSON.stringify(nextRepertoires),
    );
    const tombstones = new Set<string>(
      JSON.parse(localStorage.getItem("tempo-repertoire-tombstones") ?? "[]"),
    );
    tombstones.add(id);
    localStorage.setItem(
      "tempo-repertoire-tombstones",
      JSON.stringify([...tombstones]),
    );
    if (!removed) return;
    const retainedIds = new Set(
      nextRepertoires.flatMap((item) => item.cards.map((entry) => entry.id)),
    );
    const removedIds = new Set(
      removed.cards
        .filter((entry) => !retainedIds.has(entry.id))
        .map((item) => item.id),
    );
    const queuedIds = dailyQueue
      .map((index) => practiceCards[index]?.id)
      .filter(
        (cardId): cardId is CardId =>
          Boolean(cardId) && !removedIds.has(cardId),
      );
    const nextCards = practiceCards.filter((item) => !removedIds.has(item.id));
    const nextQueue = queuedIds
      .map((cardId) => nextCards.findIndex((item) => item.id === cardId))
      .filter((index) => index >= 0);
    setPracticeCards(nextCards);
    setDailyQueue(nextQueue);
    setCardsLeft(nextQueue.length);
    setActiveCardIndex(nextQueue[0] ?? 0);
    localStorage.setItem("tempo-daily-queue", JSON.stringify(nextQueue));
    localStorage.setItem("tempo-cards-left", String(nextQueue.length));
    resetLine(nextCards[nextQueue[0] ?? 0] ?? demoCards[0]);
  }

  function resetLine(nextCard = card) {
    attemptGeneration.current += 1;
    clearTimeout(completionTimer.current);
    resetTrainingLine(nextCard);
  }

  function tryMove(from: Square, to: Square) {
    const currentTurn =
      new Chess(card.startingFen).turn() === "b" ? "black" : "white";
    if (
      isLocked ||
      step >= card.moves.length ||
      currentTurn !== trainedColor(card) ||
      card.kind === "endgame"
    )
      return;
    const position = new Chess(currentFenString);
    let move: Move | null = null;
    try {
      move = position.move({ from, to, promotion: "q" });
    } catch {
      setBoardAttempt((value) => value + 1);
      setFeedback("wrong");
      setShowHint(true);
      setAttemptFailed(true);
      setFailureFen(currentFenString);
      setQueueNotice("Again recorded · replay the guided move");
      return;
    }
    if (!move) return;
    if (position.isCheckmate()) {
      setLastMove([move.from, move.to]);
      setOpponentLastMove(undefined);
      setStep(card.moves.length);
      completeAttempt(position.fen());
      return;
    }
    if (move.san !== card.moves[step]) {
      const alternateBranch = usesLocalApi()
        ? branchPositions.current.some(
            (other) =>
              other.repertoireId === card.repertoireId &&
              canonicalFenKey(other.fen) ===
                canonicalFenKey(currentFenString) &&
              other.nextUci === `${move.from}${move.to}${move.promotion ?? ""}`,
          )
        : demoCards.some(
            (other) =>
              other.id !== card.id &&
              other.startingFen === card.startingFen &&
              other.moves
                .slice(0, step)
                .every((san, index) => san === card.moves[index]) &&
              other.moves[step] === move?.san,
          );
      if (alternateBranch) {
        setBoardAttempt((value) => value + 1);
        setFeedback("branch");
        setShowHint(true);
        setQueueNotice(
          "Valid repertoire move · follow the arrow for today’s branch",
        );
        return;
      }
      setFeedback("wrong");
      setBoardAttempt((value) => value + 1);
      setShowHint(true);
      setAttemptFailed(true);
      setFailureFen(currentFenString);
      setQueueNotice("Again recorded · replay this move, then finish the line");
      return;
    }
    markMoveSeen(step);
    setCurrentFenString(position.fen());
    setLastMove([move.from, move.to]);
    setOpponentLastMove(undefined);
    setFeedback("correct");
    setShowHint(false);
    setQueueNotice("");
    const opponentStep = step + 1;
    setStep(opponentStep);
    if (opponentStep >= card.moves.length) {
      completeAttempt(position.fen());
      return;
    }
    setIsLocked(true);
    const generation = attemptGeneration.current;
    window.setTimeout(() => {
      if (generation !== attemptGeneration.current) return;
      const replyPosition = new Chess(position.fen());
      const reply = replyPosition.move(card.moves[opponentStep]);
      if (!reply) {
        setIsLocked(false);
        return;
      }
      const nextStep = opponentStep + 1;
      setCurrentFenString(replyPosition.fen());
      setLastMove([reply.from, reply.to]);
      setOpponentLastMove([reply.from, reply.to]);
      setStep(nextStep);
      setIsLocked(false);
      setFeedback(nextStep >= card.moves.length ? "complete" : "ready");
      playMoveSound();
      if (nextStep >= card.moves.length) completeAttempt(replyPosition.fen());
    }, 420);
  }

  function changeBoardTheme(value: BoardTheme) {
    setBoardTheme(value);
    localStorage.setItem("tempo-board-theme", value);
  }

  function changePieceSet(value: PieceSet) {
    setPieceSet(value);
    localStorage.setItem("tempo-piece-set", value);
  }

  function changeSound(value: boolean) {
    setSoundOn(value);
    localStorage.setItem("tempo-move-sound", String(value));
    if (value) playMoveSound(true);
  }

  async function rateCard(outcome: "again" | "correct") {
    if (reviewPending.current || cardsLeft === 0) return;
    reviewPending.current = true;
    setIsLocked(true);
    if (databaseQueue && card.backendId) {
      try {
        if (attemptFailed) {
          const saved = await fetch(
            `${API_URL}/api/queue/entries/${card.queueEntryId}/fail`,
            { method: "POST" },
          );
          if (!saved.ok) throw new Error();
        }
        const response = await fetch(
          `${API_URL}/api/cards/${card.backendId}/review`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              outcome,
              guided: attemptFailed,
              queue_entry_id: card.queueEntryId,
            }),
          },
        );
        if (!response.ok) throw new Error();
        setReviewed((count) => count + 1);
        setQueueNotice("");
        await refreshDatabaseQueue();
        reviewPending.current = false;
        return;
      } catch {
        reviewPending.current = false;
        setQueueNotice("The local database could not save this result.");
        return;
      }
    }
    if (usesLocalApi()) {
      reviewPending.current = false;
      setServiceError("Connect to the local service before reviewing.");
      return;
    }
    const firstClean = outcome === "correct" && !firstCleanPasses.has(card.id);
    const nextQueue = dailyQueue.slice(1);
    if (outcome === "again") {
      const key = `tempo-failures-${card.id}`;
      const failures = Number(localStorage.getItem(key) ?? 0) + 1;
      localStorage.setItem(key, String(failures));
      if (failures >= 3) setSuggestShorter(true);
      nextQueue.splice(Math.min(4, nextQueue.length), 0, activeCardIndex);
      setQueueNotice("");
    } else if (firstClean) {
      nextQueue.push(activeCardIndex);
      const nextPasses = new Set(firstCleanPasses).add(card.id);
      setFirstCleanPasses(nextPasses);
      localStorage.setItem(
        "tempo-first-clean-passes",
        JSON.stringify([...nextPasses]),
      );
      setQueueNotice("");
    } else setQueueNotice("");
    setDailyQueue(nextQueue);
    localStorage.setItem("tempo-daily-queue", JSON.stringify(nextQueue));
    setCardsLeft(nextQueue.length);
    localStorage.setItem("tempo-cards-left", String(nextQueue.length));
    setReviewed((count) => {
      const next = count + 1;
      localStorage.setItem("tempo-reviewed", String(next));
      return next;
    });
    const nextIndex = nextQueue[0] ?? 0;
    setActiveCardIndex(nextIndex);
    resetLine(practiceCards[nextIndex]);
    reviewPending.current = false;
  }

  function completeAttempt(finalFen: string) {
    setCurrentFenString(finalFen);
    setIsLocked(true);
    setFeedback("complete");
    const generation = attemptGeneration.current;
    clearTimeout(completionTimer.current);
    completionTimer.current = setTimeout(() => {
      if (generation === attemptGeneration.current)
        void rateCard(attemptFailed ? "again" : "correct");
    }, 750);
  }

  useEffect(
    () => () => {
      attemptGeneration.current += 1;
      clearTimeout(completionTimer.current);
    },
    [],
  );

  function markMoveSeen(moveStep: number) {
    const key = `${card.backendId ?? card.id}:${card.revision ?? 1}:${moveStep}`;
    setSeenMoves((current) => {
      const next = new Set(current).add(key);
      localStorage.setItem(
        "tempo-seen-moves",
        JSON.stringify(Array.from(next)),
      );
      return next;
    });
  }

  const currentMoveKey = `${card.backendId ?? card.id}:${card.revision ?? 1}:${step}`;
  const teachingCardKey = `${card.backendId ?? card.id}:${card.revision ?? 1}`;
  useEffect(() => {
    let active = true;
    if (!card.backendId || card.kind !== "opening") {
      queueMicrotask(() => setTeachingReadyCard(teachingCardKey));
      return;
    }
    void fetch(`${API_URL}/api/cards/${card.backendId}/teaching`)
      .then((response) => {
        if (!response.ok) throw new Error();
        return response.json() as Promise<{
          states: { revision: number; ply: number }[];
        }>;
      })
      .then(({ states }) => {
        if (!active) return;
        setSeenMoves(
          (current) =>
            new Set(
              [...current]
                .filter((key) => !key.startsWith(`${card.backendId}:`))
                .concat(
                  states.map(
                    (state) =>
                      `${card.backendId}:${state.revision}:${state.ply}`,
                  ),
                ),
            ),
        );
        setTeachingReadyCard(teachingCardKey);
      })
      .catch(() => {
        if (active) setTeachingReadyCard(teachingCardKey);
      });
    return () => {
      active = false;
    };
  }, [
    card.backendId,
    card.kind,
    setSeenMoves,
    setTeachingReadyCard,
    teachingCardKey,
  ]);
  const isPlayerTurn =
    step < repertoireLine.length &&
    new Chess(currentFenString).turn() ===
      (trainedColor(card) === "white" ? "w" : "b");

  useEffect(() => {
    queueMicrotask(() => {
      if (!isPlayerTurn) {
        setTeachingEncounterKey(null);
        return;
      }
      if (card.kind !== "opening") {
        setTeachingEncounterKey(null);
        return;
      }
      if (teachingReadyCard !== teachingCardKey) return;
      if (teachingEncounterKey === currentMoveKey) return;
      if (seenMoves.has(currentMoveKey)) {
        setTeachingEncounterKey(null);
        return;
      }
      setTeachingEncounterKey(currentMoveKey);
      setSeenMoves((current) => {
        if (current.has(currentMoveKey)) return current;
        const next = new Set(current).add(currentMoveKey);
        localStorage.setItem("tempo-seen-moves", JSON.stringify([...next]));
        return next;
      });
      if (card.backendId) {
        void fetch(`${API_URL}/api/cards/${card.backendId}/teaching`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision: card.revision ?? 1, ply: step }),
        }).catch(() => undefined);
      }
    });
  }, [
    card.kind,
    card.backendId,
    card.revision,
    currentMoveKey,
    isPlayerTurn,
    seenMoves,
    step,
    teachingEncounterKey,
    teachingReadyCard,
    teachingCardKey,
    setSeenMoves,
    setTeachingEncounterKey,
  ]);

  useEffect(() => {
    const repertoireId = card.repertoireId;
    if (!attemptFailed || !repertoireId || currentFenString !== failureFen) {
      queueMicrotask(() => setFailureAnnotation(undefined));
      return;
    }
    let active = true;
    void loadPositionAnnotation(repertoireId, currentFenString).then(
      (value) => {
        if (active) setFailureAnnotation(value);
      },
    );
    return () => {
      active = false;
    };
  }, [
    attemptFailed,
    card.repertoireId,
    currentFenString,
    failureFen,
    setFailureAnnotation,
  ]);

  const boardWorkspace = [
    "train",
    "tactics",
    "endgames",
    "builder",
    "games",
  ].includes(currentView);

  function handleAttemptFailure() {
    if (!attemptFailed) {
      setAttemptFailed(true);
      setQueueNotice("Again recorded · finish with guidance");
    }
    setShowHint((value) => !value);
    setFailureFen(currentFenString);
  }

  function resetCardAttempt() {
    resetLine();
    setAttemptFailed(true);
    setShowHint(true);
    setFailureFen(card.startingFen);
    setQueueNotice("Again recorded · restarted in guided mode");
  }

  return (
    <main
      className={`app-shell${boardWorkspace ? " board-workspace-shell" : ""}`}
    >
      <header className="topbar">
        <BrandButton setView={setCurrentView} />
        <Navbar view={currentView} setView={setCurrentView} />
        <div className="top-actions">
          <SoundToggleButton soundOn={soundOn} changeSound={changeSound} />
          <SavedLocallyButton setShowImport={setShowImport} />
        </div>
      </header>
      {!usesLocalApi() && <DemoBanner />}

      {currentView === "train" && (
        <TrainingView
          dateLabel={new Date().toLocaleDateString()}
          serviceError={serviceError}
          refreshDatabaseQueue={refreshDatabaseQueue}
          cardsLeft={cardsLeft}
          card={card}
          boardTheme={boardTheme}
          pieceSet={pieceSet}
          rateCard={rateCard}
          handleAttemptFailure={handleAttemptFailure}
          resetCardAttempt={resetCardAttempt}
          setEditorCard={setEditorCard}
          onMove={tryMove}
        />
      )}
      {currentView === "tactics" && (
        <TacticsView
          theme={boardTheme}
          pieceSet={pieceSet}
          onQueueChanged={() => void refreshDatabaseQueue()}
        />
      )}
      {currentView === "endgames" && (
        <EndgamesView
          theme={boardTheme}
          pieceSet={pieceSet}
          onQueueChanged={() => void refreshDatabaseQueue()}
        />
      )}
      {currentView === "repertoire" && (
        <RepertoireView
          imported={importedRepertoires}
          onImport={() => setShowImport(true)}
          onBrowse={(id) => {
            const existing = JSON.parse(
              localStorage.getItem("tempo-builder-session") ?? "null",
            );
            if (existing)
              localStorage.setItem(
                "tempo-builder-session",
                JSON.stringify({ ...existing, activeRepertoireId: id }),
              );
            else localStorage.setItem("tempo-active-repertoire-white", id);
            setCurrentView("builder");
          }}
          onDeleteLocal={deleteLocalRepertoire}
          onRenameLocal={renameLocalRepertoire}
          onQueueChanged={refreshDatabaseQueue}
        />
      )}
      {currentView === "builder" && (
        <BuilderView
          theme={boardTheme}
          pieceSet={pieceSet}
          imported={importedRepertoires}
          settings={new Settings()}
        />
      )}
      {currentView === "games" && (
        <GamesView
          syncState={gameSync.state}
          onSync={() => void gameSync.sync(true)}
          onSettings={() => setCurrentView("settings")}
          onAnalyze={(game, gameCursor) => {
            const position = new Chess(game.startFen);
            const history = game.moves.slice(0, gameCursor).map((san) => {
              const move = position.move(san);
              return {
                san: move.san,
                uci: `${move.from}${move.to}${move.promotion ?? ""}`,
                fen: position.fen(),
              };
            });
            localStorage.setItem(
              "tempo-builder-session",
              JSON.stringify({
                version: 1,
                activeRepertoireId: game.repertoireId,
                activeRepertoireByColor: {},
                orientation: game.color,
                startingFen: game.startFen,
                history,
                cursor: history.length,
                branchStart: history.length,
              }),
            );
            setCurrentView("builder");
          }}
          theme={boardTheme}
          pieceSet={pieceSet}
        />
      )}
      {currentView === "progress" && (
        <ProgressView
          reviewed={reviewed}
          cardsLeft={cardsLeft}
          totalCards={practiceCards.length}
        />
      )}
      {currentView === "settings" && (
        <SettingsView
          theme={boardTheme}
          pieceSet={pieceSet}
          sound={soundOn}
          onTheme={changeBoardTheme}
          onPieces={changePieceSet}
          onSound={changeSound}
        />
      )}
      {showImport && (
        <ImportDialogBox
          onClose={() => setShowImport(false)}
          onImported={addImportedRepertoire}
          onDatabaseUpdated={refreshDatabaseQueue}
          onViewRepertoire={() => setCurrentView("repertoire")}
        />
      )}
      {showTree && (
        <TreeBrowser
          onClose={() => setShowTree(false)}
          theme={boardTheme}
          pieceSet={pieceSet}
        />
      )}
      {editorCard && (
        <CardEditor
          practiceCard={editorCard}
          boardTheme={boardTheme}
          pieceSet={pieceSet}
          onClose={() => setEditorCard(null)}
          onSave={(updated) => {
            setPracticeCards((current) =>
              current.map((item) => (item.id === updated.id ? updated : item)),
            );
            resetLine(updated);
            setSuggestShorter(false);
            activeQueueEntry.current = undefined;
            if (usesLocalApi()) void refreshDatabaseQueue();
          }}
        />
      )}
      {!boardWorkspace && <Footer />}
    </main>
  );
}
