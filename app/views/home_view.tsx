import { TrainingRepairNotice } from "../components/TrainingRepairNotice";
import { useCommittedCallback } from "../hooks/use-committed-callback";
import { Button } from "../components/buttons/BaseButton";
import { teachingResponseSchema } from "../domain/schemas";
import { acceptPrefixSplitCommand, rejectPrefixSplitCommand } from "../lib/prefix-split-command";
import {
  readJsonResponse,
  readStoredValue,
  validRecords,
} from "../lib/validated-data";
import * as z from "zod";
import { localRepertoireSchema } from "../domain/schemas";
import { measureTempoOperation, measureTempoDragPhase } from "../lib/performance";
import { isCurrentAttempt } from "../domain/attempt";
import { DataDiagnosticsNotice } from "../components/data-diagnostics-notice";
import { useState, useCallback, useEffect, useRef } from "react";
import { BoardTheme, PieceSet } from "../components/chessboard";
import { API_URL } from "../const";
import { enqueueTeachingState, flushTeachingStates, pendingTeachingStates } from "../lib/teaching-state-outbox";
import {
  invalidateWorkspaceData,
  preloadView,
  readWorkspaceData,
} from "../lib/workspace-data";
import { runStudyTask } from "../lib/background-study";
import { ImportDialogBox } from "../ImportDialogBox";
import {
  AnalysisPasteDialog,
  type AnalysisPasteContext,
} from "../AnalysisPasteDialog";
import {
  cancelMoveSounds,
  moveSoundEnabled,
  playChessMoveSound,
  playMoveSound,
  prepareMoveSounds,
} from "../lib/move-sound";
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
import { buryQueuedCard } from "../domain/training-session";
import { rememberBrowserTrainingBurial, restoreBrowserTrainingBurials } from "../lib/browser-training-burials";
import { buryTrainingEntry, finishTrainingBurial, hasPendingTrainingBurial, pendingTrainingBurialEntry, recoverTrainingBurial } from "../lib/training-bury-command";
import BuilderView from "./analysis_view";
import ComparisonView from "./comparison_view";
import type { ComparisonBoard, ComparisonLaunch } from "../lib/comparison";
import CardEditor from "./card_editor";
import EndgamesView from "./endgames_view";
import GamesView from "./games_view";
import InsightsView from "./insights_view";
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
import { BoardWorkspace } from "../components/board/board-workspace";
import BrandButton from "../components/buttons/BrandButton";
import SoundToggleButton from "../components/buttons/SoundToggleButton";
import SavedLocallyButton from "../components/buttons/SavedLocallyButton";
import DemoBanner from "../components/DemoBanner";
import TrainingView from "./training_view";
import StudiesView from "./studies_view";
import { fetchAndInitializeQueue, invalidateTrainingQueueCache } from "./fetchAndInitializeQueue";
import {
  enqueuePendingReview,
  flushPendingReviews,
  pendingReviews,
} from "../lib/review-outbox";
import { describeOfflineQueue, markOfflineAttemptFailed, recordOfflineAttempt, requiresConnectedGrading } from "../lib/offline-training";
import { enqueueTrainingFailure, flushTrainingFailures } from "../lib/training-failure-outbox";
import { beginOpeningAttempt, completeOpeningAttempt, partialOpeningAttempt } from "../lib/opening-evidence-journal";
import { useOpeningEvidenceRecovery } from "../hooks/use-opening-evidence-recovery";
import type { AssistanceKind } from "../domain/opening-evidence";
import { Settings } from "../utils/settings";
import { TreeBrowser } from "./tree_browser";
import { useShallow } from "zustand/react/shallow";
import { WorkspaceRefreshStatus } from "../components/workspace-refresh-status";
import { RepertoireIntegrityDialog } from "../components/repertoire-integrity-dialog";
import { repertoiresResponseSchema } from "../domain/schemas";
import { NotificationCenter } from "../components/notification-center";
import { OfflineReviewConflicts } from "../components/offline-review-conflicts";
import { notifications, publishNotification, resolveNotification } from "../lib/notifications";
import { ServiceStatusPanel } from "../components/service-status-panel";
import {
  DiscoveriesTray,
  type DiscoveryItem,
} from "../components/discoveries-tray";
import { setActiveDebugWorkspace } from "../lib/debug-reporting";

export default function Home() {
  const gameSync = useGameSync();
  useEffect(() => prepareMoveSounds(), []);
  useEffect(() => {
    if (!usesLocalApi()) return;
    let lastSentAt = 0;
    const markActivity = () => {
      if (Date.now() - lastSentAt < 1_000) return;
      lastSentAt = Date.now();
      void fetch(`${API_URL}/api/system/browser-activity`, {
        method: "POST",
        headers: { "X-Tempo-Work-Class": "background" },
      }).catch(() => undefined);
    };
    window.addEventListener("pointerdown", markActivity, true);
    window.addEventListener("keydown", markActivity, true);
    return () => {
      window.removeEventListener("pointerdown", markActivity, true);
      window.removeEventListener("keydown", markActivity, true);
    };
  }, []);
  const [currentView, setCurrentView] = useState<View>("train");
  const [comparisonLaunch, setComparisonLaunch] = useState<ComparisonLaunch>();
  const [comparisonOpenError, setComparisonOpenError] = useState("");
  const [discoveryReturn, setDiscoveryReturn] = useState<{
    view: View;
    id: string;
  }>();
  const [discoveryOpenRequest, setDiscoveryOpenRequest] = useState<{
    id: string;
    token: number;
  }>();
  const [safeBreakCounter, setSafeBreakCounter] = useState(0);
  const [repairRepertoireId, setRepairRepertoireId] = useState<string>();
  const [pasteContext, setPasteContext] = useState<AnalysisPasteContext | null>(
    null,
  );
  const [pasteRevision, setPasteRevision] = useState(0);
  const [pausedIntegrity, setPausedIntegrity] = useState<{
    id: string;
    issueCount: number;
    blockedDue: number;
  }>();
  const deferredRepairIds = useRef(new Set<string>());
  const [insightsTab, setInsightsTab] = useState<"training" | "games">(
    "training",
  );
  const [gamesFenFilter, setGamesFenFilter] = useState("");
  const [gamesRepertoireFilter, setGamesRepertoireFilter] = useState("");
  const [reviewPersistenceState, setReviewPersistenceState] = useState<
    | "idle"
    | "saving"
    | "saveFailed"
    | "saved"
    | "refreshingQueue"
    | "queueFailed"
  >("idle");
  useEffect(() => {
    const showUpdate = () => publishNotification({
      severity: "warning", source: "phone update", key: "phone-update-ready",
      message: "Tempo update ready. Finish this attempt, then close and reopen Tempo while connected. Reviews saved on this phone remain available to sync.",
    });
    window.addEventListener("tempo:update-ready", showUpdate);
    return () => window.removeEventListener("tempo:update-ready", showUpdate);
  }, []);
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
  const branchPositions = useRef<IndexedPosition[] | null>(null);
  const [branchIndexLoadError, setBranchIndexLoadError] = useState("");
  const [branchIndexRetry, setBranchIndexRetry] = useState(0);
  const {
    practiceCards,
    importedRepertoires,
    activeCardIndex,
    currentFenString,
    currentStepIndex: step,
    isViewLocked: isLocked,
    attempt,
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
    offlineQueue,
    queueNotice,
    serviceError,
    reviewSaveError,
    pendingReviewError,
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
    setAssistedThisAttempt,
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
    setPendingReviewError,
    setServiceError,
    initializeCardState,
    resetTrainingLine,
  } = useTrainingStore(useShallow(selectTrainingActions));
  function showTrainingNotice(message: string, severity: "info" | "success" | "warning" | "error") {
    setQueueNotice(message);
    publishNotification({ severity, source: "training", message });
  }
  useEffect(() => {
    if (pendingReviewError) publishNotification({ severity: "error", source: "training review",
      key: "pending-review-error", message: `Could not save a previous training review. ${pendingReviewError}` });
    else {
      const previous = notifications().find((record) => record.key === "pending-review-error" && !record.resolvedAt);
      if (previous) resolveNotification(previous.id, { severity: "success", message: "Previous training review saved." });
    }
  }, [pendingReviewError]);
  useEffect(() => {
    if (serviceError) publishNotification({ severity: "error", source: "training service",
      key: "training-service-error", message: serviceError });
    else {
      const previous = notifications().find((record) => record.key === "training-service-error" && !record.resolvedAt);
      if (previous) resolveNotification(previous.id, { severity: "success", message: "Training service is available again." });
    }
  }, [serviceError]);
  const reviewPendingEntries = useRef(new Set<string>());
  const [pendingBurialEntryState, setPendingBurialEntryId] = useState<number | undefined>(() => usesLocalApi() ? pendingTrainingBurialEntry() : undefined);
  const pendingBurialEntryId = pendingBurialEntryState ?? (databaseQueue && !offlineQueue
    ? pendingTrainingBurialEntry(practiceCards.flatMap(card => card.queueEntryId ? [card.queueEntryId] : []))
    : undefined);
  const [burialRecoveryError, setBurialRecoveryError] = useState("");
  const burialRecoveryStarted = useRef(false);
  const reviewTransitionGeneration = useRef(0);
  const pendingOpponentReply = useRef<{
    timer: ReturnType<typeof setTimeout> | undefined;
    finish: (failed?: boolean) => void;
  } | undefined>(undefined);
  const completionTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const activeQueueEntry = useRef<number | undefined>(undefined);
  const card = practiceCards[activeCardIndex] ?? demoCards[0];
  const openingJournal = useCommittedCallback(() => beginOpeningAttempt(card, useTrainingStore.getState().attempt.attemptId,
    { offline: offlineQueue, studyTimezone: card.openingEvidenceStudyTimezone }));
  const observeAssistance = useCommittedCallback((moveOffset: number, kind: AssistanceKind) => {
    openingJournal()?.assistance(moveOffset, kind);
  });
  useEffect(() => {
    if (currentView === "train" && cardsLeft > 0) openingJournal();
  }, [currentView, cardsLeft, attempt.attemptId, openingJournal]);
  const queueReadiness = useTrainingStore(state => state.queueReadiness);
  useOpeningEvidenceRecovery(usesLocalApi(), queueReadiness === "ready",
    pendingBurialEntryId !== undefined || attempt.phase === "opponentReplyPending" ||
    reviewPersistenceState === "saving" || reviewPersistenceState === "refreshingQueue" ||
    reviewPersistenceState === "saveFailed");
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
      const candidate = parsed.repertoires.find(
        (item) =>
          item.integrity_status === "needs_repair" &&
          !deferredRepairIds.current.has(item.id),
      );
      const preferredCandidate = preferred
        ? parsed.repertoires.find(
            (item) =>
              item.id === preferred && item.integrity_status === "needs_repair",
          )
        : undefined;
      setRepairRepertoireId(preferredCandidate?.id);
      const paused = preferredCandidate ?? candidate;
      const repairItems = parsed.repertoires.filter(
        (item) => item.integrity_status === "needs_repair",
      );
      setPausedIntegrity(
        paused
          ? {
              id: paused.id,
              issueCount: repairItems.reduce(
                (total, item) => total + (item.integrity_issue_count ?? 0),
                0,
              ),
              blockedDue: repairItems.reduce(
                (total, item) => total + (item.blocked_due_count ?? 0),
                0,
              ),
            }
          : undefined,
      );
    } catch {
      // The normal workspace refresh path reports the service error.
    }
  }, []);

  const refreshDatabaseQueue = useCallback(
    async (advance = false) => {
      invalidateWorkspaceData();
      await fetchAndInitializeQueue(advance);
      await checkPendingIntegrity();
    },
    [checkPendingIntegrity],
  );
  useEffect(() => {
    if (!databaseQueue || offlineQueue || burialRecoveryStarted.current) return;
    burialRecoveryStarted.current = true;
    const entryId = pendingBurialEntryId;
    if (entryId === undefined) return;
    void (async () => {
      try {
        await recoverTrainingBurial(entryId);
        await refreshDatabaseQueue(true);
        finishTrainingBurial(entryId);
        setPendingBurialEntryId(undefined);
      } catch (error) {
        if (!hasPendingTrainingBurial(entryId)) setPendingBurialEntryId(undefined);
        setBurialRecoveryError(error instanceof Error ? error.message : "Could not resolve burial. Retry to check its result.");
      }
    })();
  }, [databaseQueue, offlineQueue, pendingBurialEntryId, practiceCards, refreshDatabaseQueue]);

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
    window.scrollTo({ top: 0, behavior: "auto" });
    if (usesLocalApi() && currentView === "train") {
      queueMicrotask(() => void refreshDatabaseQueue().catch(() => undefined));
    }
  }, [currentView, refreshDatabaseQueue]);

  useEffect(() => {
    if (!usesLocalApi() || currentView !== "train") return;
    let visibleRefreshCount = 0;
    const refreshWhenVisible = () => {
      if (document.visibilityState !== "visible") return;
      if (useTrainingStore.getState().serviceError.includes("no longer in today's queue")) return;
      visibleRefreshCount = 0;
      void fetchAndInitializeQueue(false, { preparePhoneQueue: true }).catch(() => undefined);
    };
    const refreshTimer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (useTrainingStore.getState().serviceError.includes("no longer in today's queue")) return;
      visibleRefreshCount += 1;
      void fetchAndInitializeQueue(false, {
        preparePhoneQueue: visibleRefreshCount % 2 === 0,
      }).catch(() => undefined);
    }, 30_000);
    window.addEventListener("online", refreshWhenVisible);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.clearInterval(refreshTimer);
      window.removeEventListener("online", refreshWhenVisible);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [currentView]);

  useEffect(() => {
    const timer = window.setTimeout(
      () => void preloadView("tactics").catch(() => undefined),
      100,
    );
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (usesLocalApi() && currentView === "train") {
      let active = true;
      branchPositions.current = null;
      void readWorkspaceData(`${API_URL}/api/repertoire/lines`)
        .then(async (body) => {
          const lines = await runStudyTask<AnalysisLine[]>({
            kind: "transportLines",
            payload: body,
          });
          const indexedPositions = await runStudyTask<IndexedPosition[]>({
            kind: "index",
            lines,
          });
          if (active) {
            branchPositions.current = indexedPositions;
            setBranchIndexLoadError("");
          }
        })
        .catch((failure) => {
          if (!active) return;
          branchPositions.current = null;
          setBranchIndexLoadError(
            failure instanceof Error
              ? failure.message
              : "Could not load repertoire lines.",
          );
        });
      return () => {
        active = false;
      };
    }
  }, [currentView, branchIndexRetry]);
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
            (
              readStoredValue(
                localStorage,
                "tempo-seen-moves",
                z.array(z.string()),
              ) ?? []
            ).map(asTeachingMoveKey),
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
          (
            readStoredValue(
              localStorage,
              "tempo-seen-moves",
              z.array(z.string()),
            ) ?? []
          ).map(asTeachingMoveKey),
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
      const savedQueue = JSON.parse(
        localStorage.getItem("tempo-daily-queue") ??
          JSON.stringify(
            Array.from({ length: 12 }, (_, index) => index % demoCards.length),
          ),
      ) as number[];
      const storedQueue = restoreBrowserTrainingBurials(savedQueue, loadedCards);
      localStorage.setItem("tempo-daily-queue", JSON.stringify(storedQueue));
      localStorage.setItem("tempo-cards-left", String(storedQueue.length));
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
    const queue = restoreBrowserTrainingBurials([...dailyQueue, ...addedIndexes], cards);
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
    const canceledReply = pendingOpponentReply.current;
    clearTimeout(canceledReply?.timer);
    canceledReply?.finish(true);
    clearTimeout(completionTimer.current);
    resetTrainingLine(nextCard);
  }

  function persistExplicitAttemptFailure() {
    if (!usesLocalApi() || card.kind === "defense" || !card.queueEntryId) return;
    if (offlineQueue) {
      void markOfflineAttemptFailed(card.queueEntryId)
        .then(() => showTrainingNotice("Guided attempt saved on phone.", "success"))
        .catch((error) => showTrainingNotice(`The phone could not save the guided attempt. ${String(error)}`, "error"));
      return;
    }
    try {
      enqueueTrainingFailure(card.queueEntryId);
      void flushTrainingFailures().catch((error) =>
        showTrainingNotice(`Could not save guided-attempt state. Tempo will retry. ${String(error)}`, "warning"));
    } catch (error) {
      showTrainingNotice(`Could not save guided-attempt state in this browser. Keep this page open. ${String(error)}`, "error");
    }
  }

  async function buryCurrentCard() {
    setBurialRecoveryError("");
    if (serviceError && !offlineQueue && pendingBurialEntryId === undefined)
      throw new Error("Refresh the live queue before burying this card.");
    if (offlineQueue) throw new Error("Burying needs the computer. Continue reviewing or reconnect.");
    if (databaseQueue) {
      const queueEntryId = pendingBurialEntryId ?? card.queueEntryId;
      if (!queueEntryId)
        throw new Error("The active queue entry is unavailable. Refresh the queue.");
      setPendingBurialEntryId(queueEntryId);
      try {
        await buryTrainingEntry(queueEntryId, pendingBurialEntryId !== undefined);
        await refreshDatabaseQueue(true);
        finishTrainingBurial(queueEntryId);
        setPendingBurialEntryId(undefined);
      } catch (error) {
        if (!hasPendingTrainingBurial(queueEntryId)) setPendingBurialEntryId(undefined);
        throw error;
      }
      setSafeBreakCounter((count) => count + 1);
      return;
    }
    const remainingQueue = buryQueuedCard(dailyQueue, activeCardIndex);
    if (remainingQueue === dailyQueue)
      throw new Error("The active card is no longer in today’s queue. Refresh training.");
    rememberBrowserTrainingBurial(card.id);
    setDailyQueue(remainingQueue);
    setActiveCardIndex(remainingQueue[0] ?? 0);
    setCardsLeft(remainingQueue.length);
    localStorage.setItem("tempo-daily-queue", JSON.stringify(remainingQueue));
    localStorage.setItem("tempo-cards-left", String(remainingQueue.length));
    resetLine(practiceCards[remainingQueue[0] ?? 0] ?? demoCards[0]);
    setSafeBreakCounter((count) => count + 1);
  }

  const tryMove = useCommittedCallback((from: Square, to: Square) => {
    if (pendingBurialEntryId !== undefined || (serviceError && !offlineQueue)) return;
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
      const promotion = position.get(from)?.type === "p" && /[18]$/.test(to) ? "q" : "";
      openingJournal()?.response(step, `${from}${to}${promotion}`, "illegal");
      setBoardAttempt((value) => value + 1);
      setFeedback("wrong");
      setShowHint(true);
      setAttemptFailed(true);
      setFailureFen(currentFenString);
      setQueueNotice(offlineQueue ? "Again · saving guided attempt on phone..." : "Again recorded · replay the guided move");
      persistExplicitAttemptFailure();
      return;
    }
    if (!move) return;
    const responseUci = `${move.from}${move.to}${move.promotion ?? ""}`;
    if (position.isCheckmate()) {
      openingJournal()?.response(step, responseUci, move.san === card.moves[step] ? "expected" : "wrong");
      setLastMove([move.from, move.to]);
      setOpponentLastMove(undefined);
      setStep(card.moves.length);
      completeAttempt(position.fen());
      return;
    }
    if (move.san !== card.moves[step]) {
      if (usesLocalApi() && !offlineQueue && branchPositions.current === null) {
        openingJournal()?.response(step, responseUci, "unverified");
        setBoardAttempt((value) => value + 1);
        setQueueNotice(
          "Cannot verify another repertoire move until lines load. Retry loading lines, then try again.",
        );
        return;
      }
      const alternateBranch = usesLocalApi() && branchPositions.current
        ? branchPositions.current.some(
            (other) =>
              other.repertoireId === card.repertoireId &&
              canonicalFenKey(other.fen) ===
                canonicalFenKey(currentFenString) &&
              other.nextUci === `${move.from}${move.to}${move.promotion ?? ""}`,
          )
        : (usesLocalApi() ? practiceCards : demoCards).some(
            (other) =>
              other.id !== card.id &&
              (!usesLocalApi() || ("repertoireId" in other && other.repertoireId === card.repertoireId)) &&
              other.startingFen === card.startingFen &&
              other.moves
                .slice(0, step)
                .every((san, index) => san === card.moves[index]) &&
              other.moves[step] === move?.san,
          );
      if (alternateBranch) {
        openingJournal()?.response(step, responseUci, "alternate");
        setBoardAttempt((value) => value + 1);
        setFeedback("branch");
        setShowHint(true);
        setQueueNotice(
          "Valid repertoire move · follow the arrow for today’s branch",
        );
        return;
      }
      openingJournal()?.response(step, responseUci, "wrong");
      setFeedback("wrong");
      setBoardAttempt((value) => value + 1);
      setShowHint(true);
      setAttemptFailed(true);
      setFailureFen(currentFenString);
      setQueueNotice(offlineQueue ? "Again · saving guided attempt on phone..." : "Again recorded · replay this move, then finish the line");
      persistExplicitAttemptFailure();
      return;
    }
    openingJournal()?.response(step, responseUci, "expected");
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
    const finishOpponentReply = measureTempoDragPhase("opponent-reply");
    const scheduledReply = {
      timer: undefined as ReturnType<typeof setTimeout> | undefined,
      finish: (failed = false) => {
        finishOpponentReply(failed);
        scheduledReply.timer = undefined;
        if (pendingOpponentReply.current === scheduledReply)
          pendingOpponentReply.current = undefined;
      },
    };
    pendingOpponentReply.current = scheduledReply;
    scheduledReply.timer = setTimeout(() => {
      if (!isCurrentAttempt(useTrainingStore.getState().attempt, token)) { scheduledReply.finish(true); return; }
      const replyPosition = new Chess(position.fen());
      let reply: Move | null;
      try {
        reply = replyPosition.move(card.moves[opponentStep]);
      } catch {
        scheduledReply.finish(true);
        setAttemptPhase("guided", token);
        setServiceError(
          "This line needs repair: its opponent reply is illegal.",
        );
        return;
      }
      if (!reply) {
        scheduledReply.finish(true);
        setAttemptPhase(
          useTrainingStore.getState().isAttemptFailed ? "guided" : "playerTurn",
        );
        return;
      }
      scheduledReply.finish();
      const nextStep = opponentStep + 1;
      setCurrentFenString(asFenString(replyPosition.fen()));
      setLastMove([reply.from, reply.to]);
      setOpponentLastMove([reply.from, reply.to]);
      setStep(nextStep);
      setAttemptPhase(
        useTrainingStore.getState().isAttemptFailed ? "guided" : "playerTurn",
      );
      setFeedback(nextStep >= card.moves.length ? "complete" : "ready");
      playChessMoveSound(reply, replyPosition.isCheck());
      if (nextStep >= card.moves.length) completeAttempt(replyPosition.fen());
    }, 420);
  });

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
    if (!value) cancelMoveSounds();
    if (value) playMoveSound({ force: true });
  }

  async function rateCard(
    outcome: "again" | "correct",
    options: { recordedAtCompletion?: boolean; retryPending?: boolean } = {},
  ) {
    if (pendingBurialEntryId !== undefined || (serviceError && !offlineQueue)) return;
    const entryKey = String(card.queueEntryId ?? card.id);
    if (reviewPendingEntries.current.has(entryKey) || cardsLeft === 0) return;
    const pendingBeforeReview = databaseQueue && !offlineQueue ? pendingReviews() : [];
    if (!options.retryPending && !options.recordedAtCompletion &&
        pendingBeforeReview.some((review) => review.queueEntryId === card.queueEntryId)) {
      setPendingReviewError("This card has a review waiting to save. Retry saving the review before grading it again.");
      return;
    }
    reviewPendingEntries.current.add(entryKey);
    const transitionGeneration = ++reviewTransitionGeneration.current;
    const { recordedAtCompletion = false, retryPending = false } = options;
    clearTimeout(completionTimer.current);
    const retryNeedsAdvance =
      retryPending &&
      pendingBeforeReview[0]?.queueEntryId === card.queueEntryId;
    setReviewPersistenceState("saving");
    setReviewSaveError("");
    if (!retryPending) setAttemptPhase("feedbackPause");
    if (offlineQueue && card.queueEntryId) {
      try {
        openingJournal();
        const attemptId = useTrainingStore.getState().attempt.attemptId;
        const completion = completeOpeningAttempt(attemptId);
        const saved = await recordOfflineAttempt(
          card.queueEntryId, outcome,
          attemptFailed || useTrainingStore.getState().assistedThisAttempt,
          undefined, { attemptId: attemptId ?? crypto.randomUUID(), completion },
        );
        const availableCards = saved.cards.filter((queuedCard) => !requiresConnectedGrading(queuedCard));
        const nextCards = await runStudyTask<typeof practiceCards>({
          kind: "queue", payload: {
            cards: availableCards, count: availableCards.length, local_date: saved.localDate,
          },
        });
        useTrainingStore.getState().hydrateLocalQueue(nextCards, true, nextCards.length);
        setReviewed((count) => count + 1);
        setReviewPersistenceState("idle");
        showTrainingNotice(describeOfflineQueue(saved), "success");
        setSafeBreakCounter((count) => count + 1);
      } catch (error) {
        setReviewPersistenceState("saveFailed");
        setReviewSaveError(`The phone could not save this review. ${String(error)}`);
      } finally {
        reviewPendingEntries.current.delete(entryKey);
      }
      return;
    }
    if (databaseQueue && card.backendId) {
      let advancedFromCache = false;
      try {
        if (!retryPending) {
          if (!card.queueEntryId)
            throw new Error(
              "The active queue entry is unavailable. Refresh the queue.",
            );
          if (!recordedAtCompletion) {
            openingJournal();
            const attemptId = useTrainingStore.getState().attempt.attemptId;
            const completion = completeOpeningAttempt(attemptId);
            enqueuePendingReview({
              attemptId, openingEvidenceCompletion: completion, completedAt: completion?.terminal?.ended_at,
              backendId: card.backendId,
              queueEntryId: card.queueEntryId,
              outcome,
              guided:
                attemptFailed ||
                useTrainingStore.getState().assistedThisAttempt,
            });
          }
          const finishNextCard = measureTempoDragPhase("next-card-readiness");
          advancedFromCache = useTrainingStore.getState().advanceCachedQueue();
          if (advancedFromCache) requestAnimationFrame(() => finishNextCard());
          else finishNextCard(true);
          setReviewed((count) => count + 1);
        }
        const finishReviewPersistence = measureTempoDragPhase("review-persistence");
        try { await flushPendingReviews(); finishReviewPersistence(); }
        catch (error) { finishReviewPersistence(true); throw error; }
        if (transitionGeneration === reviewTransitionGeneration.current)
          setReviewPersistenceState("saved");
        setQueueNotice("");
        reviewPendingEntries.current.delete(entryKey);
        if (
          transitionGeneration === reviewTransitionGeneration.current &&
          !advancedFromCache &&
          (!retryPending || retryNeedsAdvance)
        )
          setReviewPersistenceState("refreshingQueue");
        const finishQueueReadiness = measureTempoDragPhase("next-card-readiness");
        void refreshDatabaseQueue(
          !advancedFromCache && (!retryPending || retryNeedsAdvance),
        )
          .then(() => {
            requestAnimationFrame(() => finishQueueReadiness());
            if (transitionGeneration === reviewTransitionGeneration.current)
              setReviewPersistenceState("idle");
            setSafeBreakCounter((count) => count + 1);
          })
          .catch(() => {
            finishQueueReadiness(true);
            if (transitionGeneration === reviewTransitionGeneration.current)
              setReviewPersistenceState("queueFailed");
            showTrainingNotice("Result saved. The queue could not be refreshed.", "warning");
          });
        return;
      } catch (error) {
        reviewPendingEntries.current.delete(entryKey);
        setReviewPersistenceState("saveFailed");
        setReviewSaveError(
          error instanceof Error && error.message
            ? `The local database could not save this result. ${error.message}`
            : "The local database could not save this result. Please retry.",
        );
        setQueueNotice("");
        if (!advancedFromCache && !retryPending)
          setAttemptPhase("feedbackPause");
        return;
      }
    }
    if (usesLocalApi()) {
      reviewPendingEntries.current.delete(entryKey);
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
    reviewPendingEntries.current.delete(entryKey);
    setReviewPersistenceState("idle");
    setSafeBreakCounter((count) => count + 1);
  }

  function completeAttempt(finalFen: string) {
    setCurrentFenString(asFenString(finalFen));
    setAttemptPhase("feedbackPause");
    setFeedback("complete");
    openingJournal();
    const token = useTrainingStore.getState().attempt;
    const completedAt = new Date().toISOString();
    const completion = completeOpeningAttempt(token.attemptId, completedAt);
    const outcome = useTrainingStore.getState().isAttemptFailed
      ? "again"
      : "correct";
    let reviewRecordedAtCompletion = false;
    if (databaseQueue && !offlineQueue && card.backendId && card.queueEntryId) {
      try {
        enqueuePendingReview({
          attemptId: token.attemptId, completedAt, openingEvidenceCompletion: completion,
          backendId: card.backendId,
          queueEntryId: card.queueEntryId,
          outcome,
          guided:
            useTrainingStore.getState().isAttemptFailed ||
            useTrainingStore.getState().assistedThisAttempt,
        });
        reviewRecordedAtCompletion = true;
      } catch (error) {
        setReviewPersistenceState("saveFailed");
        setReviewSaveError(
          `The completed result could not be stored locally. ${String(error)}`,
        );
      }
    }
    clearTimeout(completionTimer.current);
    completionTimer.current = setTimeout(() => {
      if (isCurrentAttempt(useTrainingStore.getState().attempt, token))
        void rateCard(outcome, {
          recordedAtCompletion: reviewRecordedAtCompletion,
        });
    }, 750);
  }

  useEffect(
    () => () => {
      partialOpeningAttempt(useTrainingStore.getState().attempt.attemptId);
      const canceledReply = pendingOpponentReply.current;
      clearTimeout(canceledReply?.timer);
      canceledReply?.finish(true);
      clearTimeout(completionTimer.current);
    },
    [],
  );

  function markMoveSeen(moveStep: number) {
    const key = asTeachingMoveKey(
      `${card.backendId ?? card.id}:${card.revision ?? 1}:${moveStep}`,
    );
    setSeenMoves((current) => {
      const next = new Set(current).add(key);
      localStorage.setItem(
        "tempo-seen-moves",
        JSON.stringify(Array.from(next)),
      );
      return next;
    });
  }

  const currentMoveKey = asTeachingMoveKey(
    `${card.backendId ?? card.id}:${card.revision ?? 1}:${step}`,
  );
  const teachingCardKey = asTeachingCardKey(
    `${card.backendId ?? card.id}:${card.revision ?? 1}`,
  );
  useEffect(() => {
    let active = true;
    if (!card.backendId || card.kind !== "opening") {
      queueMicrotask(() => setTeachingReadyCard(teachingCardKey));
      return;
    }
    void flushTeachingStates().catch(() => undefined);
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
                  states.map((state) =>
                    asTeachingMoveKey(
                      `${card.backendId}:${state.revision}:${state.ply}`,
                    ),
                  ),
                  pendingTeachingStates()
                    .filter((state) => state.cardId === card.backendId)
                    .map((state) => asTeachingMoveKey(
                      `${state.cardId}:${state.revision}:${state.ply}`,
                    )),
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
        card.hasPriorStudyReview ||
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
      setAssistedThisAttempt(true);
      setSeenMoves((current) => {
        if (current.has(currentMoveKey)) return current;
        const next = new Set(current).add(currentMoveKey);
        localStorage.setItem("tempo-seen-moves", JSON.stringify([...next]));
        return next;
      });
      if (card.backendId) {
        enqueueTeachingState({ cardId: card.backendId, revision: card.revision ?? 1, ply: step });
        void flushTeachingStates().catch(() => undefined);
      }
    });
  }, [
    card.kind,
    card.queueAttemptState,
    card.firstCleanPassAt,
    card.hasPriorStudyReview,
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
    setAssistedThisAttempt,
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
    "studies",
    "games",
  ].includes(currentView);

  function handleAttemptFailure() {
    if (pendingBurialEntryId !== undefined || (serviceError && !offlineQueue)) return;
    openingJournal()?.manualFailure(step);
    if (!attemptFailed) {
      setAttemptFailed(true);
      setQueueNotice("Again recorded · finish with guidance");
    }
    setFeedback("wrong");
    setShowHint(true);
    setFailureFen(currentFenString);
    persistExplicitAttemptFailure();
  }

  function resetCardAttempt() {
    if (pendingBurialEntryId !== undefined || (serviceError && !offlineQueue)) return;
    resetLine();
    setAttemptFailed(true);
    setShowHint(true);
    setFailureFen(card.startingFen);
    setQueueNotice("Again recorded · restarted in guided mode");
    persistExplicitAttemptFailure();
  }

  function openReviewPosition(target: "analysis" | "builder" | "games" | "compare") {
    if (target === "compare") {
      try {
        const position = new Chess(card.startingFen);
        const history = card.moves.map((san) => {
          const move = position.move(san);
          return { uci: `${move.from}${move.to}${move.promotion ?? ""}`, san: move.san, fen: position.fen() };
        });
        const source: ComparisonBoard = {
          id: "source", label: card.title || "Training card", cardId: card.backendId ?? card.id,
          orientation: trainedColor(card), startingFen: card.startingFen,
          history, cursor: Math.min(step, history.length),
        };
        setComparisonOpenError("");
        setComparisonLaunch({
          source, sourceKey: `${source.cardId}:${card.revision ?? 1}:${source.cursor}:${currentFenString}`,
          repertoireId: card.repertoireId, returnView: "train",
        });
        changeWorkspace("compare");
      } catch (error) {
        setComparisonOpenError(`Cannot compare this card until its move route is repaired. ${String(error)}`);
      }
      return;
    }
    if (target === "games") {
      setGamesFenFilter(canonicalFenKey(currentFenString));
      setGamesRepertoireFilter("");
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

  function openDiscoveryInBuilder(
    discovery: DiscoveryItem,
    selectedMove: string | null,
  ) {
    const positionFen = discovery.decision_fen ?? discovery.fen;
    const routeStartFen = discovery.decision_start_fen ?? positionFen;
    const routeBoard = new Chess(routeStartFen);
    const routeHistory: BuilderSession["history"] = [];
    try {
      for (const moveUci of discovery.decision_route_uci ?? []) {
        const played = routeBoard.move({
          from: moveUci.slice(0, 2),
          to: moveUci.slice(2, 4),
          promotion: moveUci[4],
        });
        routeHistory.push({
          san: asSanMove(played.san),
          uci: asUciMove(moveUci),
          fen: asFenString(routeBoard.fen()),
        });
      }
      if (
        canonicalFenKey(routeBoard.fen()) !==
        canonicalFenKey(new Chess(positionFen).fen())
      )
        throw new Error("Route does not reach the decision");
    } catch {
      routeHistory.length = 0;
    }
    const repertoireId = asRepertoireId(discovery.repertoire_id);
    const session: BuilderSession = {
      version: 1,
      activeRepertoireByColor: { [discovery.trained_color]: repertoireId },
      activeRepertoireId: repertoireId,
      orientation: discovery.trained_color,
      startingFen: asFenString(
        routeHistory.length ? routeStartFen : positionFen,
      ),
      history: routeHistory,
      cursor: routeHistory.length,
      branchStart: routeHistory.length,
      selectedMoveUci: selectedMove ? asUciMove(selectedMove) : undefined,
    };
    localStorage.setItem("tempo-builder-session", JSON.stringify(session));
    sessionStorage.setItem("tempo-builder-tools", "Compare");
    setDiscoveryReturn({ view: currentView, id: discovery.id });
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
          <NotificationCenter />
          <ServiceStatusPanel />
          <DiscoveriesTray
            safeToOpen={
              !(["train", "tactics", "endgames", "builder"] as View[]).includes(
                currentView,
              )
            }
            interactionBlocked={Boolean(
              editorCard || showImport || pasteContext || repairRepertoireId,
            )}
            speculativePreparationPaused={
              (currentView === "train" && !["feedbackPause", "complete"].includes(attempt.phase)) ||
              (["tactics", "endgames", "builder"] as View[]).includes(currentView)
            }
            safeBreakCounter={safeBreakCounter}
            onOpenRepertoire={() => changeWorkspace("repertoire")}
            onOpenBuilder={openDiscoveryInBuilder}
            boardTheme={boardTheme}
            pieceSet={pieceSet}
            openRequest={discoveryOpenRequest}
            onQueueChanged={() => refreshDatabaseQueue()}
          />
        </div>
      </header>
      {!usesLocalApi() && <DemoBanner />}
      <WorkspaceRefreshStatus />
      {currentView === "builder" && discoveryReturn && (
        <div className="discovery-builder-return" role="status">
          <span>Investigating a discovery in Builder.</span>
          <Button
            onClick={() => {
              changeWorkspace(discoveryReturn.view);
              setDiscoveryOpenRequest((previous) => ({
                id: discoveryReturn.id,
                token: (previous?.token ?? 0) + 1,
              }));
              setDiscoveryReturn(undefined);
            }}
          >
            Return to discovery
          </Button>
          <Button
            onClick={() => {
              changeWorkspace(discoveryReturn.view);
              setDiscoveryReturn(undefined);
            }}
          >
            Back to work
          </Button>
        </div>
      )}

      <BoardWorkspaceContainer enabled={boardWorkspace} view={currentView}>
        {comparisonOpenError && currentView === "train" && <div className="ui-notice error" role="alert">{comparisonOpenError}</div>}
        {currentView === "train" && (
          <>
            <OfflineReviewConflicts />
            {offlineQueue && (
              <div className="ui-notice training-offline-notice" role="status">
                <strong>Offline queue</strong>
                <span>{queueNotice}</span>
                <Button onClick={() => void refreshDatabaseQueue().catch(() => undefined)}>
                  Retry sync
                </Button>
              </div>
            )}
            {pendingReviewError && !offlineQueue && (
              <div className="ui-notice error" role="alert">
                <span>Could not save a previous training review. Its card is paused until the save is resolved. {pendingReviewError}</span>
                <Button onClick={() => void refreshDatabaseQueue().catch(() => undefined)}>
                  Retry saving review
                </Button>
              </div>
            )}
            {branchIndexLoadError && (
              <div className="ui-notice error" role="alert">
                <span>
                  Repertoire lines unavailable: {branchIndexLoadError}
                </span>
                <Button
                  onClick={() => {
                    setBranchIndexLoadError("");
                    setBranchIndexRetry((attempt) => attempt + 1);
                  }}
                >
                  Retry loading lines
                </Button>
              </div>
            )}
            <TrainingView
              repairNotice={pausedIntegrity && <TrainingRepairNotice
                blockedDue={pausedIntegrity.blockedDue}
                issueCount={pausedIntegrity.issueCount}
                onResume={() => {
                  deferredRepairIds.current.delete(pausedIntegrity.id);
                  setRepairRepertoireId(pausedIntegrity.id);
                }}
              />}
              dateLabel={new Date().toLocaleDateString()}
              serviceError={serviceError}
              offlineQueue={offlineQueue}
              refreshDatabaseQueue={refreshDatabaseQueue}
              cardsLeft={cardsLeft}
              card={card}
              boardTheme={boardTheme}
              pieceSet={pieceSet}
              rateCard={rateCard}
              onBury={buryCurrentCard}
              burialPending={pendingBurialEntryId !== undefined}
              burialRecoveryError={burialRecoveryError}
              onDefenseGraded={async () => {
                await refreshDatabaseQueue(true);
                setReviewed((count) => count + 1);
                setSafeBreakCounter((count) => count + 1);
              }}
              reviewPersistenceState={reviewPersistenceState}
              reviewSaveError={reviewSaveError}
              retryReviewSave={() =>
                void rateCard("correct", { retryPending: true })
              }
              retryQueueAfterReview={() => {
                setReviewPersistenceState("refreshingQueue");
                void refreshDatabaseQueue(true)
                  .then(() => setReviewPersistenceState("idle"))
                  .catch(() => setReviewPersistenceState("queueFailed"));
              }}
              handleAttemptFailure={handleAttemptFailure}
              resetCardAttempt={resetCardAttempt}
              setEditorCard={setEditorCard}
              onAcceptPrefixSplit={async (prefixCard) => {
                if (!prefixCard.backendId)
                  throw new Error("The card is missing its local database ID. Refresh the queue.");
                if (pendingReviews().length) await flushPendingReviews();
                await acceptPrefixSplitCommand(prefixCard.backendId, prefixCard.revision ?? 1);
                invalidateTrainingQueueCache();
                setSuggestShorter(false);
                activeQueueEntry.current = undefined;
                setSafeBreakCounter((count) => count + 1);
                if (useTrainingStore.getState().advanceCachedQueue()) {
                  void refreshDatabaseQueue().catch(() => {
                    useTrainingStore.getState().setServiceError(
                      "Prefix split saved. Queue refresh failed; retry loading the queue.",
                    );
                  });
                } else {
                  try {
                    await refreshDatabaseQueue(true);
                  } catch {
                    useTrainingStore.getState().setServiceError(
                      "Prefix split saved. The next card could not be loaded. Retry loading the queue.",
                    );
                  }
                }
              }}
              onRejectPrefixSplit={async (prefixCard) => {
                if (!prefixCard.backendId)
                  throw new Error("The card is missing its local database ID. Refresh the queue.");
                await rejectPrefixSplitCommand(prefixCard.backendId, prefixCard.revision ?? 1);
                invalidateTrainingQueueCache();
                void fetchAndInitializeQueue().catch(() => undefined);
              }}
              onOpeningAssistance={observeAssistance}
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
            theme={boardTheme}
            pieceSet={pieceSet}
            imported={importedRepertoires}
            refreshRevision={pasteRevision}
            onImport={() => setShowImport(true)}
            onPaste={() => setPasteContext({})}
            onPasteGap={(_repertoireId, gap) =>
              setPasteContext({
                startingFen: gap.fen,
                sourceGapId: gap.gap_id,
              })
            }
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
              localStorage.setItem(
                "tempo-builder-session",
                JSON.stringify(session),
              );
              setCurrentView("builder");
            }}
            onDeleteLocal={deleteLocalRepertoire}
            onShowGamesAtPosition={(fen, repertoireId) => {
              setGamesFenFilter(fen);
              setGamesRepertoireFilter(repertoireId ?? "");
              setCurrentView("games");
            }}
            onRenameLocal={renameLocalRepertoire}
            onQueueChanged={refreshDatabaseQueue}
            onTrain={() => setCurrentView("train")}
          />
        )}
        {currentView === "builder" && (
          <>
            <BuilderView
              theme={boardTheme}
              pieceSet={pieceSet}
              imported={importedRepertoires}
              settings={new Settings()}
              onPasteAnalysis={setPasteContext}
              onCompare={(source, repertoireId) => {
                setComparisonLaunch({
                  source,
                  sourceKey: `${source.startingFen}:${source.cursor}:${source.history.map((move) => move.uci).join(" ")}`,
                  repertoireId,
                  returnView: "builder",
                });
                changeWorkspace("compare");
              }}
              useSharedBoard
            />
          </>
        )}
        {currentView === "compare" && comparisonLaunch && (
          <ComparisonView
            key={comparisonLaunch.sourceKey}
            launch={comparisonLaunch}
            theme={boardTheme}
            pieceSet={pieceSet}
            onReturn={() => changeWorkspace(comparisonLaunch.returnView)}
          />
        )}
        {currentView === "studies" && (
          <StudiesView boardTheme={boardTheme} pieceSet={pieceSet} />
        )}
        {currentView === "games" && (
          <>
            <GamesView
              initialFenFilter={gamesFenFilter}
              initialRepertoireFilter={gamesRepertoireFilter}
              onClearFenFilter={() => {
                setGamesFenFilter("");
                setGamesRepertoireFilter("");
              }}
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
          onClose={() => {
            setShowImport(false);
            setSafeBreakCounter((count) => count + 1);
          }}
          onImported={addImportedRepertoire}
          onDatabaseUpdated={refreshQueueOnly}
          onViewRepertoire={() => setCurrentView("repertoire")}
        />
      )}
      {pasteContext && (
        <AnalysisPasteDialog
          context={pasteContext}
          onClose={() => {
            setPasteContext(null);
            setSafeBreakCounter((count) => count + 1);
          }}
          onSaved={(affectedRepertoireIds, conflictingRepertoireIds) => {
            invalidateWorkspaceData();
            setPasteRevision((revision) => revision + 1);
            void refreshQueueOnly();
            setPasteContext(null);
            if (conflictingRepertoireIds.length)
              setRepairRepertoireId(conflictingRepertoireIds[0]);
            for (const repertoireId of affectedRepertoireIds) {
              window.dispatchEvent(
                new CustomEvent("tempo:integrity", {
                  detail: { repertoireId },
                }),
              );
            }
          }}
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
          onClose={() => {
            setEditorCard(null);
            setSafeBreakCounter((count) => count + 1);
          }}
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
                mergedSession.activeRepertoireId =
                  asRepertoireId(savedWhiteRepertoire);
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
                window.dispatchEvent(
                  new CustomEvent("tempo:integrity", {
                    detail: { repertoireId: updated.repertoireId },
                  }),
                );
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

function BoardWorkspaceContainer({
  enabled,
  view,
  children,
}: {
  enabled: boolean;
  view: string;
  children: React.ReactNode;
}) {
  return (
    <BoardWorkspace view={view} enabled={enabled}>
      {children}
    </BoardWorkspace>
  );
}
