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
import { PendingOperationError } from "../lib/operation-status";
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
import { Suspense, useState, useCallback, useEffect, useRef } from "react";
import { BoardTheme, PieceSet } from "../components/chessboard";
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
import {
  View,
  LocalRepertoire,
  CardId,
  asFenString,
  asTeachingCardKey,
  asTeachingMoveKey,
} from "../types";
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
import { capturePreparedNext, advancePreparedContinuation, type PreparedContinuation } from "../lib/prepared-review-continuation";
import type { PracticeCard } from "../domain/cards";
import {
  enqueuePendingReview,
  flushPendingReviews,
  logicalAttemptId,
  pendingReviews,
  recoverableReviews,
  conflictedReviews,
  ReviewReplayError,
  reviewStudySession,
  type PendingReview,
  type ReviewFlushResult,
} from "../lib/review-outbox";
import { describeOfflineQueue, markOfflineAttemptFailed, recordOfflineAttempt } from "../lib/offline-training";
import { enqueueTrainingFailure, flushTrainingFailures } from "../lib/training-failure-outbox";
import { beginOpeningAttempt, completeOpeningAttempt, partialOpeningAttempt } from "../lib/opening-evidence-journal";
import { useOpeningEvidenceRecovery } from "../hooks/use-opening-evidence-recovery";
import type { AssistanceKind } from "../domain/opening-evidence";
import { useShallow } from "zustand/react/shallow";
import { WorkspaceRefreshStatus } from "../components/workspace-refresh-status";
import { NotificationCenter } from "../components/notification-center";
import { OfflineReviewConflicts } from "../components/offline-review-conflicts";
import { notifications, publishNotification, resolveNotification, updateNotification } from "../lib/notifications";
import { reportReviewSaveFailure } from "../lib/review-save-notice";
import { setActiveDebugWorkspace, reportDebugError } from "../lib/debug-reporting";

export default function Home({ optionalWorkspace }: { optionalWorkspace?: LegacyWorkspace } = {}) { return <Suspense fallback={<p role="status">Loading workspace…</p>}><StudyStorageBoundary><StudyComposition optionalWorkspace={optionalWorkspace} /></StudyStorageBoundary></Suspense>; }

function StudyComposition({ optionalWorkspace }: { optionalWorkspace?: LegacyWorkspace }) {
  const OptionalComposition = optionalWorkspace;
  const optionalController = useRef<LegacyWorkspaceController | null>(null);
  const studyOnlyMode = useStudyOnlyMode();
  useEffect(() => prepareMoveSounds(), []);
  const [currentView, setCurrentView] = useState<View>("train");
  const [safeBreakCounter, setSafeBreakCounter] = useState(0);
  const [reviewPersistenceState, setReviewPersistenceState] = useState<
    | "idle"
    | "saving"
    | "pendingConfirmation"
    | "saveFailed"
    | "saved"
    | "conflicted"
    | "refreshingQueue"
    | "queueFailed"
  >("idle");
  useStudyPriority(currentView === "train", studyOnlyMode, ["saving","pendingConfirmation","refreshingQueue"].includes(reviewPersistenceState));
  const [reviewPersistenceIdentity, setReviewPersistenceIdentity] = useState<Pick<PendingReview, "backendId" | "queueEntryId" | "attemptId">>();
  const [queueReconciliationError, setQueueReconciliationError] = useState("");
  const reviewSaveDiagnosticIdentity = useRef<Pick<PendingReview, "backendId" | "queueEntryId" | "attemptId"> | undefined>(undefined);
  useEffect(() => {
    const showUpdate = () => publishNotification({
      severity: "warning", source: "phone update", key: "phone-update-ready",
      message: "Tempo update ready. Finish this attempt, then close and reopen Tempo while connected. Reviews saved on this phone remain available to sync.",
    });
    window.addEventListener("tempo:update-ready", showUpdate);
    return () => window.removeEventListener("tempo:update-ready", showUpdate);
  }, []);
  const changeWorkspace = useCallback((view: View) => {
    if (!canOpenWorkspace(view, independentStudy)) return;
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
    const identity = attempt.attemptId;
    void reviewStudySession().then(coordinator => {
      if (useTrainingStore.getState().attempt.attemptId === identity) coordinator.start(`review:${identity}`);
    }).catch(error => setReviewSaveError(String(error)));
  }, [attempt.attemptId, setReviewSaveError]);
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
  const reviewPendingEntries = useRef(new Set<string>());
  const [pendingBurialEntryState, setPendingBurialEntryId] = useState<number | undefined>(() => usesLocalApi() ? pendingTrainingBurialEntry() : undefined);
  const pendingBurialEntryId = pendingBurialEntryState ?? (databaseQueue && !offlineQueue
    ? pendingTrainingBurialEntry(practiceCards.flatMap(card => card.queueEntryId ? [card.queueEntryId] : []))
    : undefined);
  const [burialRecoveryError, setBurialRecoveryError] = useState("");
  const burialRecoveryStarted = useRef(false);
  const reviewTransitionGeneration = useRef(0);
  const confirmedProgression = useRef<{ card: PracticeCard; attemptId: string; generation: number;
    transitionGeneration: number; conflicted: boolean } | undefined>(undefined);
  const progressionController = useRef<AbortController | undefined>(undefined);
  const progressionNotification = useRef<string | undefined>(undefined);
  const preparedProgressionAttempt = useRef<{ attemptId?: string; generation: number } | undefined>(undefined);
  useEffect(() => () => {
    progressionController.current?.abort();
    if (progressionNotification.current && notifications().find(record => record.id === progressionNotification.current)?.active)
      updateNotification(progressionNotification.current, { active: false,
      severity: "warning", message: "Result saved. Reopen training to recover queue progression." });
  }, []);
  const pendingOpponentReply = useRef<{
    timer: ReturnType<typeof setTimeout> | undefined;
    finish: (failed?: boolean) => void;
  } | undefined>(undefined);
  const completionTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const activeQueueEntry = useRef<number | undefined>(undefined);
  const card = practiceCards[activeCardIndex] ?? demoCards[0];
  const openingJournal = useCommittedCallback(() => card.kind !== "opening" ? undefined : beginOpeningAttempt(card, useTrainingStore.getState().attempt.attemptId,
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
  const confirmRecoveredReviews = useCommittedCallback((result: ReviewFlushResult) => {
    const statusAttemptId = reviewPersistenceIdentity && logicalAttemptId(reviewPersistenceIdentity);
    const statusPersisted = Boolean(statusAttemptId && result.persistedAttemptIds.includes(statusAttemptId));
    const statusConflicted = Boolean(statusAttemptId && result.conflictedAttemptIds.includes(statusAttemptId));
    if (!pendingReviews().length) setPendingReviewError("");
    if (!statusPersisted && !statusConflicted) return;
    setReviewPersistenceState(statusConflicted ? "conflicted" : "saved");
    setReviewSaveError("");
    const training = useTrainingStore.getState();
    if (statusAttemptId && training.attempt.attemptId === statusAttemptId)
      return progressConfirmedReview(training.getCard(), statusAttemptId, training.attempt.generation,
        reviewTransitionGeneration.current, statusConflicted, result.preparedContinuations?.[statusAttemptId]);
  });
  usePendingReviewRecovery(usesLocalApi() && !offlineQueue, queueReadiness === "ready",
    pendingBurialEntryId !== undefined || attempt.phase === "opponentReplyPending" ||
    reviewPersistenceState === "saving" || reviewPersistenceState === "refreshingQueue", confirmRecoveredReviews);
  const repertoireLine = card.moves;

  useEffect(() => {
    setActiveDebugWorkspace(currentView);
  }, [currentView]);

  const checkPendingIntegrity = useCallback(async (preferred?: string, passive = false) => {
    await optionalController.current?.checkIntegrity(preferred, passive);
  }, []);

  const refreshDatabaseQueue = useCallback(
    async (advance = false, replaySavedReviews = true) => {
      invalidateWorkspaceData();
      const result = await fetchAndInitializeQueue(advance, { replaySavedReviews });
      if (result !== "superseded") await checkPendingIntegrity();
    },
    [checkPendingIntegrity],
  );
  const reconcilePreparedQueue = useCommittedCallback(async () => {
    const expectedAttempt = preparedProgressionAttempt.current;
    const notice = progressionNotification.current;
    const controller = progressionController.current;
    if (!expectedAttempt) return;
    const finishReconciliation = measureTempoOperation("queue-reconciliation");
    const stillPrepared = () => preparedProgressionAttempt.current === expectedAttempt &&
      progressionController.current === controller && !controller?.signal.aborted &&
      useTrainingStore.getState().attempt.attemptId === expectedAttempt.attemptId &&
      useTrainingStore.getState().attempt.generation === expectedAttempt.generation;
    try {
      const result = await fetchAndInitializeQueue(false, { replaySavedReviews: false, preservePreparedAttempt: true,
        timeoutMs: 15_000, expectedAttempt, signal: controller?.signal });
      if (!stillPrepared() || result === "superseded") return;
      setQueueReconciliationError("");
      if (notice) resolveNotification(notice, { severity: "success", message: "Result saved. Upcoming queue reconciled.",
        details: { ...notifications().find(record => record.id === notice)?.details,
          phase: "prepared queue reconciled", reconciliationAttemptId: expectedAttempt.attemptId ?? "unknown" } });
    } catch (error) {
      if (!stillPrepared()) return;
      if (notice) updateNotification(notice, { severity: "warning", active: false,
        details: { ...notifications().find(record => record.id === notice)?.details,
          reconciliationAttemptId: expectedAttempt.attemptId ?? "unknown", phase: "prepared reconciliation requires recovery", error: String(error) },
        message: "Result saved. The prepared card is ready; retry loading the queue to reconcile upcoming cards." });
      setQueueReconciliationError("Queue reconciliation is waiting. You can finish the prepared card.");
    } finally {
      finishReconciliation();
    }
  });
  const progressConfirmedReview = useCommittedCallback(async (completedCard: PracticeCard, attemptId: string,
    generation: number, transitionGeneration: number, conflicted: boolean = false, continuation?: PreparedContinuation) => {
    progressionController.current?.abort();
    const controller = new AbortController();
    progressionController.current = controller;
    const expectedAttempt = { attemptId, generation };
    const stillDisplayed = () => useTrainingStore.getState().attempt.attemptId === attemptId &&
      useTrainingStore.getState().attempt.generation === generation;
    const ownsProgression = () => progressionController.current === controller &&
      transitionGeneration === reviewTransitionGeneration.current && !controller.signal.aborted;
    const details = { cardId: String(completedCard.backendId ?? completedCard.id),
      queueEntryId: Number(completedCard.queueEntryId), attemptId, phase: "review committed" };
    const notice = publishNotification({ severity: "info", source: "training review",
      key: `review-queue:${completedCard.queueEntryId ?? completedCard.id}`, details,
      message: "Result saved. Loading the next card…", active: true });
    progressionNotification.current = notice;
    const settleSuperseded = () => {
      if (progressionController.current !== controller && progressionNotification.current === notice) return;
      updateNotification(notice, { active: false, severity: "info", details: { ...details, phase: "progression superseded" },
        message: "Result saved. Queue progression was superseded; retry loading the queue if needed." });
      if (ownsProgression()) setReviewPersistenceState(stillDisplayed() ? "queueFailed" : "idle");
    };
    if (!stillDisplayed() || !ownsProgression()) { settleSuperseded(); return; }
    confirmedProgression.current = { card: completedCard, attemptId, generation, transitionGeneration, conflicted };
    preparedProgressionAttempt.current = undefined;
    setQueueReconciliationError("");
    if (!conflicted) rememberConfirmedQueueEntry(completedCard);
    invalidateWorkspaceData();
    setReviewPersistenceState(conflicted ? "conflicted" : "refreshingQueue");
    const finishReadiness = measureTempoDragPhase("next-card-readiness");
    try {
      const coordinator = await reviewStudySession();
      await coordinator.retryNext(`review:${attemptId}`, async (signal, acquisition) => {
        if (!conflicted && advancePreparedContinuation(continuation, attemptId, generation)) {
            setReviewPersistenceState("idle");
            setSafeBreakCounter(count => count + 1);
            resolveNotification(notice, { severity: "success", details: { ...details, phase: "prepared continuation selected" },
              message: "Result saved. Next card loaded." });
            preparedProgressionAttempt.current = useTrainingStore.getState().attempt;
            void reconcilePreparedQueue();
            void checkPendingIntegrity();
            return;
          }
        updateNotification(notice, { details: { ...details, phase: "authoritative refresh requested", acquisition: acquisition + 1 },
          message: acquisition ? "Result saved. Retrying the next card…" : "Result saved. Loading the next card…", active: true });
        const result = await fetchAndInitializeQueue(true, { replaySavedReviews: false, timeoutMs: 15_000, expectedAttempt, signal });
        if (!ownsProgression() || result === "superseded") { settleSuperseded(); return; }
        setReviewPersistenceState(conflicted ? "conflicted" : "idle");
        setSafeBreakCounter(count => count + 1);
        resolveNotification(notice, { severity: "success", details: { ...details, phase: result === "exhausted" ? "queue exhausted" : "next card installed" },
          message: result === "exhausted" ? "Result saved. Training queue complete." : "Result saved. Next card loaded." });
        requestAnimationFrame(() => finishReadiness());
      }, ownsProgression, error => !(error instanceof QueueProcessingError));
      if (!ownsProgression()) { finishReadiness(true); settleSuperseded(); }
    } catch (error) {
      if (!ownsProgression() || !stillDisplayed()) { finishReadiness(true); settleSuperseded(); return; }
      finishReadiness(true);
      setReviewPersistenceState(conflicted ? "conflicted" : "queueFailed");
      updateNotification(notice, { severity: "warning", active: false, details: { ...details, phase: "fallback/retry entered", error: String(error) },
        message: "Result saved; the next card could not be loaded. Retry loading the queue." });
    }
  });
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

  const refreshVisibleQueue = useCommittedCallback((preparePhoneQueue: boolean) => {
    const activeAttempt = useTrainingStore.getState().attempt;
    const progression = confirmedProgression.current;
    // The confirmed-review owner controls loading and its two-attempt budget.
    // A periodic/visibility refresh cannot supersede it or reset its recovery.
    if (progression && progression.attemptId === activeAttempt.attemptId && progression.generation === activeAttempt.generation) return;
    const preparedAttempt = preparedProgressionAttempt.current;
    if (preparedAttempt && preparedAttempt.attemptId === activeAttempt.attemptId && preparedAttempt.generation === activeAttempt.generation) {
      void reconcilePreparedQueue();
      return;
    }
    // Normal reconnection syncs saved results. Only the confirmed-review owner
    // above performs queue-only acquisition and disables both replay paths.
    void fetchAndInitializeQueue(false, { preparePhoneQueue }).catch(() => undefined);
  });

  useEffect(() => {
    if (!usesLocalApi() || currentView !== "train") return;
    let visibleRefreshCount = 0;
    const refreshWhenVisible = () => {
      if (document.visibilityState !== "visible") return;
      if (useTrainingStore.getState().serviceError.includes("no longer in today's queue")) return;
      visibleRefreshCount = 0;
      refreshVisibleQueue(true);
    };
    const refreshTimer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (useTrainingStore.getState().serviceError.includes("no longer in today's queue")) return;
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
          const indexedPositions = await runStudyTask<RepertoireAlternativeSnapshot>({
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
    if (pendingBurialEntryId !== undefined || (serviceError && !offlineQueue)) return;
    const currentTurn =
      new Chess(currentFenString).turn() === "b" ? "black" : "white";
    if (
      isLocked ||
      step >= card.moves.length ||
      currentTurn !== trainedColor(card)
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
    // Queue recovery can fail while this exact completed result is retained.
    // Retrying its frozen delivery must remain possible without enabling grading.
    if (pendingBurialEntryId !== undefined || (serviceError && !offlineQueue && !options.retryPending)) return;
    const entryKey = String(card.queueEntryId ?? card.id);
    if (reviewPendingEntries.current.has(entryKey) || cardsLeft === 0) return;
    const pendingBeforeReview = databaseQueue && !offlineQueue ? pendingReviews() : [];
    if (!options.retryPending && !options.recordedAtCompletion &&
        pendingBeforeReview.some((review) => review.queueEntryId === card.queueEntryId && review.backendId === String(card.backendId ?? card.id))) {
      setPendingReviewError("This card has a review waiting to save. Retry saving the review before grading it again.");
      return;
    }
    reviewPendingEntries.current.add(entryKey);
    const retryReview = reviewPersistenceIdentity
      ? pendingBeforeReview.find(review => logicalAttemptId(review) === logicalAttemptId(reviewPersistenceIdentity))
      : pendingBeforeReview[0];
    const retryAttemptId = retryReview ? logicalAttemptId(retryReview)
      : reviewPersistenceIdentity && logicalAttemptId(reviewPersistenceIdentity);
    const transitionGeneration = ++reviewTransitionGeneration.current;
    const displayedAttemptGeneration = useTrainingStore.getState().attempt.generation;
    const { recordedAtCompletion = false, retryPending = false } = options;
    clearTimeout(completionTimer.current);
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
        const nextCards = await loadEligibleOfflineQueue(saved);
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
      let submittedAttemptId: string | undefined;
      // Retention/capture can fail before an outbox envelope exists. Keep diagnostics
      // owned by the original attempt even if a later projection replaces the card.
      const saveIdentity = (retryPending && (retryReview ?? reviewPersistenceIdentity ?? reviewSaveDiagnosticIdentity.current)) || {
        backendId: card.backendId, queueEntryId: card.queueEntryId ?? 0,
        attemptId: useTrainingStore.getState().attempt.attemptId,
      };
      reviewSaveDiagnosticIdentity.current = saveIdentity;
      const showSubmittedReviewUnconfirmed = () => {
        if (transitionGeneration !== reviewTransitionGeneration.current) return;
        const retainedReviews = pendingReviews();
        const submittedIndex = retainedReviews.findIndex(review => logicalAttemptId(review) === submittedAttemptId);
        const submittedReview = retainedReviews[submittedIndex];
        const earlierRequiresAttention = submittedReview && retainedReviews.slice(0, submittedIndex).some(review =>
          review.backendId === submittedReview.backendId && review.automaticRecoverySuppressed);
        const message = earlierRequiresAttention
          ? "An earlier result for this card needs attention. Use Check saved reviews to resolve it first."
          : submittedReview?.automaticRecoverySuppressed
            ? "This result needs explicit attention. Keep this browser's data and open Notifications before retrying."
            : "Waiting for the computer to confirm this result.";
        setReviewPersistenceState(earlierRequiresAttention || submittedReview?.automaticRecoverySuppressed ? "saveFailed" : "pendingConfirmation");
        setReviewSaveError(message);
        if (earlierRequiresAttention) setPendingReviewError(message);
      };
      try {
        if (!retryPending) {
          if (!card.queueEntryId)
            throw new Error(
              "The active queue entry is unavailable. Refresh the queue.",
            );
          if (!recordedAtCompletion) {
            if (useTrainingStore.getState().isAttemptFailed && !await persistExplicitAttemptFailure()) throw new Error("The guided failure must be saved before completing this review.");
            openingJournal();
            const attemptId = useTrainingStore.getState().attempt.attemptId;
            const completion = completeOpeningAttempt(attemptId);
            submittedAttemptId = await enqueuePendingReview({
              attemptId, openingEvidenceCompletion: completion, completedAt: completion?.terminal?.ended_at,
              backendId: card.backendId,
              queueEntryId: card.queueEntryId,
              expectedRevision: card.revision,
              preparedNext: capturePreparedNext(),
              outcome,
              guided:
                attemptFailed ||
                useTrainingStore.getState().assistedThisAttempt,
            });
          }
          const submittedReview = pendingReviews().find((review) => review.queueEntryId === card.queueEntryId && review.backendId === String(card.backendId ?? card.id));
          submittedAttemptId = submittedAttemptId ?? (submittedReview ? logicalAttemptId(submittedReview)
            : recordedAtCompletion ? useTrainingStore.getState().attempt.attemptId : undefined);
          reviewSaveDiagnosticIdentity.current = undefined;
          setReviewPersistenceIdentity(submittedAttemptId ? { backendId: card.backendId,
            queueEntryId: card.queueEntryId, attemptId: submittedAttemptId } : undefined);
          // Advancement still requires authoritative, transaction-bound next-head issuance.
          setReviewed((count) => count + 1);
        }
        if (retryPending) {
          submittedAttemptId = retryAttemptId;
          reviewSaveDiagnosticIdentity.current = undefined;
          setReviewPersistenceIdentity(retryReview ? { ...retryReview, attemptId: submittedAttemptId } : reviewPersistenceIdentity);
        }
        if (!submittedAttemptId) throw new Error("The completed review identity is unavailable. Keep this browser's data and check saved reviews.");
        const finishReviewPersistence = measureTempoDragPhase("review-persistence");
        let flushResult: ReviewFlushResult;
        try {
          flushResult = await flushPendingReviews(Infinity, retryPending, retryPending ? submittedAttemptId : undefined, undefined, submittedAttemptId);
          finishReviewPersistence();
        }
        catch (error) {
          if (error instanceof ReviewReplayError && error.flushResult &&
              [...error.flushResult.persistedAttemptIds, ...error.flushResult.conflictedAttemptIds].includes(submittedAttemptId)) {
            flushResult = error.flushResult;
            finishReviewPersistence();
            setPendingReviewError(error.message);
          } else { finishReviewPersistence(true); throw error; }
        }
        const resultPersisted = flushResult.persistedAttemptIds.includes(submittedAttemptId);
        const resultConflicted = flushResult.conflictedAttemptIds.includes(submittedAttemptId) ||
          conflictedReviews().some(review => logicalAttemptId(review) === submittedAttemptId);
        if (!resultPersisted && !resultConflicted) {
          showSubmittedReviewUnconfirmed();
          reviewPendingEntries.current.delete(entryKey);
          return;
        }
        if (transitionGeneration === reviewTransitionGeneration.current)
          setReviewPersistenceState(resultConflicted ? "conflicted" : "saved");
        setQueueNotice("");
        reviewPendingEntries.current.delete(entryKey);
        void progressConfirmedReview(card, submittedAttemptId, displayedAttemptGeneration, transitionGeneration,
          resultConflicted, flushResult.preparedContinuations?.[submittedAttemptId]);
        return;
      } catch (error) {
        reviewPendingEntries.current.delete(entryKey);
        if (error instanceof ReviewReplayError) {
          reportDebugError(error, { kind: "api", source: "training-review-replay", operation: "save pending review",
            endpoint: error.endpoint, status: error.status, retryable: error.retryable,
            cardId: error.backendId, queueEntryId: error.queueEntryId, attemptId: error.attemptId, code: error.code,
            classification: error.classification, notify: false });
        } else {
          // Diagnostic ownership must never make an unretained result eligible for receipt checks.
          const diagnosticIdentity = submittedAttemptId ? { ...saveIdentity, attemptId: submittedAttemptId } : saveIdentity;
          if (transitionGeneration === reviewTransitionGeneration.current)
            reviewSaveDiagnosticIdentity.current = diagnosticIdentity;
          reportReviewSaveFailure(error, diagnosticIdentity);
        }
        if (transitionGeneration !== reviewTransitionGeneration.current) return;
        if (error instanceof ReviewReplayError && error.attemptId !== submittedAttemptId) {
          // The outbox reports that other attempt separately; it cannot settle this result.
          showSubmittedReviewUnconfirmed();
          return;
        }
        if (submittedAttemptId && pendingReviews().some(review => logicalAttemptId(review) === submittedAttemptId) &&
            !recoverableReviews(submittedAttemptId).some(review => logicalAttemptId(review) === submittedAttemptId)) {
          showSubmittedReviewUnconfirmed();
          return;
        }
        const awaitingConfirmation = (error instanceof ReviewReplayError && !error.blocked && ["pending", "transient"].includes(error.classification)) ||
          (error instanceof PendingOperationError && !error.blocked);
        setReviewPersistenceState(awaitingConfirmation ? "pendingConfirmation" : "saveFailed");
        setReviewSaveError(
          awaitingConfirmation ? "Waiting for the computer to confirm this result." :
          error instanceof ReviewReplayError && error.classification === "storage" ? "This browser could not update the saved result. Keep its data and open Notifications for details." :
          error instanceof ReviewReplayError && error.blocked ? "Saving is blocked. Resolve and retry the operation in Jobs." :
          "This result could not be confirmed. Keep this browser's data and open Notifications for details before retrying.",
        );
        setQueueNotice("");
        if (!retryPending)
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
    reviewPendingEntries.current.delete(entryKey);
    setReviewPersistenceState("idle");
    setSafeBreakCounter((count) => count + 1);
  }

  async function completeAttempt(finalFen: string) {
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
        if (outcome === "again" && !await persistExplicitAttemptFailure()) throw new Error("The guided failure must be saved before completing this review.");
        if (!isCurrentAttempt(useTrainingStore.getState().attempt, token)) return;
        await enqueuePendingReview({
          attemptId: token.attemptId, completedAt, openingEvidenceCompletion: completion,
          backendId: card.backendId,
          queueEntryId: card.queueEntryId,
          expectedRevision: card.revision,
          preparedNext: capturePreparedNext(),
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
        return;
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
      const state = { cardId: String(card.backendId ?? card.id), revision: card.revision ?? 1, ply: step };
      void (card.backendId ? enqueueTeachingState(state) : rememberTeachingFact({ key: currentMoveKey, ...state, kind: "seen" }))
        .then(() => { setSeenMoves(current => new Set(current).add(currentMoveKey)); if (card.backendId) void flushTeachingStates().catch(() => undefined); })
        .catch(error => showTrainingNotice(`Teaching progress could not be saved. Keep this page open. ${String(error)}`, "error"));
    });
  }, [
    card.kind,
    card.queueAttemptState,
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
            {studyOnlyMode === true && <div className="ui-notice" role="status">Study-only mode is on. Optional analysis and imports are paused. Review saves and the daily queue remain available.</div>}
            <OfflineReviewConflicts />
            {queueReconciliationError && <div role="status">
              {queueReconciliationError}{" "}
              <Button onClick={() => void reconcilePreparedQueue()}>Retry loading the queue</Button>
            </div>}
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
              <div className="ui-notice" role="status">
                <span>A previous result needs confirmation or attention. Open Notifications for details.</span>
                <Button onClick={() => void (async () => {
                  const retainedReview = pendingReviews()[0];
                  if (retainedReview) await confirmRecoveredReviews(await flushPendingReviews(Infinity, true,
                    logicalAttemptId(retainedReview)));
                  if (!reviewPersistenceIdentity) await refreshDatabaseQueue(false, false);
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
              reviewPersistenceIdentity={reviewSaveDiagnosticIdentity.current ?? reviewPersistenceIdentity}
              retryReviewSave={() =>
                void rateCard("correct", { retryPending: true })
              }
              retryQueueAfterReview={() => {
                const progression = confirmedProgression.current;
                if (progression) void progressConfirmedReview(progression.card, progression.attemptId,
                  progression.generation, progression.transitionGeneration, progression.conflicted);
              }}
              handleAttemptFailure={handleAttemptFailure}
              resetCardAttempt={resetCardAttempt}
              setEditorCard={slots.onEditCard}
              onAcceptPrefixSplit={async (prefixCard) => {
                if (!prefixCard.backendId)
                  throw new Error("The card is missing its local database ID. Refresh the queue.");
                if (pendingReviews().length) await flushPendingReviews();
                await acceptPrefixSplitCommand(prefixCard.backendId, prefixCard.revision ?? 1, prefixCard.queueEntryId);
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
