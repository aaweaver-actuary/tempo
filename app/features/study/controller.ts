import { useCommittedCallback } from "../../hooks/use-committed-callback";
import { useTrainingStore } from "../../state/training-store";
import { advancePreparedContinuation } from "../../lib/prepared-review-continuation";
import { invalidateWorkspaceData } from "../../lib/workspace-data";
import { measureTempoOperation } from "../../lib/performance";
import { notifications, publishNotification, resolveNotification, updateNotification } from "../../lib/notifications";
import { reviewStudySession } from "../../lib/review-outbox";
import { useEffect, useRef, useState } from "react";
import type { PracticeCard } from "../../domain/cards";
import { PendingOperationError } from "../../lib/operation-status";
import { measureTempoDragPhase } from "../../lib/performance";
import { reportDebugError } from "../../lib/debug-reporting";
import { reportReviewSaveFailure } from "../../lib/review-save-notice";
import { enqueuePendingReview, flushPendingReviews, logicalAttemptId, pendingReviews, recoverableReviews,
  conflictedReviews, ReviewReplayError, type PendingReview, type ReviewFlushResult } from "../../lib/review-outbox";
import { studyWorkspace } from "../../lib/study-connection";
import { studyIntentStore, isStudyIntentStoreCurrent } from "../../../database/browser/study-intent-storage";
import type { PreparedContinuation } from "../../lib/prepared-review-continuation";

export type ReviewPhase = "idle" | "saving" | "pendingConfirmation" | "saveFailed" | "saved" | "conflicted" | "refreshingQueue" | "queueFailed";
export type StudyReviewIdentity = Readonly<Pick<PendingReview, "backendId" | "queueEntryId" | "attemptId">>;
type Identity = StudyReviewIdentity;
export type StudyReviewSnapshot = Readonly<{ phase: ReviewPhase; identity?: StudyReviewIdentity; diagnosticIdentity?: StudyReviewIdentity; queueReconciliationError: string }>;
type AuthorityBinding = { authority: string; store: Awaited<ReturnType<typeof studyIntentStore>> };
function sameAuthority(authority: string, store: Awaited<ReturnType<typeof studyIntentStore>>) {
  try { return studyWorkspace() === authority && isStudyIntentStoreCurrent(store); }
  catch { return false; }
}
export type StudyReviewEvent = { type: "error"; message: string } | { type: "pending-error"; message: string } |
  { type: "reviewed" } | { type: "clear-queue-notice" } | { type: "feedback-pause" };
export type StudyReviewSubmitOptions = {
  card: PracticeCard; attemptId?: string; attemptGeneration: number; transitionGeneration: number;
  retryPending: boolean; recordedAtCompletion: boolean;
  prepare: () => Promise<PendingReview>;
  onEvent: (event: StudyReviewEvent) => void;
};

/** Presentation and orchestration only; the existing outbox/session owns durable settlement. */
export type StudyReviewControllerOptions = { onNextCard: () => void; checkIntegrity: () => Promise<unknown>;
  acquireQueue: (advance: boolean, options: { replaySavedReviews?: boolean; preservePreparedAttempt?: boolean; timeoutMs?: number; expectedAttempt?: { attemptId?: string; generation: number }; signal?: AbortSignal; retryPreparation?: boolean; preparePhoneQueue?: boolean }) => Promise<"installed" | "exhausted" | "superseded">;
  rememberConfirmed: (card: PracticeCard) => void; isRetryable: (error: unknown) => boolean;
  attempt?: { attemptId?: string; generation: number } };

export function useStudyReviewController(options: StudyReviewControllerOptions) {
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const [queueReconciliationError, setQueueReconciliationError] = useState("");
  const confirmedProgression = useRef<{ card: PracticeCard; attemptId: string; generation: number; transitionGeneration: number; conflicted: boolean; binding?: AuthorityBinding } | undefined>(undefined);
  const progressionController = useRef<AbortController | undefined>(undefined);
  const progressionNotification = useRef<string | undefined>(undefined);
  const preparedProgressionAttempt = useRef<{ attemptId?: string; generation: number } | undefined>(undefined);
  const [phase, setPhase] = useState<ReviewPhase>("idle");
  const [identity, setIdentity] = useState<Identity>();
  const diagnostic = useRef<Identity | undefined>(undefined);
  const transition = useRef(0);
  const pendingEntries = useRef(new Set<string>());
  const lifecycle = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; lifecycle.current += 1;
      progressionController.current?.abort();
      if (progressionNotification.current && notifications().find(record => record.id === progressionNotification.current)?.active)
        updateNotification(progressionNotification.current, { active: false, severity: "warning", message: "Result saved. Reopen training to recover queue progression." });
    };
  }, []);
  useEffect(() => {
    if (!options.attempt) return;
    const expected = options.attempt, generation = lifecycle.current;
    let cancelled = false;
    let authority: string;
    try { authority = studyWorkspace(); } catch (error) {
      useTrainingStore.getState().setReviewSaveError(String(error));
      return;
    }
    void studyIntentStore().then(async store => {
      if (cancelled || !sameAuthority(authority, store)) return;
      const coordinator = await reviewStudySession();
      const active = useTrainingStore.getState().attempt;
      if (!cancelled && mounted.current && lifecycle.current === generation && sameAuthority(authority, store) && active.attemptId === expected.attemptId && active.generation === expected.generation)
        coordinator.start(`review:${expected.attemptId}`);
    }).catch(error => {
      const active = useTrainingStore.getState().attempt;
      let same = false;
      try { same = studyWorkspace() === authority; } catch { /* The original authority is no longer available. */ }
      if (!cancelled && same && mounted.current && lifecycle.current === generation && active.attemptId === expected.attemptId && active.generation === expected.generation) useTrainingStore.getState().setReviewSaveError(String(error));
    });
    return () => { cancelled = true; };
  }, [options.attempt?.attemptId, options.attempt?.generation]);
  // A presentation lease binds Home's existing local/offline work to this controller's
  // transition, without exposing its mutable guard or phase setter.
  const beginReview = (entryKey: string) => {
    if (!mounted.current || pendingEntries.current.has(entryKey)) return undefined;
    pendingEntries.current.add(entryKey);
    setPhase("saving");
    return ++transition.current;
  };
  const finishReview = (entryKey: string) => { pendingEntries.current.delete(entryKey); };
  const finishLocalReview = (entryKey: string, outcome: "saved" | "failed") => {
    pendingEntries.current.delete(entryKey);
    setPhase(outcome === "saved" ? "idle" : "saveFailed");
  };
  const markRetentionFailed = () => setPhase("saveFailed");
  const capturePresentation = () => {
    const generation = lifecycle.current, reviewTransition = transition.current;
    return () => mounted.current && generation === lifecycle.current && reviewTransition === transition.current;
  };
  const retain = (review: PendingReview) => enqueuePendingReview(review);
  const hasPendingForCard = (card: PracticeCard) => pendingReviews().some(review =>
    review.queueEntryId === card.queueEntryId && review.backendId === String(card.backendId ?? card.id));
  const reconcilePreparedQueue = useCommittedCallback(async () => {
    const expectedAttempt = preparedProgressionAttempt.current;
    const notice = progressionNotification.current;
    const controller = progressionController.current;
    const binding = confirmedProgression.current?.binding;
    if (!expectedAttempt) return;
    const finishReconciliation = measureTempoOperation("queue-reconciliation");
    const stillPrepared = () => mounted.current && (!binding || sameAuthority(binding.authority, binding.store)) && preparedProgressionAttempt.current === expectedAttempt &&
      progressionController.current === controller && !controller?.signal.aborted &&
      useTrainingStore.getState().attempt.attemptId === expectedAttempt.attemptId &&
      useTrainingStore.getState().attempt.generation === expectedAttempt.generation;
    try {
      if (!stillPrepared()) return;
      const result = await optionsRef.current.acquireQueue(false, { replaySavedReviews: false, preservePreparedAttempt: true,
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
    generation: number, transitionGeneration: number, conflicted: boolean = false, continuation?: PreparedContinuation, retryPreparation: boolean = false,
    binding?: AuthorityBinding) => {
    if (!mounted.current || (binding && !sameAuthority(binding.authority, binding.store))) return;
    const progressionLifecycle = lifecycle.current;
    progressionController.current?.abort();
    const controller = new AbortController();
    progressionController.current = controller;
    const expectedAttempt = { attemptId, generation };
    const stillDisplayed = () => useTrainingStore.getState().attempt.attemptId === attemptId &&
      useTrainingStore.getState().attempt.generation === generation;
    let invalidated = false;
    const ownsProgression = () => mounted.current && lifecycle.current === progressionLifecycle && !invalidated &&
      (!binding || sameAuthority(binding.authority, binding.store)) && progressionController.current === controller &&
      transitionGeneration === transition.current && !controller.signal.aborted;
    const details = { cardId: String(completedCard.backendId ?? completedCard.id),
      queueEntryId: Number(completedCard.queueEntryId), attemptId, phase: "review committed" };
    const notice = publishNotification({ severity: "info", source: "training review",
      key: `review-queue:${completedCard.queueEntryId ?? completedCard.id}`, details,
      message: "Result saved. Loading the next card…", active: true });
    progressionNotification.current = notice;
    const settleSuperseded = () => {
      if (!mounted.current || lifecycle.current !== progressionLifecycle || (binding && !sameAuthority(binding.authority, binding.store))) return;
      if (progressionController.current !== controller && progressionNotification.current === notice) return;
      updateNotification(notice, { active: false, severity: "info", details: { ...details, phase: "progression superseded" },
        message: "Result saved. Queue progression was superseded; retry loading the queue if needed." });
      if (progressionController.current === controller && transitionGeneration === transition.current && !controller.signal.aborted)
        setPhase(stillDisplayed() ? "queueFailed" : "idle");
    };
    if (!stillDisplayed() || !ownsProgression()) { settleSuperseded(); return; }
    confirmedProgression.current = { card: completedCard, attemptId, generation, transitionGeneration, conflicted, binding };
    preparedProgressionAttempt.current = undefined;
    setQueueReconciliationError("");
    if (!conflicted) optionsRef.current.rememberConfirmed(completedCard);
    invalidateWorkspaceData();
    setPhase(conflicted ? "conflicted" : "refreshingQueue");
    const finishReadiness = measureTempoDragPhase("next-card-readiness");
    try {
      const coordinator = await reviewStudySession();
      if (!ownsProgression() || !stillDisplayed()) { invalidated = true; finishReadiness(true); settleSuperseded(); return; }
      await coordinator.retryNext(`review:${attemptId}`, async (signal, acquisition) => {
        if (!ownsProgression() || !stillDisplayed()) { invalidated = true; return; }
        if (!conflicted && advancePreparedContinuation(continuation, attemptId, generation)) {
            setPhase("idle");
            optionsRef.current.onNextCard();
            resolveNotification(notice, { severity: "success", details: { ...details, phase: "prepared continuation selected" },
              message: "Result saved. Next card loaded." });
            preparedProgressionAttempt.current = useTrainingStore.getState().attempt;
            void reconcilePreparedQueue();
            void optionsRef.current.checkIntegrity();
            return;
          }
        updateNotification(notice, { details: { ...details, phase: "authoritative refresh requested", acquisition: acquisition + 1 },
          message: acquisition ? "Result saved. Retrying the next card…" : "Result saved. Loading the next card…", active: true });
        const result = await optionsRef.current.acquireQueue(true, { replaySavedReviews: false, timeoutMs: 15_000, expectedAttempt, signal: AbortSignal.any([signal, controller.signal]), retryPreparation: retryPreparation && acquisition === 0 });
        if (!ownsProgression() || result === "superseded") { invalidated = true; settleSuperseded(); return; }
        setPhase(conflicted ? "conflicted" : "idle");
        optionsRef.current.onNextCard();
        resolveNotification(notice, { severity: "success", details: { ...details, phase: result === "exhausted" ? "queue exhausted" : "next card installed" },
          message: result === "exhausted" ? "Result saved. Training queue complete." : "Result saved. Next card loaded." });
        requestAnimationFrame(() => finishReadiness());
      }, ownsProgression, error => optionsRef.current.isRetryable(error));
      if (!ownsProgression()) { finishReadiness(true); settleSuperseded(); }
    } catch (error) {
      if (!ownsProgression() || !stillDisplayed()) { finishReadiness(true); settleSuperseded(); return; }
      finishReadiness(true);
      setPhase(conflicted ? "conflicted" : "queueFailed");
      updateNotification(notice, { severity: "warning", active: false, details: { ...details, phase: "fallback/retry entered", error: String(error) },
        message: "Result saved; the next card could not be loaded. Retry loading the queue." });
    }
  });
  const settleConfirmation = (result: ReviewFlushResult, attemptId: string, card: PracticeCard,
    generation: number, transitionGeneration: number, conflicted: boolean, binding?: AuthorityBinding) => {
    setPhase(conflicted ? "conflicted" : "saved");
    const active = useTrainingStore.getState().attempt;
    if (active.attemptId === attemptId && active.generation === generation)
      void progressConfirmedReview(card, attemptId, generation, transitionGeneration, conflicted,
        result.preparedContinuations?.[attemptId], false, binding);
  };
  // Bind deferred facts before the flush, including its storage and presentation authority.
  const captureConfirmation = useCommittedCallback(() => {
    const generation = lifecycle.current, reviewTransition = transition.current;
    const displayed = useTrainingStore.getState().attempt;
    const statusAttemptId = identity && logicalAttemptId(identity);
    let authority: string;
    try { authority = studyWorkspace(); } catch { return async (_result: ReviewFlushResult) => false; }
    const store = studyIntentStore().catch(() => undefined);
    return async (result: ReviewFlushResult) => {
      const originalStore = await store;
      const training = useTrainingStore.getState();
      if (!mounted.current || lifecycle.current !== generation || transition.current !== reviewTransition ||
          !originalStore || !sameAuthority(authority, originalStore) ||
          training.attempt.attemptId !== displayed.attemptId || training.attempt.generation !== displayed.generation) return false;
      if (!pendingReviews().length) training.setPendingReviewError("");
      const persisted = Boolean(statusAttemptId && result.persistedAttemptIds.includes(statusAttemptId));
      const conflicted = Boolean(statusAttemptId && result.conflictedAttemptIds.includes(statusAttemptId));
      if (!persisted && !conflicted) return true;
      training.setReviewSaveError("");
      if (statusAttemptId) settleConfirmation(result, statusAttemptId, training.getCard(), displayed.generation, reviewTransition, conflicted, { authority, store: originalStore });
      return true;
    };
  });
  const checkSavedReviews = async () => {
    if (!mounted.current) return undefined;
    const confirm = captureConfirmation();
    const generation = lifecycle.current, reviewTransition = transition.current;
    const review = pendingReviews()[0];
    let result: ReviewFlushResult | undefined;
    try {
      if (review) { result = await flushPendingReviews(Infinity, true, logicalAttemptId(review)); if (!await confirm(result)) return undefined; }
      else if (!await confirm({ persistedAttemptIds: [], conflictedAttemptIds: [] })) return undefined;
    } catch (error) {
      if (error instanceof ReviewReplayError && error.flushResult) await confirm(error.flushResult);
      throw error;
    }
    return mounted.current && lifecycle.current === generation && transition.current === reviewTransition ? result ?? true : undefined;
  };
  const retryQueueAfterReview = () => {
    const progression = confirmedProgression.current;
    if (progression) void progressConfirmedReview(progression.card, progression.attemptId,
      progression.generation, progression.transitionGeneration, progression.conflicted, undefined, true, progression.binding);
  };
  const refreshVisibleQueue = useCommittedCallback((preparePhoneQueue: boolean) => {
    const activeAttempt = useTrainingStore.getState().attempt;
    const progression = confirmedProgression.current;
    if (progression && progression.attemptId === activeAttempt.attemptId && progression.generation === activeAttempt.generation) return;
    const preparedAttempt = preparedProgressionAttempt.current;
    if (preparedAttempt && preparedAttempt.attemptId === activeAttempt.attemptId && preparedAttempt.generation === activeAttempt.generation) {
      void reconcilePreparedQueue(); return;
    }
    void optionsRef.current.acquireQueue(false, { preparePhoneQueue }).catch(() => undefined);
  });
  async function submit(input: StudyReviewSubmitOptions) {
    const { card, retryPending, recordedAtCompletion, transitionGeneration, onEvent } = input;
    const generation = lifecycle.current;
    const displayed = useTrainingStore.getState().attempt;
    let authority: string | undefined;
    try { authority = studyWorkspace(); } catch { /* Durable work still reports its own failure. */ }
    const storePromise = studyIntentStore().catch(() => undefined);
    let binding: AuthorityBinding | undefined;
    const bindingPromise = storePromise.then(store => { if (authority && store) binding = { authority, store }; });
    const current = () => {
      const active = useTrainingStore.getState().attempt;
      return mounted.current && generation === lifecycle.current && transitionGeneration === transition.current &&
        Boolean(binding && sameAuthority(binding.authority, binding.store)) &&
        active.attemptId === displayed.attemptId && active.generation === displayed.generation;
    };
    const before = pendingReviews();
    const retryReview = identity ? before.find(review => logicalAttemptId(review) === logicalAttemptId(identity)) : before[0];
    let submittedAttemptId: string | undefined;
    const saveIdentity: Identity = (retryPending && (retryReview ?? identity ?? diagnostic.current)) || {
      backendId: card.backendId!, queueEntryId: card.queueEntryId ?? 0, attemptId: input.attemptId,
    };
    diagnostic.current = saveIdentity;
    const unconfirmed = () => {
      if (!current()) return;
      const retained = pendingReviews();
      const index = retained.findIndex(review => logicalAttemptId(review) === submittedAttemptId);
      const submitted = retained[index];
      const earlier = submitted && retained.slice(0, index).some(review => review.backendId === submitted.backendId && review.automaticRecoverySuppressed);
      const message = earlier ? "An earlier result for this card needs attention. Use Check saved reviews to resolve it first."
        : submitted?.automaticRecoverySuppressed ? "This result needs explicit attention. Keep this browser's data and open Notifications before retrying."
        : "Waiting for the computer to confirm this result.";
      setPhase(earlier || submitted?.automaticRecoverySuppressed ? "saveFailed" : "pendingConfirmation");
      onEvent({ type: "error", message });
      if (earlier) onEvent({ type: "pending-error", message });
    };
    try {
      if (!retryPending) {
        if (!card.queueEntryId) throw new Error("The active queue entry is unavailable. Refresh the queue.");
        if (!recordedAtCompletion) submittedAttemptId = await retain(await input.prepare());
        const submitted = pendingReviews().find(review => review.queueEntryId === card.queueEntryId && review.backendId === String(card.backendId ?? card.id));
        submittedAttemptId = submittedAttemptId ?? (recordedAtCompletion && input.attemptId ? input.attemptId : submitted ? logicalAttemptId(submitted) : undefined);
        void bindingPromise.then(() => {
          if (!current()) return;
          diagnostic.current = undefined;
          setIdentity(submittedAttemptId ? { backendId: card.backendId!, queueEntryId: card.queueEntryId!, attemptId: submittedAttemptId } : undefined);
          onEvent({ type: "reviewed" });
        });
      } else {
        submittedAttemptId = retryReview ? logicalAttemptId(retryReview) : identity && logicalAttemptId(identity);
        void bindingPromise.then(() => {
          if (!current()) return;
          diagnostic.current = undefined;
          setIdentity(retryReview ? { ...retryReview, attemptId: submittedAttemptId } : identity);
        });
      }
      if (!submittedAttemptId) throw new Error("The completed review identity is unavailable. Keep this browser's data and check saved reviews.");
      const finish = measureTempoDragPhase("review-persistence");
      let result: ReviewFlushResult;
      try {
        result = await flushPendingReviews(Infinity, retryPending, retryPending ? submittedAttemptId : undefined, undefined, submittedAttemptId);
        finish();
      } catch (error) {
        await bindingPromise;
        if (error instanceof ReviewReplayError && error.flushResult && [...error.flushResult.persistedAttemptIds, ...error.flushResult.conflictedAttemptIds].includes(submittedAttemptId)) {
          result = error.flushResult; finish();
          if (current()) onEvent({ type: "pending-error", message: error.message });
        } else { finish(true); throw error; }
      }
      await bindingPromise;
      if (!current()) return;
      const persisted = result.persistedAttemptIds.includes(submittedAttemptId);
      const conflicted = result.conflictedAttemptIds.includes(submittedAttemptId) || conflictedReviews().some(review => logicalAttemptId(review) === submittedAttemptId);
      if (!persisted && !conflicted) { unconfirmed(); return; }
      if (current()) {
        onEvent({ type: "clear-queue-notice" });
        settleConfirmation(result, submittedAttemptId, card, input.attemptGeneration, transitionGeneration, conflicted, binding);
      }
    } catch (error) {
      await bindingPromise;
      if (error instanceof ReviewReplayError) {
        reportDebugError(error, { kind: "api", source: "training-review-replay", operation: "save pending review", endpoint: error.endpoint,
          status: error.status, retryable: error.retryable, cardId: error.backendId, queueEntryId: error.queueEntryId,
          attemptId: error.attemptId, code: error.code, classification: error.classification, notify: false });
      } else {
        const diagnosticIdentity = submittedAttemptId ? { ...saveIdentity, attemptId: submittedAttemptId } : saveIdentity;
        if (current()) diagnostic.current = diagnosticIdentity;
        reportReviewSaveFailure(error, diagnosticIdentity);
      }
      if (!current()) return;
      if (error instanceof ReviewReplayError && error.attemptId !== submittedAttemptId) { unconfirmed(); return; }
      if (submittedAttemptId && pendingReviews().some(review => logicalAttemptId(review) === submittedAttemptId) &&
          !recoverableReviews(submittedAttemptId).some(review => logicalAttemptId(review) === submittedAttemptId)) { unconfirmed(); return; }
      const awaiting = (error instanceof ReviewReplayError && !error.blocked && ["pending", "transient"].includes(error.classification)) ||
        (error instanceof PendingOperationError && !error.blocked);
      setPhase(awaiting ? "pendingConfirmation" : "saveFailed");
      onEvent({ type: "error", message: awaiting ? "Waiting for the computer to confirm this result." :
        error instanceof ReviewReplayError && error.classification === "storage" ? "This browser could not update the saved result. Keep its data and open Notifications for details." :
        error instanceof ReviewReplayError && error.blocked ? "Saving is blocked. Resolve and retry the operation in Jobs." :
        "This result could not be confirmed. Keep this browser's data and open Notifications for details before retrying." });
      onEvent({ type: "clear-queue-notice" });
      if (!retryPending) onEvent({ type: "feedback-pause" });
    } finally {
      // A replaced attempt cannot receive this result, but its saving indicator
      // must not remain on the replacement. A newer transition owns its own state.
      const replacedWhileOwned = () => {
        const active = useTrainingStore.getState().attempt;
        return mounted.current && generation === lifecycle.current && transitionGeneration === transition.current &&
          Boolean(binding && sameAuthority(binding.authority, binding.store)) &&
          (active.attemptId !== displayed.attemptId || active.generation !== displayed.generation);
      };
      if (replacedWhileOwned()) setPhase(previous => replacedWhileOwned() && previous === "saving" ? "idle" : previous);
    }
  }
  const snapshot: StudyReviewSnapshot = { phase, identity, diagnosticIdentity: diagnostic.current, queueReconciliationError };
  return { snapshot, beginReview, finishReview, finishLocalReview, markRetentionFailed, capturePresentation, retain, submit, checkSavedReviews, captureConfirmation, hasPendingForCard, retryQueueAfterReview, reconcilePreparedQueue, refreshVisibleQueue };
}

export type StudyReviewController = ReturnType<typeof useStudyReviewController>;
