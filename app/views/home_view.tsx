import { teachingResponseSchema } from "../domain/schemas";
import {
  readJsonResponse,
  readStoredValue,
  validRecords,
} from "../lib/validated-data";
import * as z from "zod";
import { localRepertoireSchema } from "../domain/schemas";
import { measureTempoOperation } from "../lib/performance";
import { isCurrentAttempt } from "../domain/attempt";
import { DataDiagnosticsNotice } from "../components/data-diagnostics-notice";
import { useState, useCallback, useEffect, useRef } from "react";
import { BoardTheme, PieceSet } from "../components/chessboard";
import { API_URL } from "../const";
import {
  preloadWorkspaces,
  invalidateWorkspaceData,
  readWorkspaceData,
} from "../lib/workspace-data";
import { runStudyTask } from "../lib/background-study";
import { ImportDialogBox } from "../ImportDialogBox";
import { moveSoundEnabled, playMoveSound } from "../lib/move-sound";
import { bundledRepertoires, demoCards } from "../samples";
import {
  View,
  BuilderSession,
  LocalRepertoire,
  AnalysisLine,
  CardId,
  asRepertoireId,
  asFenString,
  asTeachingCardKey,
  asTeachingMoveKey,
  asUciMove,
  asSanMove,
} from "../types";
import { canonicalFenKey } from "../utils/canonical-line";
import { IndexedPosition } from "../lib/position-similarity";
import { usesLocalApi, localDayKey } from "../utils/local";
import { trainedColor } from "../utils/cards";
import BuilderView from "./analysis_view";
import CardEditor from "./card_editor";
import EndgamesView from "./endgames_view";
import GamesView from "./games_view";
import InsightsView from "./insights_view";
import RepertoireView from "./repertoire_view";
import SettingsView from "./settings_view";
import TacticsView from "./tactics_view";
import { loadPositionAnnotation } from "../utils/position-annotations";
import { useGameSync } from "../hooks/use-game-sync";
import { useGameAnalysis } from "../hooks/use-game-analysis";
import { useRepertoireCoverageWorker } from "../hooks/use-repertoire-coverage";
import { Chess, Move, Square } from "chess.js";
import {
  useTrainingStore,
  selectHomeViewState,
  selectTrainingActions,
} from "../state/training-store";
import Footer from "../components/Footer";
import Navbar from "../components/Navbar";
import { BoardWorkspace } from "../components/board-workspace";
import BrandButton from "../components/buttons/BrandButton";
import SoundToggleButton from "../components/buttons/SoundToggleButton";
import SavedLocallyButton from "../components/buttons/SavedLocallyButton";
import DemoBanner from "../components/DemoBanner";
import TrainingView from "./training_view";
import { fetchAndInitializeQueue } from "./fetchAndInitializeQueue";
import { Settings } from "../utils/settings";
import { TreeBrowser } from "./tree_browser";
import { useShallow } from "zustand/react/shallow";
import { WorkspaceRefreshStatus } from "../components/workspace-refresh-status";
import { RepertoireIntegrityDialog } from "../components/repertoire-integrity-dialog";
import { repertoiresResponseSchema } from "../domain/schemas";
import { DebugErrorPanel } from "../components/debug-error-panel";
import { ServiceStatusPanel } from "../components/service-status-panel";
import { setActiveDebugWorkspace } from "../lib/debug-reporting";

async function responseErrorDetail(response: Response): Promise<string> {
  try {
    const payload = z
      .looseObject({ detail: z.string().optional(), retryable: z.boolean().optional() })
      .parse(await response.json());
    if (payload.detail)
      return `${payload.detail}${payload.retryable ? " Please retry." : ""}`;
  } catch {
    // Fall back to a stable HTTP message when the service did not return JSON.
  }
  return `Request failed (HTTP ${response.status}).`;
}

export default function Home() {
  const gameSync = useGameSync();
  useGameAnalysis();
  useRepertoireCoverageWorker();
  const [currentView, setCurrentView] = useState<View>("train");
  const [repairRepertoireId, setRepairRepertoireId] = useState<string>();
  const [pausedIntegrity, setPausedIntegrity] = useState<{ id: string; issueCount: number; blockedDue: number }>();
  const deferredRepairIds = useRef(new Set<string>());
  const [insightsTab, setInsightsTab] = useState<"training" | "games">(
    "training",
  );
  const [gamesFenFilter, setGamesFenFilter] = useState("");
  const [reviewPersistenceState, setReviewPersistenceState] = useState<
    "idle" | "saving" | "saveFailed" | "saved" | "refreshingQueue" | "queueFailed"
  >("idle");
  const changeWorkspace = useCallback((view: View) => {
    const finished = measureTempoOperation("view-switch");
    setRepairRepertoireId((currentRepairId) => {
      if (currentRepairId) deferredRepairIds.current.add(currentRepairId);
      return undefined;
    });
    if (view === "progress" || view === "statistics") {
      setInsightsTab(view === "statistics" ? "games" : "training");
      setCurrentView("insights");
    } else {
      setCurrentView(view);
    }
    requestAnimationFrame(() => requestAnimationFrame(finished));
  }, []);
  const branchPositions = useRef<IndexedPosition[]>([]);
  const {
    practiceCards,
    importedRepertoires,
    activeCardIndex,
    currentFenString,
    currentStepIndex: step,
    isViewLocked: isLocked,
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
    reviewSaveError,
  } = useTrainingStore(useShallow(selectHomeViewState));
  const {
    setPracticeCards,
    setImportedRepertoires,
    setActiveCardIndex,
    setCurrentFenString,
    setStep,
    setFeedback,
    setLastMove,
    setOpponentLastMove,
    setAttemptPhase,
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
    setReviewSaveError,
    setServiceError,
    initializeCardState,
    resetTrainingLine,
  } = useTrainingStore(useShallow(selectTrainingActions));
  const reviewPending = useRef(false);
  const replyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const completionTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const activeQueueEntry = useRef<number | undefined>(undefined);
  const card = practiceCards[activeCardIndex] ?? demoCards[0];
  const repertoireLine = card.moves;

  useEffect(() => {
    setActiveDebugWorkspace(currentView);
  }, [currentView]);

  const checkPendingIntegrity = useCallback(async (preferred?: string) => {
    if (!usesLocalApi()) return;
    try {
      const response = await fetch(`${API_URL}/api/repertoires`);
      const data = await response.json();
      const parsed = repertoiresResponseSchema.parse(data);
      const candidate = parsed.repertoires.find((item) =>
        item.integrity_status === "needs_repair" &&
        !deferredRepairIds.current.has(item.id),
      );
      const preferredCandidate = preferred ? parsed.repertoires.find((item) => item.id === preferred && item.integrity_status === "needs_repair") : undefined;
      setRepairRepertoireId(preferredCandidate?.id);
      const paused = preferredCandidate ?? candidate;
      const repairItems = parsed.repertoires.filter((item) => item.integrity_status === "needs_repair");
      setPausedIntegrity(paused ? {
        id: paused.id,
        issueCount: repairItems.reduce((total, item) => total + (item.integrity_issue_count ?? 0), 0),
        blockedDue: repairItems.reduce((total, item) => total + (item.blocked_due_count ?? 0), 0),
      } : undefined);
    } catch {
      // The normal workspace refresh path reports the service error.
    }
  }, []);

  const refreshDatabaseQueue = useCallback(async (advance = false) => {
    invalidateWorkspaceData();
    await fetchAndInitializeQueue(advance);
    await checkPendingIntegrity();
  }, [checkPendingIntegrity]);
  const refreshQueueOnly = useCallback(async () => {
    invalidateWorkspaceData();
    await fetchAndInitializeQueue(false);
  }, []);

  useEffect(() => {
    const onIntegrity = (event: Event) => {
      const detail = (event as CustomEvent<{ repertoireId?: string }>).detail;
      if (detail?.repertoireId) {
        deferredRepairIds.current.delete(detail.repertoireId);
        void checkPendingIntegrity(detail.repertoireId);
      } else void checkPendingIntegrity();
    };
    window.addEventListener("tempo:integrity", onIntegrity);
    queueMicrotask(() => void checkPendingIntegrity());
    return () => window.removeEventListener("tempo:integrity", onIntegrity);
  }, [checkPendingIntegrity]);

  useEffect(() => {
    const timer = window.setTimeout(() => void preloadWorkspaces(), 100);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
    if (usesLocalApi() && currentView === "train") {
      queueMicrotask(() => void refreshDatabaseQueue().catch(() => undefined));
      void readWorkspaceData(`${API_URL}/api/repertoire/lines`)
        .then(async (body) => {
          const lines = await runStudyTask<AnalysisLine[]>({
            kind: "transportLines",
            payload: body,
          });
          branchPositions.current = await runStudyTask<IndexedPosition[]>({
            kind: "index",
            lines,
          });
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
          new Set(
            (readStoredValue(
              localStorage,
              "tempo-seen-moves",
              z.array(z.string()),
            ) ?? []).map(asTeachingMoveKey),
          ),
        );
        setBoardTheme(
          (localStorage.getItem("tempo-board-theme") as BoardTheme) ?? "brown",
        );
        setPieceSet(
          (localStorage.getItem("tempo-piece-set") as PieceSet) ?? "cburnett",
        );
        setSoundOn(moveSoundEnabled());
        void refreshDatabaseQueue().catch(() => undefined);
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
        new Set(
          (readStoredValue(
            localStorage,
            "tempo-seen-moves",
            z.array(z.string()),
          ) ?? []).map(asTeachingMoveKey),
        ),
      );
      setFirstCleanPasses(
        new Set(
          readStoredValue(
            localStorage,
            "tempo-first-clean-passes",
            z.array(z.string()),
          ) ?? [],
        ),
      );
      const savedRepertoires = validRecords(
        localRepertoireSchema,
        readStoredValue(
          localStorage,
          "tempo-imported-repertoires",
          z.array(z.unknown()),
        ) ?? [],
        "saved repertoire",
      );
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
      void refreshDatabaseQueue().catch(() => undefined);
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
    clearTimeout(replyTimer.current);
    clearTimeout(completionTimer.current);
    resetTrainingLine(nextCard);
  }

  function tryMove(from: Square, to: Square) {
    const currentTurn =
      new Chess(currentFenString).turn() === "b" ? "black" : "white";
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
    setCurrentFenString(asFenString(position.fen()));
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
    setAttemptPhase("opponentReplyPending");
    const token = useTrainingStore.getState().attempt;
    replyTimer.current = setTimeout(() => {
      if (!isCurrentAttempt(useTrainingStore.getState().attempt, token)) return;
      const replyPosition = new Chess(position.fen());
      let reply: Move | null;
      try {
        reply = replyPosition.move(card.moves[opponentStep]);
      } catch {
        setAttemptPhase("guided", token);
        setServiceError(
          "This line needs repair: its opponent reply is illegal.",
        );
        return;
      }
      if (!reply) {
        setAttemptPhase(
          useTrainingStore.getState().isAttemptFailed ? "guided" : "playerTurn",
        );
        return;
      }
      const nextStep = opponentStep + 1;
      setCurrentFenString(asFenString(replyPosition.fen()));
      setLastMove([reply.from, reply.to]);
      setOpponentLastMove([reply.from, reply.to]);
      setStep(nextStep);
      setAttemptPhase(
        useTrainingStore.getState().isAttemptFailed ? "guided" : "playerTurn",
      );
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
    setReviewPersistenceState("saving");
    setReviewSaveError("");
    setAttemptPhase("feedbackPause");
    if (databaseQueue && card.backendId) {
      try {
        if (attemptFailed) {
          const saved = await fetch(
            `${API_URL}/api/queue/entries/${card.queueEntryId}/fail`,
            { method: "POST" },
          );
          if (!saved.ok) throw new Error(await responseErrorDetail(saved));
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
        if (!response.ok) throw new Error(await responseErrorDetail(response));
        setReviewPersistenceState("saved");
        setReviewed((count) => count + 1);
        setQueueNotice("");
        reviewPending.current = false;
        setReviewPersistenceState("refreshingQueue");
        try {
          await refreshDatabaseQueue(true);
          setReviewPersistenceState("idle");
        } catch {
          setReviewPersistenceState("queueFailed");
          setQueueNotice(
            "Result saved. The next card could not be loaded.",
          );
        }
        return;
      } catch (error) {
        reviewPending.current = false;
        setReviewPersistenceState("saveFailed");
        setReviewSaveError(
          error instanceof Error && error.message
            ? `The local database could not save this result. ${error.message}`
            : "The local database could not save this result. Please retry.",
        );
        setQueueNotice("");
        setAttemptPhase("feedbackPause");
        return;
      }
    }
    if (usesLocalApi()) {
      reviewPending.current = false;
      setReviewPersistenceState("saveFailed");
      setReviewSaveError("Connect to the local service before reviewing.");
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
    setReviewPersistenceState("idle");
  }

  function completeAttempt(finalFen: string) {
    setCurrentFenString(asFenString(finalFen));
    setAttemptPhase("feedbackPause");
    setFeedback("complete");
    const token = useTrainingStore.getState().attempt;
    clearTimeout(completionTimer.current);
    completionTimer.current = setTimeout(() => {
      if (isCurrentAttempt(useTrainingStore.getState().attempt, token))
        void rateCard(
          useTrainingStore.getState().isAttemptFailed ? "again" : "correct",
        );
    }, 750);
  }

  useEffect(
    () => () => {
      clearTimeout(replyTimer.current);
      clearTimeout(completionTimer.current);
    },
    [],
  );

  function markMoveSeen(moveStep: number) {
    const key = asTeachingMoveKey(`${card.backendId ?? card.id}:${card.revision ?? 1}:${moveStep}`);
    setSeenMoves((current) => {
      const next = new Set(current).add(key);
      localStorage.setItem(
        "tempo-seen-moves",
        JSON.stringify(Array.from(next)),
      );
      return next;
    });
  }

  const currentMoveKey = asTeachingMoveKey(`${card.backendId ?? card.id}:${card.revision ?? 1}:${step}`);
  const teachingCardKey = asTeachingCardKey(`${card.backendId ?? card.id}:${card.revision ?? 1}`);
  useEffect(() => {
    let active = true;
    if (!card.backendId || card.kind !== "opening") {
      queueMicrotask(() => setTeachingReadyCard(teachingCardKey));
      return;
    }
    void fetch(`${API_URL}/api/cards/${card.backendId}/teaching`)
      .then((response) => {
        if (!response.ok) throw new Error();
        return readJsonResponse(
          response,
          teachingResponseSchema,
          "teaching state",
        );
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
                      asTeachingMoveKey(`${card.backendId}:${state.revision}:${state.ply}`),
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
      if (
        card.kind !== "opening" ||
        card.firstCleanPassAt ||
        card.queueAttemptState === "reinforcement"
      ) {
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
    card.queueAttemptState,
    card.firstCleanPassAt,
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
    setShowHint(true);
    setFailureFen(currentFenString);
  }

  function resetCardAttempt() {
    resetLine();
    setAttemptFailed(true);
    setShowHint(true);
    setFailureFen(card.startingFen);
    setQueueNotice("Again recorded · restarted in guided mode");
  }

  function openReviewPosition(target: "analysis" | "builder" | "games") {
    if (target === "games") {
      setGamesFenFilter(canonicalFenKey(currentFenString));
      changeWorkspace("games");
      return;
    }
    const orientation = trainedColor(card);
    const repertoireId = card.repertoireId;
    const session: BuilderSession = {
      version: 1,
      activeRepertoireByColor: repertoireId
        ? { [orientation]: repertoireId }
        : {},
      activeRepertoireId: repertoireId,
      orientation,
      startingFen: currentFenString,
      history: [],
      cursor: 0,
      branchStart: target === "builder" ? 0 : null,
    };
    localStorage.setItem("tempo-builder-session", JSON.stringify(session));
    sessionStorage.setItem(
      "tempo-builder-tools",
      target === "analysis" ? "Compare" : "Repertoire",
    );
    changeWorkspace("builder");
  }

  return (
    <main
      className={`app-shell${boardWorkspace ? " board-workspace-shell" : ""}`}
    >
      <header className="topbar">
        <BrandButton setView={changeWorkspace} />
        <Navbar view={currentView} setView={changeWorkspace} />
        <div className="top-actions">
          <SoundToggleButton soundOn={soundOn} changeSound={changeSound} />
          <SavedLocallyButton setShowImport={setShowImport} />
          <ServiceStatusPanel />
        </div>
      </header>
      {!usesLocalApi() && <DemoBanner />}
      <WorkspaceRefreshStatus />
      <DebugErrorPanel />

      <BoardWorkspaceContainer enabled={boardWorkspace} view={currentView}>
      {currentView === "train" && (
        <>
            {pausedIntegrity && <div className="integrity-train-notice" role="status"><strong>{pausedIntegrity.blockedDue} opening card{pausedIntegrity.blockedDue === 1 ? "" : "s"} paused by repertoire repair.</strong><span>Unaffected openings and tactics remain available · {pausedIntegrity.issueCount} issue{pausedIntegrity.issueCount === 1 ? "" : "s"} remaining.</span><button onClick={() => { deferredRepairIds.current.delete(pausedIntegrity.id); setRepairRepertoireId(pausedIntegrity.id); }}>Resume repair</button></div>}
            <TrainingView
              dateLabel={new Date().toLocaleDateString()}
              serviceError={serviceError}
              refreshDatabaseQueue={refreshDatabaseQueue}
              cardsLeft={cardsLeft}
              card={card}
              boardTheme={boardTheme}
              pieceSet={pieceSet}
              rateCard={rateCard}
              reviewPersistenceState={reviewPersistenceState}
              reviewSaveError={reviewSaveError}
              retryQueueAfterReview={() => {
                setReviewPersistenceState("refreshingQueue");
                void refreshDatabaseQueue(true)
                  .then(() => setReviewPersistenceState("idle"))
                  .catch(() => setReviewPersistenceState("queueFailed"));
              }}
              handleAttemptFailure={handleAttemptFailure}
              resetCardAttempt={resetCardAttempt}
              setEditorCard={setEditorCard}
              onMove={tryMove}
              onOpenPosition={openReviewPosition}
              useSharedBoard
            />
        </>
      )}
      {currentView === "tactics" && (
        <>
            <TacticsView
              theme={boardTheme}
              pieceSet={pieceSet}
              onQueueChanged={() => void refreshDatabaseQueue()}
              useSharedBoard
            />
        </>
      )}
      {currentView === "endgames" && (
        <>
            <EndgamesView
              theme={boardTheme}
              pieceSet={pieceSet}
              onQueueChanged={() => void refreshDatabaseQueue()}
              useSharedBoard
            />
        </>
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
          onRepair={(id) => {
            deferredRepairIds.current.delete(id);
            setRepairRepertoireId(id);
          }}
          onResolveGap={(repertoireId, gap) => {
            const position = new Chess(gap.fen);
            const played = position.move({
              from: gap.move_uci.slice(0, 2) as Square,
              to: gap.move_uci.slice(2, 4) as Square,
              promotion: gap.move_uci[4],
            });
            const session: BuilderSession = {
              version: 1,
              activeRepertoireByColor: {
                [gap.trained_color]: asRepertoireId(repertoireId),
              },
              activeRepertoireId: asRepertoireId(repertoireId),
              orientation: gap.trained_color,
              startingFen: asFenString(gap.fen),
              history: [
                {
                  san: asSanMove(played.san),
                  uci: asUciMove(gap.move_uci),
                  fen: asFenString(position.fen()),
                },
              ],
              cursor: 1,
              branchStart: 0,
              sourceGapId: gap.gap_id,
            };
            localStorage.setItem("tempo-builder-session", JSON.stringify(session));
            setCurrentView("builder");
          }}
          onDeleteLocal={deleteLocalRepertoire}
          onRenameLocal={renameLocalRepertoire}
          onQueueChanged={refreshDatabaseQueue}
        />
      )}
      {currentView === "builder" && (
        <>
            <BuilderView
              theme={boardTheme}
              pieceSet={pieceSet}
              imported={importedRepertoires}
              settings={new Settings()}
              useSharedBoard
            />
        </>
      )}
      {currentView === "games" && (
        <>
            <GamesView
              initialFenFilter={gamesFenFilter}
              onClearFenFilter={() => setGamesFenFilter("")}
              syncState={gameSync.state}
              onSync={() => void gameSync.sync(true)}
              onRepair={() => void gameSync.sync(true, true)}
              onQueueUpdated={refreshQueueOnly}
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
              useSharedBoard
            />
        </>
      )}
      {currentView === "insights" && (
          <InsightsView
          initialTab={insightsTab}
          onTabChange={setInsightsTab}
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
      </BoardWorkspaceContainer>
      {showImport && (
        <ImportDialogBox
          onClose={() => setShowImport(false)}
          onImported={addImportedRepertoire}
          onDatabaseUpdated={refreshQueueOnly}
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
          onOpenBuilderForLineRemoval={(sessionFromEditor: BuilderSession) => {
            const existingSession = JSON.parse(
              localStorage.getItem("tempo-builder-session") ?? "null",
            ) as BuilderSession | null;
            const mergedSession: BuilderSession = {
              ...sessionFromEditor,
              activeRepertoireByColor:
                sessionFromEditor.activeRepertoireByColor,
              activeRepertoireId:
                sessionFromEditor.activeRepertoireId ??
                existingSession?.activeRepertoireId,
            };
            if (!mergedSession.activeRepertoireId) {
              const savedWhiteRepertoire = localStorage.getItem(
                "tempo-active-repertoire-white",
              );
              if (savedWhiteRepertoire)
                mergedSession.activeRepertoireId = asRepertoireId(
                  savedWhiteRepertoire,
                );
            }
            localStorage.setItem(
              "tempo-builder-session",
              JSON.stringify(mergedSession),
            );
            setEditorCard(null);
            setCurrentView("builder");
          }}
          onSave={(updated) => {
            setPracticeCards((current) =>
              current.map((item) => (item.id === updated.id ? updated : item)),
            );
            resetLine(updated);
            setSuggestShorter(false);
            activeQueueEntry.current = undefined;
            if (usesLocalApi()) {
              void refreshDatabaseQueue().catch(() => undefined);
              if (updated.repertoireId)
                window.dispatchEvent(new CustomEvent("tempo:integrity", { detail: { repertoireId: updated.repertoireId } }));
            }
          }}
        />
      )}
      {repairRepertoireId && (
        <RepertoireIntegrityDialog
          repertoireId={repairRepertoireId}
          theme={boardTheme}
          pieceSet={pieceSet}
          onClose={() => {
            deferredRepairIds.current.add(repairRepertoireId);
            setRepairRepertoireId(undefined);
          }}
          onClean={() => {
            deferredRepairIds.current.delete(repairRepertoireId);
            setRepairRepertoireId(undefined);
            setPausedIntegrity(undefined);
            void refreshDatabaseQueue();
          }}
        />
      )}
      <DataDiagnosticsNotice />
      {!boardWorkspace && <Footer />}
    </main>
  );
}

function BoardWorkspaceContainer({ enabled, view, children }: { enabled: boolean; view: string; children: React.ReactNode }) {
  return <BoardWorkspace view={view} enabled={enabled}>{children}</BoardWorkspace>;
}
