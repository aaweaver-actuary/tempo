import type { LegacyWorkspace, LegacyWorkspaceController, LegacyWorkspaceSlots } from '../components/optional-workspace';
import StudySettingsView from './study-settings-view';
import { canOpenWorkspace } from '../lib/study-workspace-policy';
import { LEARNING_URL as API_URL, learningFetch as fetch, independentStudy, studyConnection } from "../lib/study-connection";
import { StudyStorageBoundary } from "../components/study-storage-boundary";
import { rememberTeachingFact, savedTeachingFacts, subscribeTeachingFacts } from "../../database/browser/study-intent-storage";
import { usePendingReviewRecovery } from "../hooks/use-pending-review-recovery";
import { useStudyPriority } from "../hooks/use-study-priority";
import { studyInteractionActive } from "../lib/study-priority";
import { useStudyOnlyMode } from "../hooks/use-study-only-mode";
import { verifySavedRepertoireMove } from "../lib/repertoire-move-verification";
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
import { isCurrentAttempt, positionStudyUnavailable } from "../domain/attempt";
import { DataDiagnosticsNotice } from "../components/data-diagnostics-notice";
import { Suspense, useState, useCallback, useEffect, useRef } from "react";
import type { BoardTheme, PieceSet } from "../components/chessboard";
import { enqueueTeachingState, flushTeachingStates, pendingTeachingStates } from "../lib/teaching-state-outbox";
import {
  invalidateWorkspaceData,
  preloadView,
} from "../lib/workspace-data";
import { runStudyTask } from "../lib/background-study";
import {
  cancelMoveSounds,
  moveSoundEnabled,
  playChessMoveSound,
  playMoveSound,
  prepareMoveSounds,
} from "../lib/move-sound";
import { bundledRepertoires, demoCards } from "../samples";
import { asFenString, asTeachingCardKey, asTeachingMoveKey, asUciMove, asSanMove,
  type View, type CardId } from "../domain/shared";
import type { LocalRepertoire } from "../domain/repertoire";
import { canonicalFenKey } from "../utils/canonical-line";
import { lookupRepertoireAlternative, type RepertoireAlternativeSnapshot } from "../lib/repertoire-alternative-index";
import { usesLocalApi, localDayKey } from "../utils/local";
import { trainedColor } from "../utils/cards";
import { buryQueuedCard } from "../domain/training-session";
import { rememberBrowserTrainingBurial, restoreBrowserTrainingBurials } from "../lib/browser-training-burials";
import { buryTrainingEntry, finishTrainingBurial, hasPendingTrainingBurial, pendingTrainingBurialEntry, recoverTrainingBurial } from "../lib/training-bury-command";
import TacticsView from "./tactics_view";
import { loadPositionAnnotation } from "../utils/position-annotations";
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
import DemoBanner from "../components/DemoBanner";
import TrainingView from "./training_view";
import { loadEligibleOfflineQueue, fetchAndInitializeQueue, invalidateTrainingQueueCache, rememberConfirmedQueueEntry, QueueProcessingError } from "./fetchAndInitializeQueue";
import { capturePreparedNext } from "../lib/prepared-review-continuation";
import type { PracticeCard } from "../domain/cards";
import {
  flushPendingReviews,
  pendingReviews,
  conflictedReviews,
} from "../lib/review-outbox";
import { describeOfflineQueue, markOfflineAttemptFailed, recordOfflineAttempt } from "../lib/offline-training";
import { enqueueTrainingFailure, flushTrainingFailures } from "../lib/training-failure-outbox";
import { beginOpeningAttempt, completeOpeningAttempt, partialOpeningAttempt, preparePositionOpeningAttempt } from "../lib/opening-evidence-journal";
import { useOpeningEvidenceRecovery } from "../hooks/use-opening-evidence-recovery";
import type { AssistanceKind } from "../domain/opening-evidence";
import { useShallow } from "zustand/react/shallow";
import { WorkspaceRefreshStatus } from "../components/workspace-refresh-status";
import { NotificationCenter } from "../components/notification-center";
import { OfflineReviewConflicts } from "../components/offline-review-conflicts";
import { notifications, publishNotification, resolveNotification } from "../lib/notifications";
import { setActiveDebugWorkspace, reportDebugError } from "../lib/debug-reporting";
import { useStudyReviewController } from "../features/study";

export default function Home({ optionalWorkspace }: { optionalWorkspace?: LegacyWorkspace } = {}) { return <Suspense fallback={<p role="status">Loading workspace…</p>}><StudyStorageBoundary><StudyComposition optionalWorkspace={optionalWorkspace} /></StudyStorageBoundary></Suspense>; }

function StudyComposition({ optionalWorkspace }: { optionalWorkspace?: LegacyWorkspace }) {
  const OptionalComposition = optionalWorkspace;
  const optionalController = useRef<LegacyWorkspaceController | null>(null);
  const studyOnlyMode = useStudyOnlyMode();
  useEffect(() => prepareMoveSounds(), []);
  const [currentView, setCurrentView] = useState<View>("train");
  const [browserOnline, setBrowserOnline] = useState(() => navigator.onLine);
  useEffect(() => {
    const updateConnection = () => setBrowserOnline(navigator.onLine);
    window.addEventListener("online", updateConnection);
    window.addEventListener("offline", updateConnection);
    return () => {
      window.removeEventListener("online", updateConnection);
      window.removeEventListener("offline", updateConnection);
    };
  }, []);
  const [safeBreakCounter, setSafeBreakCounter] = useState(0);
  useEffect(() => {
    const showUpdate = () => publishNotification({
      severity: "warning", source: "phone update", key: "phone-update-ready",
      message: "Tempo update ready. Finish this attempt, then close and reopen Tempo while connected. Reviews saved on this phone remain available to sync.",
    });
    window.addEventListener("tempo:update-ready", showUpdate);
    return () => window.removeEventListener("tempo:update-ready", showUpdate);
  }, []);
  const changeWorkspace = useCallback((view: View) => {
    if (!canOpenWorkspace(view, independentStudy)) view = "train";
    const finished = measureTempoOperation("view-switch");
    optionalController.current?.deferRepair();
    setCurrentView(optionalController.current?.prepareNavigation(view) ?? view);
    requestAnimationFrame(() => requestAnimationFrame(finished));
  }, []);
  const repertoireAlternativeSnapshot = useRef<RepertoireAlternativeSnapshot | null>(null);
  const repertoirePositionGeneration = useRef(0);
  const pendingMoveVerification = useRef<AbortController | null>(null);
  const [branchIndexLoadError, setBranchIndexLoadError] = useState("");
  const [branchIndexRetry, setBranchIndexRetry] = useState(0);
  const invalidateBranchEvidence = useCallback(() => {
    if (!usesLocalApi()) return;
    repertoirePositionGeneration.current += 1;
    repertoireAlternativeSnapshot.current = null;
    invalidateWorkspaceData();
    setBranchIndexRetry((generation) => generation + 1);
  }, []);
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
  const reviewController = useStudyReviewController({ onNextCard: () => setSafeBreakCounter(count => count + 1),
    checkIntegrity: async () => { await optionalController.current?.checkIntegrity(); }, acquireQueue: fetchAndInitializeQueue,
    rememberConfirmed: rememberConfirmedQueueEntry, isRetryable: error => !(error instanceof QueueProcessingError), attempt });
  const { phase: reviewPersistenceState, identity: reviewPersistenceIdentity,
    diagnosticIdentity: reviewSaveDiagnosticIdentity } = reviewController.snapshot;
  useStudyPriority(currentView === "train", studyOnlyMode, ["saving","pendingConfirmation","refreshingQueue"].includes(reviewPersistenceState));
  const { queueReconciliationError } = reviewController.snapshot;
  const { reconcilePreparedQueue } = reviewController;
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
  const showTrainingNotice = useCommittedCallback((message: string, severity: "info" | "success" | "warning" | "error") => {
    setQueueNotice(message);
    publishNotification({ severity, source: "training", message });
  });
  useEffect(() => {
    if (!pendingReviewError) return;
    // Exact attempt notices are owned by the outbox. Only failures outside it need a separate notice.
    try {
      if (pendingReviews().length || conflictedReviews().length) return;
    } catch { /* Surface unreadable storage below. */ }
    publishNotification({ severity: "warning", source: "training review", key: "pending-review-storage",
      message: "Saved reviews could not be read. Keep this browser's data and open Notifications for details.",
      details: { error: pendingReviewError } });
  }, [pendingReviewError]);
  useEffect(() => {
    if (serviceError) publishNotification({ severity: "error", source: "training service",
      key: "training-service-error", message: serviceError });
    else {
      const previous = notifications().find((record) => record.key === "training-service-error" && !record.resolvedAt);
      if (previous) resolveNotification(previous.id, { severity: "success", message: "Training service is available again." });
    }
  }, [serviceError]);
  useEffect(() => {
    const project = (persisted: ReturnType<typeof savedTeachingFacts>) => {
      const seen = persisted.filter(fact => fact.kind === "seen").map(fact => asTeachingMoveKey(fact.key));
      const clean = persisted.filter(fact => fact.kind === "first-clean").map(fact => fact.cardId);
      if (seen.length) setSeenMoves(current => seen.some(key => !current.has(key)) ? new Set([...current, ...seen]) : current);
      if (clean.length) setFirstCleanPasses(current => clean.some(key => !current.has(key)) ? new Set([...current, ...clean]) : current);
    };
    project(savedTeachingFacts());
    return subscribeTeachingFacts(project);
  }, [setSeenMoves, setFirstCleanPasses]);
  const [pendingBurialEntryState, setPendingBurialEntryId] = useState<number | undefined>(() => usesLocalApi() ? pendingTrainingBurialEntry() : undefined);
  const pendingBurialEntryId = pendingBurialEntryState ?? (databaseQueue && !offlineQueue
    ? pendingTrainingBurialEntry(practiceCards.flatMap(card => card.queueEntryId ? [card.queueEntryId] : []))
    : undefined);
  const [burialRecoveryError, setBurialRecoveryError] = useState("");
  const burialRecoveryStarted = useRef(false);
  const pendingOpponentReply = useRef<{
    timer: ReturnType<typeof setTimeout> | undefined;
    finish: (failed?: boolean) => void;
  } | undefined>(undefined);
  const completionTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const activeQueueEntry = useRef<number | undefined>(undefined);
  const card = practiceCards[activeCardIndex] ?? demoCards[0];
  const positionMetadataUnavailable = positionStudyUnavailable(card, offlineQueue || !browserOnline);
  const positionCaptureKey = card.positionPresentation
    ? `${card.positionPresentation.plan_id}:${card.positionPresentation.attempt_id}` : "";
  const [positionCapture, setPositionCapture] = useState<{ key: string; hold?: string }>();
  const positionUnavailable = positionMetadataUnavailable ?? (card.positionStudyMode === "position"
    ? positionCapture?.key !== positionCaptureKey ? "Preparing the issued position attempt…" : positionCapture.hold
    : undefined);
  useEffect(() => {
    if (card.positionStudyMode !== "position" || positionMetadataUnavailable) return;
    let active = true;
    const id = card.positionPresentation?.attempt_id;
    void preparePositionOpeningAttempt(card).then(hold => {
      if (!active) {
        if (useTrainingStore.getState().attempt.attemptId !== id) partialOpeningAttempt(id);
        return;
      }
      if (!hold) {
        const capture = beginOpeningAttempt(card, id, { studyTimezone: card.openingEvidenceStudyTimezone });
        if (!capture) hold = "The issued position attempt could not be restored. Refresh training.";
        else {
          const events = capture.snapshot().events;
          if (events.some(event => event.kind === "manual_failure" ||
              event.kind === "first_response" && ["wrong", "illegal"].includes(event.disposition ?? ""))) {
            setAttemptFailed(true);
            setShowHint(true);
            setFailureFen(useTrainingStore.getState().currentFenString);
          }
          if (events.some(event => event.kind === "assistance" || event.kind === "manual_failure"))
            setAssistedThisAttempt(true);
        }
      }
      setPositionCapture({ key: positionCaptureKey, hold });
    }).catch(error => {
      if (active) setPositionCapture({ key: positionCaptureKey,
        hold: `The issued position attempt could not be restored. ${String(error)}` });
    });
    return () => { active = false; };
  }, [card, positionCaptureKey, positionMetadataUnavailable, setAttemptFailed, setShowHint, setFailureFen, setAssistedThisAttempt]);
  const openingJournal = useCommittedCallback(() => card.kind !== "opening" || positionUnavailable ? undefined : beginOpeningAttempt(card, useTrainingStore.getState().attempt.attemptId,
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
  usePendingReviewRecovery(usesLocalApi() && !offlineQueue, queueReadiness === "ready",
    pendingBurialEntryId !== undefined || attempt.phase === "opponentReplyPending" ||
    reviewPersistenceState === "saving" || reviewPersistenceState === "refreshingQueue",
    () => {}, reviewController.captureConfirmation);
  const repertoireLine = card.moves;

  useEffect(() => {
    setActiveDebugWorkspace(currentView);
  }, [currentView]);

  const checkPendingIntegrity = useCallback(async (preferred?: string, passive = false) => {
    await optionalController.current?.checkIntegrity(preferred, passive);
  }, []);

  const refreshDatabaseQueue = useCallback(
    async (advance = false, replaySavedReviews = true, retryPreparation = false) => {
      invalidateWorkspaceData();
      const result = await fetchAndInitializeQueue(advance, { replaySavedReviews, retryPreparation });
      if (result !== "superseded") await checkPendingIntegrity();
    },
    [checkPendingIntegrity],
  );
  const finishPermanentDeletion = useCallback(async (cardId: string) => {
    invalidateBranchEvidence();
    const training = useTrainingStore.getState();
    const cachedCards = training.practiceCards.slice(training.activeCardIndex);
    const deletesActiveCard = cachedCards[0]?.backendId === cardId;
    const remainingCards = cachedCards.filter(item => item.backendId !== cardId);
    training.hydrateLocalQueue(remainingCards, deletesActiveCard,
      Math.max(0, training.cardsLeft - Number(remainingCards.length !== cachedCards.length)));
    if (deletesActiveCard) {
      activeQueueEntry.current = undefined;
      setSafeBreakCounter(count => count + 1);
    }
    invalidateTrainingQueueCache();
    try { await refreshDatabaseQueue(deletesActiveCard); }
    catch {
      useTrainingStore.getState().setServiceError("Card deleted. Training queue refresh failed; retry loading the queue.");
    }
  }, [refreshDatabaseQueue, invalidateBranchEvidence]);
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
  const refreshQueueAfterRepertoireChange = useCallback(async () => {
    invalidateBranchEvidence();
    await refreshQueueOnly();
  }, [invalidateBranchEvidence, refreshQueueOnly]);
  const refreshDatabaseQueueAfterRepertoireChange = useCallback(async () => {
    invalidateBranchEvidence();
    await refreshDatabaseQueue();
  }, [invalidateBranchEvidence, refreshDatabaseQueue]);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
    if (usesLocalApi() && currentView === "train") {
      queueMicrotask(() => void refreshDatabaseQueue(false, false).catch(() => undefined));
    }
  }, [currentView, refreshDatabaseQueue]);

  const refreshVisibleQueue = reviewController.refreshVisibleQueue;

  useEffect(() => {
    if (!usesLocalApi() || currentView !== "train") return;
    let visibleRefreshCount = 0;
    const refreshWhenVisible = () => {
      if (document.visibilityState !== "visible") return;
      visibleRefreshCount = 0;
      refreshVisibleQueue(true);
    };
    const refreshTimer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      visibleRefreshCount += 1;
      refreshVisibleQueue(visibleRefreshCount % 2 === 0);
    }, 30_000);
    window.addEventListener("online", refreshWhenVisible);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.clearInterval(refreshTimer);
      window.removeEventListener("online", refreshWhenVisible);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [currentView, refreshVisibleQueue]);

  useEffect(() => {
    const timer = window.setTimeout(
      () => { if (!independentStudy) void preloadView("tactics").catch(() => undefined); },
      100,
    );
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (usesLocalApi() && !independentStudy && currentView === "train" && studyOnlyMode === false) {
      let active = true;
      const generation = ++repertoirePositionGeneration.current;
      const controller = new AbortController();
      const isCurrent = () => active && generation === repertoirePositionGeneration.current;
      repertoireAlternativeSnapshot.current = null;
      invalidateWorkspaceData(`${API_URL}/api/repertoire/lines`);
      void fetch(`${API_URL}/api/repertoire/lines`, {
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
        headers: { "X-Tempo-Work-Class": "background" },
      }).then(async response => {
          if (!response.ok) throw new Error(`Could not load repertoire lines (HTTP ${response.status}).`);
          const payload = await response.text();
          if (!isCurrent()) return;
          const indexedPositions = await runStudyTask({
            kind: "repertoirePreparation", generation, payload,
          }, controller.signal, isCurrent);
          if (isCurrent()) {
            if (indexedPositions.generation !== generation)
              throw new Error("Repertoire lines have an obsolete generation. Retry loading lines.");
            repertoireAlternativeSnapshot.current = indexedPositions;
            setBranchIndexLoadError("");
          }
        })
        .catch((failure) => {
          if (!isCurrent()) return;
          repertoireAlternativeSnapshot.current = null;
          setBranchIndexLoadError(
            failure instanceof Error
              ? failure.message
              : "Could not load repertoire lines.",
          );
        });
      return () => {
        active = false;
        controller.abort();
        if (generation === repertoirePositionGeneration.current) {
          repertoirePositionGeneration.current += 1;
          repertoireAlternativeSnapshot.current = null;
        }
      };
    }
  }, [currentView, branchIndexRetry, studyOnlyMode]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (usesLocalApi()) {
        setPracticeCards([]);
        setDailyQueue([]);
        setImportedRepertoires([]);
        setSeenMoves(current => new Set([
          ...current,
          ...(readStoredValue(localStorage, "tempo-seen-moves", z.array(z.string())) ?? []).map(asTeachingMoveKey),
        ]));
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
      setSeenMoves(current => new Set([
        ...current,
        ...(readStoredValue(localStorage, "tempo-seen-moves", z.array(z.string())) ?? []).map(asTeachingMoveKey),
      ]));
      setFirstCleanPasses(current => new Set([
        ...current,
        ...(readStoredValue(localStorage, "tempo-first-clean-passes", z.array(z.string())) ?? []),
      ]));
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

  async function persistExplicitAttemptFailure() {
    if (card.positionStudyMode === "position") return true;
    if (!usesLocalApi() || card.kind === "defense" || !card.queueEntryId) return true;
    if (offlineQueue) {
      try { await markOfflineAttemptFailed(card.queueEntryId); showTrainingNotice("Guided attempt saved on phone.", "success"); return true; }
      catch (error) { showTrainingNotice(`The phone could not save the guided attempt. ${String(error)}`, "error"); return false; }
    }
    try {
      await enqueueTrainingFailure(card.queueEntryId, String(card.backendId ?? card.id), card.revision);
      void flushTrainingFailures().catch((error) =>
        showTrainingNotice(`Could not save guided-attempt state. Tempo will retry. ${String(error)}`, "warning"));
      return true;
    } catch (error) {
      showTrainingNotice(`Could not save guided-attempt state in this browser. Keep this page open. ${String(error)}`, "error");
      return false;
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
        await buryTrainingEntry(queueEntryId, pendingBurialEntryId !== undefined, {
          card_id: String(card.backendId ?? card.id), expected_revision: card.revision,
        });
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

  useEffect(() => () => {
    pendingMoveVerification.current?.abort();
    pendingMoveVerification.current = null;
  }, [currentView, currentFenString, attempt, studyOnlyMode]);

  const tryMove = useCommittedCallback(async (from: Square, to: Square) => {
    if (pendingMoveVerification.current) {
      setBoardAttempt((value) => value + 1);
      return;
    }
    if (positionUnavailable || pendingBurialEntryId !== undefined || (serviceError && !offlineQueue)) return;
    const currentTurn =
      new Chess(currentFenString).turn() === "b" ? "black" : "white";
    if (
      isLocked ||
      step >= card.moves.length ||
      currentTurn !== trainedColor(card) ||
      (card.kind === "endgame" || card.kind === "defense")
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
    if (position.isCheckmate() && (card.positionStudyMode !== "position" || move.san === card.moves[step])) {
      openingJournal()?.response(step, responseUci, move.san === card.moves[step] ? "expected" : "wrong");
      setLastMove([move.from, move.to]);
      setOpponentLastMove(undefined);
      setStep(card.moves.length);
      completeAttempt(position.fen());
      return;
    }
    if (move.san !== card.moves[step]) {
      let indexedAlternative = card.kind !== "opening" ? false : usesLocalApi()
        ? lookupRepertoireAlternative(repertoireAlternativeSnapshot.current, repertoirePositionGeneration.current,
            card.repertoireId, currentFenString, responseUci)
        : undefined;
      if (usesLocalApi() && !offlineQueue && indexedAlternative === undefined &&
          (independentStudy || studyOnlyMode === true || studyInteractionActive()) && card.repertoireId && card.kind === "opening") {
        const verification = new AbortController();
        pendingMoveVerification.current = verification;
        const capturedAttempt = useTrainingStore.getState().attempt;
        const isCurrent = () => !verification.signal.aborted
          && pendingMoveVerification.current === verification
          && useTrainingStore.getState().attempt === capturedAttempt
          && useTrainingStore.getState().currentFenString === currentFenString;
        setBoardAttempt((value) => value + 1);
        setQueueNotice("Checking this move against saved repertoire lines...");
        try {
          if (independentStudy) {
            if (card.kind !== "opening") indexedAlternative = false;
            else {
              const parameters = new URLSearchParams({ revision: String(card.revision), fen: currentFenString, move_uci: responseUci });
              const response = await fetch(`${API_URL}/api/cards/${encodeURIComponent(String(card.backendId))}/move-membership?${parameters}`, { signal: verification.signal });
              const result = z.strictObject({ card_id: z.string(), revision: z.number().int(), move_uci: z.string(), verified: z.boolean() }).parse(await response.json());
              if (!response.ok || result.card_id !== card.backendId || result.revision !== card.revision || result.move_uci !== responseUci) throw new Error("Accepted move evidence is unavailable. Retry.");
              indexedAlternative = result.verified;
            }
          } else indexedAlternative = await verifySavedRepertoireMove(String(card.repertoireId), currentFenString, responseUci, verification.signal);
          if (!isCurrent()) return;
        } catch (error) {
          if (!isCurrent()) return;
          openingJournal()?.response(step, responseUci, "unverified");
          setQueueNotice(`Could not verify this move. ${error instanceof Error ? error.message : "Reload and retry."}`);
          return;
        } finally {
          if (pendingMoveVerification.current === verification) pendingMoveVerification.current = null;
        }
      }
      if (usesLocalApi() && !offlineQueue && indexedAlternative === undefined) {
        openingJournal()?.response(step, responseUci, "unverified");
        setBoardAttempt((value) => value + 1);
        setQueueNotice(
          "Cannot verify another repertoire move until lines load. Retry loading lines, then try again.",
        );
        return;
      }
      const alternateBranch = usesLocalApi() && indexedAlternative !== undefined
        ? indexedAlternative
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
      playChessMoveSound(reply, replyPosition.isCheck(), "opponent");
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
    options: { recordedAtCompletion?: boolean; recordedAttemptId?: string; retryPending?: boolean } = {},
  ) {
    // Queue recovery can fail while this exact completed result is retained.
    // Retrying its frozen delivery must remain possible without enabling grading.
    if ((positionUnavailable && !options.retryPending && !options.recordedAtCompletion) || pendingBurialEntryId !== undefined || (serviceError && !offlineQueue && !options.retryPending)) return;
    const entryKey = String(card.queueEntryId ?? card.id);
    if (cardsLeft === 0) return;
    if (databaseQueue && !offlineQueue && !options.retryPending && !options.recordedAtCompletion &&
        reviewController.hasPendingForCard(card)) {
      setPendingReviewError("This card has a review waiting to save. Retry saving the review before grading it again.");
      return;
    }
    const transitionGeneration = reviewController.beginReview(entryKey);
    if (transitionGeneration === undefined) return;
    const displayedAttemptGeneration = useTrainingStore.getState().attempt.generation;
    const { recordedAtCompletion = false, retryPending = false } = options;
    clearTimeout(completionTimer.current);
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
        const nextCards = await loadEligibleOfflineQueue(saved);
        useTrainingStore.getState().hydrateLocalQueue(nextCards, true, nextCards.length);
        setReviewed((count) => count + 1);
        reviewController.finishLocalReview(entryKey, "saved");
        showTrainingNotice(describeOfflineQueue(saved), "success");
        setSafeBreakCounter((count) => count + 1);
      } catch (error) {
        reviewController.finishLocalReview(entryKey, "failed");
        setReviewSaveError(`The phone could not save this review. ${String(error)}`);
      } finally {
        reviewController.finishReview(entryKey);
      }
      return;
    }
    if (databaseQueue && card.backendId) {
      try {
        await reviewController.submit({
          card, attemptId: options.recordedAttemptId ?? useTrainingStore.getState().attempt.attemptId,
          attemptGeneration: displayedAttemptGeneration, transitionGeneration, retryPending, recordedAtCompletion,
          prepare: async () => {
            if (useTrainingStore.getState().isAttemptFailed && !await persistExplicitAttemptFailure()) throw new Error("The guided failure must be saved before completing this review.");
            openingJournal();
            const attemptId = useTrainingStore.getState().attempt.attemptId;
            const completion = completeOpeningAttempt(attemptId);
            return { attemptId, openingEvidenceCompletion: completion, completedAt: completion?.terminal?.ended_at,
              positionStudy: card.positionStudyMode === "position" ? true : undefined,
              backendId: card.backendId!, queueEntryId: card.queueEntryId!, expectedRevision: card.revision,
              preparedNext: capturePreparedNext(), outcome,
              guided: attemptFailed || useTrainingStore.getState().assistedThisAttempt };
          },
          onEvent: event => {
            switch (event.type) {
              case "error": setReviewSaveError(event.message); break;
              case "pending-error": setPendingReviewError(event.message); break;
              case "reviewed": setReviewed(count => count + 1); break;
              case "clear-queue-notice": setQueueNotice(""); break;
              case "feedback-pause": setAttemptPhase("feedbackPause"); break;
            }
          },
        });
      } finally { reviewController.finishReview(entryKey); }
      return;
    }
    if (usesLocalApi()) {
      reviewController.finishReview(entryKey);
      reviewController.markRetentionFailed();
      setReviewSaveError("Connect to the local service before reviewing.");
      setServiceError("Connect to the local service before reviewing.");
      return;
    }
    const firstClean = outcome === "correct" && !firstCleanPasses.has(card.id);
    if (firstClean) await rememberTeachingFact({ key: `first-clean:${card.id}`, cardId: String(card.id), revision: card.revision ?? 1, kind: "first-clean" });
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
    reviewController.finishLocalReview(entryKey, "saved");
    setSafeBreakCounter((count) => count + 1);
  }

  async function completeAttempt(finalFen: string) {
    if (positionStudyUnavailable(card, offlineQueue || !navigator.onLine)) return;
    setCurrentFenString(asFenString(finalFen));
    setAttemptPhase("feedbackPause");
    setFeedback("complete");
    openingJournal();
    const token = useTrainingStore.getState().attempt;
    const currentPresentation = reviewController.capturePresentation();
    const completionCurrent = () => currentPresentation() && isCurrentAttempt(useTrainingStore.getState().attempt, token);
    const completedAt = new Date().toISOString();
    const completion = completeOpeningAttempt(token.attemptId, completedAt);
    const outcome = useTrainingStore.getState().isAttemptFailed
      ? "again"
      : "correct";
    let reviewRecordedAtCompletion = false;
    let reviewRecordedAttemptId: string | undefined;
    if (databaseQueue && !offlineQueue && card.backendId && card.queueEntryId) {
      try {
        if (outcome === "again" && !await persistExplicitAttemptFailure()) throw new Error("The guided failure must be saved before completing this review.");
        if (!isCurrentAttempt(useTrainingStore.getState().attempt, token)) return;
        reviewRecordedAttemptId = await reviewController.retain({
          attemptId: token.attemptId, completedAt, openingEvidenceCompletion: completion,
          positionStudy: card.positionStudyMode === "position" ? true : undefined,
          backendId: card.backendId,
          queueEntryId: card.queueEntryId,
          expectedRevision: card.revision,
          preparedNext: capturePreparedNext(),
          outcome,
          guided:
            useTrainingStore.getState().isAttemptFailed ||
            useTrainingStore.getState().assistedThisAttempt,
        });
        if (!completionCurrent()) return;
        reviewRecordedAtCompletion = true;
      } catch (error) {
        if (!completionCurrent()) return;
        reviewController.markRetentionFailed();
        setReviewSaveError(
          `The completed result could not be stored locally. ${String(error)}`,
        );
        return;
      }
    }
    clearTimeout(completionTimer.current);
    completionTimer.current = setTimeout(() => {
      if (completionCurrent())
        void rateCard(outcome, {
          recordedAtCompletion: reviewRecordedAtCompletion,
          recordedAttemptId: reviewRecordedAttemptId,
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
    if (card.positionStudyMode === "position") return;
    const key = asTeachingMoveKey(
      `${card.backendId ?? card.id}:${card.revision ?? 1}:${moveStep}`,
    );
    void rememberTeachingFact({ key, cardId: String(card.backendId ?? card.id), revision: card.revision ?? 1, ply: moveStep, kind: "seen" })
      .then(() => setSeenMoves(current => new Set(current).add(key)))
      .catch(error => showTrainingNotice(`Teaching progress could not be saved. Keep this page open. ${String(error)}`, "error"));
  }

  const currentMoveKey = asTeachingMoveKey(
    `${card.backendId ?? card.id}:${card.revision ?? 1}:${step}`,
  );
  const teachingCardKey = asTeachingCardKey(
    `${card.backendId ?? card.id}:${card.revision ?? 1}`,
  );
  useEffect(() => {
    let active = true;
    if (!card.backendId || card.kind !== "opening" || card.positionStudyMode === "position") {
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
              [...current].concat(
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
    card.positionStudyMode,
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
        card.positionStudyMode === "position" ||
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
      const state = { cardId: String(card.backendId ?? card.id), revision: card.revision ?? 1, ply: step };
      void (card.backendId ? enqueueTeachingState(state) : rememberTeachingFact({ key: currentMoveKey, ...state, kind: "seen" }))
        .then(() => { setSeenMoves(current => new Set(current).add(currentMoveKey)); if (card.backendId) void flushTeachingStates().catch(() => undefined); })
        .catch(error => showTrainingNotice(`Teaching progress could not be saved. Keep this page open. ${String(error)}`, "error"));
    });
  }, [
    card.kind,
    card.queueAttemptState,
    card.positionStudyMode,
    card.firstCleanPassAt,
    card.hasPriorStudyReview,
    card.backendId,
    card.revision,
    currentMoveKey,
    card.id,
    showTrainingNotice,
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
    if (independentStudy || !attemptFailed || !repertoireId || currentFenString !== failureFen) {
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
    "games",
  ].includes(currentView);

  function handleAttemptFailure() {
    if (positionUnavailable || pendingBurialEntryId !== undefined || (serviceError && !offlineQueue)) return;
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
    if (positionUnavailable || pendingBurialEntryId !== undefined || (serviceError && !offlineQueue)) return;
    resetLine();
    setAttemptFailed(true);
    setShowHint(true);
    setFailureFen(card.startingFen);
    setQueueNotice("Again recorded · restarted in guided mode");
    persistExplicitAttemptFailure();
  }

  const renderWorkspace = (slots: LegacyWorkspaceSlots = {}) => (
    <main
      className={`app-shell${boardWorkspace ? " board-workspace-shell" : ""}`}
    >
      <header className="topbar">
        <BrandButton setView={changeWorkspace} />
        <Navbar view={currentView} setView={changeWorkspace} />
        <div className="top-actions">
          <SoundToggleButton soundOn={soundOn} changeSound={changeSound} />
          <NotificationCenter />
          {slots.topActions}
        </div>
      </header>
      {!usesLocalApi() && <DemoBanner />}
      <WorkspaceRefreshStatus />
      {slots.banner}

      <BoardWorkspaceContainer enabled={boardWorkspace} view={currentView}>
        {slots.trainingNotice}
        {currentView === "train" && (
          <>
            {slots.trainingActions}
            {studyOnlyMode === true && <div className="ui-notice" role="status">Study-only mode pauses optional analysis and imports handled by the task scheduler. Review saves and the daily queue remain available. Games collection is controlled separately.</div>}
            <OfflineReviewConflicts />
            {queueReconciliationError && <div role="status">
              {queueReconciliationError}{" "}
              <Button onClick={() => void reconcilePreparedQueue()}>Retry loading the queue</Button>
            </div>}
            {offlineQueue && (
              <div className="ui-notice training-offline-notice" role="status">
                <strong>Offline queue</strong>
                <span>{queueNotice}</span>
                <Button onClick={() => void refreshDatabaseQueue(false, true, true).catch(() => undefined)}>
                  Retry sync
                </Button>
              </div>
            )}
            {pendingReviewError && !offlineQueue && (
              <div className="ui-notice" role="status">
                <span>A previous result needs confirmation or attention. Open Notifications for details.</span>
                <Button onClick={() => void (async () => {
                  const currentPresentation = reviewController.capturePresentation();
                  const current = await reviewController.checkSavedReviews();
                  if (!current || !currentPresentation()) return;
                  if (!reviewPersistenceIdentity) await refreshDatabaseQueue(false, false, true);
                })().catch(() => undefined)}>
                  Check saved reviews
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
                    invalidateBranchEvidence();
                  }}
                >
                  Retry loading lines
                </Button>
              </div>
            )}
            <TrainingView
              repairNotice={slots.repairNotice}
              dateLabel={new Date().toLocaleDateString(undefined, independentStudy ? { timeZone: studyConnection()?.bootstrap?.timezone } : undefined)}
              serviceError={serviceError}
              offlineQueue={offlineQueue}
              positionStudyUnavailableReason={positionUnavailable}
              refreshDatabaseQueue={refreshDatabaseQueue}
              cardsLeft={cardsLeft}
              card={card}
              boardTheme={boardTheme}
              pieceSet={pieceSet}
              rateCard={rateCard}
              onBury={buryCurrentCard}
              burialPending={pendingBurialEntryId !== undefined}
              burialRecoveryError={burialRecoveryError}
              reviewPersistenceState={reviewPersistenceState}
              reviewSaveError={reviewSaveError}
              reviewPersistenceIdentity={reviewSaveDiagnosticIdentity ?? reviewPersistenceIdentity}
              retryReviewSave={() =>
                void rateCard("correct", { retryPending: true })
              }
              retryQueueAfterReview={reviewController.retryQueueAfterReview}
              handleAttemptFailure={handleAttemptFailure}
              resetCardAttempt={resetCardAttempt}
              setEditorCard={slots.onEditCard}
              onAcceptPrefixSplit={async (prefixCard) => {
                if (!prefixCard.backendId)
                  throw new Error("The card is missing its local database ID. Refresh the queue.");
                if (pendingReviews().length) await flushPendingReviews();
                const split = await acceptPrefixSplitCommand(prefixCard.backendId, prefixCard.revision ?? 1, prefixCard.queueEntryId);
                if ("status" in split) {
                  if (split.status === "held") throw new Error(`The accepted split is held: ${split.reason ?? "check the original attempt"}.`);
                  return "waiting" as const;
                }
                invalidateBranchEvidence();
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
        {slots.content}
        {!OptionalComposition && currentView === "settings" && <StudySettingsView
          theme={boardTheme} pieceSet={pieceSet} sound={soundOn}
          onTheme={changeBoardTheme} onPieces={changePieceSet} onSound={changeSound}
        />}
      </BoardWorkspaceContainer>
      {slots.dialogs}
      <DataDiagnosticsNotice />
      {!boardWorkspace && <Footer />}
    </main>
  );
  if (!OptionalComposition) return renderWorkspace();
  return <OptionalComposition
    controllerRef={optionalController}
    currentView={currentView} changeWorkspace={changeWorkspace}
    boardTheme={boardTheme} pieceSet={pieceSet} soundOn={soundOn}
    changeBoardTheme={changeBoardTheme} changePieceSet={changePieceSet} changeSound={changeSound}
    studyOnlyMode={studyOnlyMode} safeBreakCounter={safeBreakCounter}
    incrementSafeBreak={() => setSafeBreakCounter(count => count + 1)}
    attemptPhase={attempt.phase}
    importedRepertoires={importedRepertoires} databaseQueue={databaseQueue} offlineQueue={offlineQueue}
    onIntegrityReconciled={recheckIntegrity => {
      invalidateTrainingQueueCache();
      recheckIntegrity();
      const currentTrainingState = useTrainingStore.getState();
      const hasPlayableTrainingCard = currentTrainingState.cardsLeft > 0
        && Boolean(currentTrainingState.practiceCards[currentTrainingState.activeCardIndex]);
      if (!hasPlayableTrainingCard) void refreshDatabaseQueue(false).catch(() => undefined);
    }}
    reviewed={reviewed} cardsLeft={cardsLeft} totalCards={practiceCards.length}
    refreshDatabaseQueue={refreshDatabaseQueue} refreshQueueOnly={refreshQueueOnly}
    invalidateBranchEvidence={invalidateBranchEvidence}
    refreshQueueAfterRepertoireChange={refreshQueueAfterRepertoireChange}
    refreshDatabaseQueueAfterRepertoireChange={refreshDatabaseQueueAfterRepertoireChange}
    addImportedRepertoire={addImportedRepertoire} renameLocalRepertoire={renameLocalRepertoire}
    deleteLocalRepertoire={deleteLocalRepertoire} finishPermanentDeletion={finishPermanentDeletion}
    onCardSaved={updated => {
      invalidateBranchEvidence();
      setPracticeCards(current => current.map(item => item.id === updated.id ? updated : item));
      resetLine(updated);
      setSuggestShorter(false);
      activeQueueEntry.current = undefined;
      if (usesLocalApi()) {
        void refreshDatabaseQueue().catch(() => undefined);
        if (updated.repertoireId) window.dispatchEvent(new CustomEvent("tempo:integrity", {
          detail: { repertoireId: updated.repertoireId },
        }));
      }
    }}
  >{renderWorkspace}</OptionalComposition>;

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
