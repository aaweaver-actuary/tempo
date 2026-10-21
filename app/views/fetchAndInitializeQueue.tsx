import { holdActiveStudyRequest } from "../lib/study-priority";
import { LEARNING_URL as API_URL, learningFetch as fetch, independentStudy } from "../lib/study-connection";
import { recoverStudyRequests } from "../lib/study-command-client";
import { initializeStudyIntentStorage, isStudyIntentStoreCurrent } from "../../database/browser/study-intent-storage";
import { isIPhoneHomeScreen, usesLocalApi } from "../utils/local";
import { learningDayKey as localDayKey } from "../lib/study-connection";
import { useTrainingStore } from "../state/training-store";
import { runStudyTask } from "../lib/background-study";
import type { PracticeCard } from "../domain/cards";
import { reportDebugError } from "../lib/debug-reporting";
import { conflictedReviews, flushPendingReviews, pendingReviews, ReviewReplayError, updateReviewConflictNotice } from "../lib/review-outbox";
import { describeOfflineQueue, OfflineReplayError, PhoneQueueRefreshingError, readPreparedTraining, replayOfflineAttempts, requiresConnectedGrading, savePreparedTraining, type OfflineAttempt, type PreparedTraining } from "../lib/offline-training";
import { waitForOfflineShell } from "../lib/offline-shell";
import { flushTrainingFailures, pendingTrainingFailures, pendingTrainingFailureContexts, isLegacyTrainingFailure } from "../lib/training-failure-outbox";
import { hydrateNotifications, notifications, publishNotification, resolveNotification, updateNotification, type NotificationSeverity } from "../lib/notifications";
import { attemptEntryKey } from "../domain/attempt";
import { queueEnvelopeSchema } from "../domain/schemas";
import { studyWorkspace } from "../lib/study-connection";

export function rememberConfirmedQueueEntry(card: PracticeCard): void {
  const keys = useTrainingStore.getState().confirmedQueueEntryKeys ?? [];
  useTrainingStore.setState({ confirmedQueueEntryKeys: [...keys.filter(key => key !== attemptEntryKey(card)), attemptEntryKey(card)].slice(-256) });
}

let requestGeneration = 0;
let activeQueueController: AbortController | null = null;
const queueCacheKey = independentStudy ? `tempo-training-queue-window-v2:${studyWorkspace()}` : "tempo-training-queue-window-v2";

function eligibleQueueCards(
  cards: PracticeCard[], phoneAttempts: readonly OfflineAttempt[] = [], localRepeatQueueIds: ReadonlySet<number> = new Set(),
): PracticeCard[] {
  const pendingResults = pendingReviews();
  const confirmedEntries = new Set(useTrainingStore.getState().confirmedQueueEntryKeys);
  const conflictedCardIds = new Set(conflictedReviews().map((review) => review.backendId));
  const unacknowledgedPhoneCardIds = new Set(phoneAttempts
    .filter((attempt) => !attempt.serverReviewId && !attempt.serverAcknowledged)
    .map((attempt) => attempt.cardId));
  return cards.filter((card) => {
    const backendCardId = String(card.backendId ?? card.id);
    return !confirmedEntries.has(attemptEntryKey(card)) && !pendingResults.some((review) => review.queueEntryId === card.queueEntryId && review.backendId === backendCardId) &&
      !conflictedCardIds.has(backendCardId) &&
      (!unacknowledgedPhoneCardIds.has(backendCardId) || localRepeatQueueIds.has(Number(card.queueEntryId)));
  });
}

export async function loadEligibleOfflineQueue(prepared: PreparedTraining): Promise<PracticeCard[]> {
  // Keep local reinforcement already linked to its saved parent attempt. A
  // revised or unrelated projection cannot inherit that offline continuation.
  const localRepeatQueueIds = new Set(prepared.cards.filter((card) => {
    const parent = prepared.attempts.find((attempt) => attempt.localEntryId === card.parent_local_entry_id);
    return parent && !parent.conflict && parent.cardId === card.id &&
      parent.expectedRevision === (card.revision ?? 1) && (parent.queueCycle ?? 0) + 1 === (card.cycle ?? 0) &&
      !prepared.attempts.some((attempt) => attempt.cardId === card.id && attempt.conflict);
  }).map((card) => Number(card.queue_entry_id)));
  const supportedCards = prepared.cards.filter((card) => !requiresConnectedGrading(card));
  const cards = await runStudyTask<PracticeCard[]>({
    kind: "queue", payload: { cards: supportedCards, count: supportedCards.length, local_date: prepared.localDate },
  });
  return eligibleQueueCards(retainPendingFailures(cards), prepared.attempts, localRepeatQueueIds);
}

function showQueueNotice(message: string, severity: NotificationSeverity = "info") {
  useTrainingStore.getState().setQueueNotice(message);
  if (message) publishNotification({ severity, source: "training queue", message });
}

export function showPhoneQueuePreparationNotice(message: string, severity: NotificationSeverity = "info", ready = false): void {
  useTrainingStore.getState().setQueueNotice(message);
  hydrateNotifications();
  const priorRecords = notifications().filter(record => record.key === "phone-queue-preparation" ||
    (!record.key && record.source === "training queue" && record.message.startsWith("Phone queue could not be prepared.")));
  if (ready) {
    const unresolved = priorRecords.filter(record => !record.resolvedAt);
    if (unresolved.length) {
      for (const record of unresolved) resolveNotification(record.id, { severity: "info", message });
      return;
    }
    if (priorRecords.some(record => record.message === message)) return;
  }
  publishNotification({ severity, source: "training queue", key: "phone-queue-preparation", message });
}

function showGuidedAttemptSaveNotice(error?: string | null) {
  hydrateNotifications();
  if (pendingTrainingFailures().length) {
    const message = error ?? "Guided attempt save pending. Tempo will retry.";
    useTrainingStore.getState().setQueueNotice(message);
    publishNotification({ severity: "warning", source: "training queue", key: "guided-attempt-save", message });
    return;
  }
  for (const record of notifications()) {
    const legacySaveNotice = !record.key && record.source === "training queue" &&
      (record.message.startsWith("Guided attempt save pending.") || record.message.startsWith("Could not confirm a guided attempt."));
    if (!record.resolvedAt && (record.key === "guided-attempt-save" || legacySaveNotice)) {
      if (useTrainingStore.getState().queueNotice === record.message)
        useTrainingStore.getState().setQueueNotice("");
      resolveNotification(record.id, { severity: "success", message: "Guided attempts confirmed." });
    }
  }
}

export function invalidateTrainingQueueCache(): void {
  requestGeneration += 1;
  localStorage.removeItem(queueCacheKey);
}

type QueuePayload = {
  cards?: unknown[];
  count?: number;
  local_date?: string;
  projection?: { state?: string; generation?: number; last_error?: string | null };
};

export class QueueProcessingError extends Error {}
class LegacyTrainingFailurePendingError extends QueueProcessingError {}

export type QueueInitializationResult = "installed" | "exhausted" | "superseded";

function awaitQueueStage<T>(operation: Promise<T> | T, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const cancelled = () => reject(signal.reason ?? new DOMException("Queue acquisition cancelled", "AbortError"));
    signal.addEventListener("abort", cancelled, { once: true });
    // Attach settlement handlers even after cancellation: late stage results
    // have no installation authority, and late rejections remain observed.
    Promise.resolve(operation).then(resolve, reject).finally(() => signal.removeEventListener("abort", cancelled));
    if (signal.aborted) cancelled();
  });
}

function retainPendingFailures(
  cards: PracticeCard[], failureContexts = pendingTrainingFailureContexts(),
): PracticeCard[] {
  const activeState = useTrainingStore.getState();
  const activeCard = activeState.isDatabaseQueueActive ? activeState.getCard() : undefined;
  const currentFailureContexts = pendingTrainingFailureContexts();
  const unresolvedLegacy = failureContexts.find(failure => {
    if (!isLegacyTrainingFailure(failure)) return false;
    const queuedCard = cards.find(card => card.queueEntryId === failure.queueEntryId);
    if (!queuedCard && activeCard?.queueEntryId !== failure.queueEntryId) return false;
    // The request snapshot stays conservative if the queue response raced replay.
    // Confirmed authoritative replay plus a server-guided queue can settle it now.
    return currentFailureContexts.some(pending => pending.operationId === failure.operationId) ||
      queuedCard?.attemptFailed !== true;
  });
  if (unresolvedLegacy) {
    // A queue-only saved marker is not evidence about whichever card now occupies
    // that entry. Only authoritative replay followed by a fresh queue read can resolve it.
    const message = `Guided attempt identity is pending for queue entry ${unresolvedLegacy.queueEntryId}. Reconnect and retry loading the queue to validate the saved failure.`;
    activeState.setServiceError(message);
    activeState.setOfflineQueue(false);
    throw new LegacyTrainingFailurePendingError(message);
  }
  return cards.map(card => card.queueEntryId && failureContexts.some(failure =>
    failure.queueEntryId === card.queueEntryId && failure.backendId === String(card.backendId ?? card.id) &&
    failure.expectedRevision === card.revision) ? { ...card, attemptFailed: true } : card);
}

async function fetchQueueWindow(signal: AbortSignal, observePreparation = false): Promise<{ response: Response; payload: unknown }> {
  const controller = new AbortController();
  const abortSupersededRequest = () => controller.abort();
  if (signal.aborted) abortSupersededRequest();
  else signal.addEventListener("abort", abortSupersededRequest, { once: true });
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 15_000);
  try {
    const response = await awaitQueueStage(fetch(`${API_URL}/api/queue/window?limit=20&include_opening_evidence=true`, {
      signal: controller.signal,
      ...(observePreparation ? { headers: { "X-Tempo-Work-Class": "background" } } : {}),
    }), controller.signal);
    let payload: unknown;
    try { payload = await awaitQueueStage(response.json(), controller.signal); }
    catch (error) {
      if (controller.signal.aborted || (error instanceof Error && error.name === "AbortError")) throw error;
      if (response.ok) throw new QueueProcessingError(`The local queue returned invalid JSON. ${String(error)}`);
    }
    return { response, payload };
  } catch (error) {
    if (timedOut)
      throw new Error("Queue request timed out after 15 seconds. Retry loading the queue.", { cause: error });
    throw error;
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener("abort", abortSupersededRequest);
  }
}

function waitForQueueRetry(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return; }
    const onAbort = () => {
      clearTimeout(timeout);
      reject(signal.reason);
    };
    const timeout = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function loadTodayQueueWithRetry(signal: AbortSignal): Promise<QueuePayload> {
  let failedRequests = 0;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const { response, payload: responsePayload } = await fetchQueueWindow(signal, attempt > 0);
    if (response.ok) {
      failedRequests = 0;
      const payload = responsePayload as QueuePayload;
      if (!Array.isArray(payload?.cards))
        throw new QueueProcessingError("The local queue returned an invalid queue response. Retry loading the queue.");
      if (payload.projection?.state === "failed")
        throw new QueueProcessingError(payload.projection.last_error ?? "Daily queue refresh failed");
      if (payload.projection?.state !== "refreshing" || payload.cards.length) {
        const validated = queueEnvelopeSchema.safeParse(responsePayload);
        if (!validated.success) throw new QueueProcessingError("The local queue returned an invalid queue response. Retry loading the queue.");
        return validated.data;
      }
      if (attempt === 19) throw new QueueProcessingError("Daily queue is still preparing. Check the analysis worker and retry.");
      await waitForQueueRetry(250, signal);
      continue;
    }

    let detail = `HTTP ${response.status}`;
    let retryable = response.status === 503;
    try {
      const payload = responsePayload as {
        detail?: unknown;
        retryable?: unknown;
      };
      if (typeof payload.detail === "string") detail = payload.detail;
      retryable = payload.retryable === true || retryable;
    } catch {
      // Keep the stable HTTP fallback when the service returned no JSON body.
    }
    if (response.status === 503 && detail === "Waiting for foreground activity") {
      await waitForQueueRetry(250, signal);
      continue;
    }
    failedRequests += 1;
    if (!retryable) throw new QueueProcessingError(detail);
    if (failedRequests >= 3) throw new Error(detail);
    await waitForQueueRetry(250 * (attempt + 1), signal);
  }
  throw new Error("The local queue could not be loaded.");
}

export async function fetchAndInitializeQueue(
  advance = false,
  options: { preparePhoneQueue?: boolean; replaySavedReviews?: boolean; preservePreparedAttempt?: boolean;
    timeoutMs?: number; signal?: AbortSignal; expectedAttempt?: { attemptId?: string; generation: number } } = {},
): Promise<QueueInitializationResult> {
  if (!usesLocalApi()) return "superseded";
  const generation = ++requestGeneration;
  activeQueueController?.abort();
  const controller = new AbortController();
  activeQueueController = controller;
  const cancelAcquisition = () => controller.abort(options.signal?.reason);
  options.signal?.addEventListener("abort", cancelAcquisition, { once: true });
  if (options.signal?.aborted) cancelAcquisition();
  const deadline = options.timeoutMs === undefined ? undefined : setTimeout(() => controller.abort(
    new Error("Queue acquisition timed out after 15 seconds. Retry loading the queue.")), options.timeoutMs);
  const expectedAttemptMatches = () => !options.expectedAttempt ||
    (useTrainingStore.getState().attempt.attemptId === options.expectedAttempt.attemptId &&
      useTrainingStore.getState().attempt.generation === options.expectedAttempt.generation);
  if (!options.preservePreparedAttempt) useTrainingStore.setState({ queueReadiness: "loading" });
  let queueInitialized = false;
  let failedOperation = "process today queue";
  let failedEndpoint: string | undefined;
  let queueRequestFailed = false;
  const releasePriority = holdActiveStudyRequest();
  try {
    const intentStore = await awaitQueueStage(initializeStudyIntentStorage(), controller.signal);
    if (options.replaySavedReviews !== false) await awaitQueueStage(recoverStudyRequests(controller.signal), controller.signal);
    const pendingFailureContexts = pendingTrainingFailureContexts();
    const pendingFailureEntries = new Set(pendingFailureContexts.map((item) => item.queueEntryId));
    let failureSaveError: string | null = null;
    try { retainPendingFailures([], pendingFailureContexts); }
    catch (error) { if (!(error instanceof LegacyTrainingFailurePendingError)) throw error; }
    const savedPreparedQueue = typeof indexedDB === "undefined" ? null : await awaitQueueStage(readPreparedTraining().catch(() => null), controller.signal);
    if (isIPhoneHomeScreen() && !useTrainingStore.getState().isDatabaseQueueActive && !pendingReviews().length &&
        savedPreparedQueue?.localDate !== localDayKey()) {
      const stored = localStorage.getItem(queueCacheKey);
      if (stored) {
        try {
          const cached = JSON.parse(stored) as QueuePayload;
          if (cached.local_date === localDayKey() && cached.cards?.length) {
            const cachedCards = await awaitQueueStage(runStudyTask<PracticeCard[]>({ kind: "queue", payload: cached }, controller.signal), controller.signal);
            if (generation === requestGeneration && expectedAttemptMatches() && !controller.signal.aborted) {
              const availableCards = eligibleQueueCards(retainPendingFailures(cachedCards, pendingFailureContexts), savedPreparedQueue?.attempts);
              useTrainingStore.getState().hydrateLocalQueue(
                availableCards, false, Math.max(0, (cached.count ?? cachedCards.length) - (cachedCards.length - availableCards.length)),
              );
            }
          }
        } catch (error) {
          if (!(error instanceof LegacyTrainingFailurePendingError)) localStorage.removeItem(queueCacheKey);
        }
      }
    }
    let replayed: Awaited<ReturnType<typeof replayOfflineAttempts>> | null = null;
    let pendingReviewError = "";
    if (typeof indexedDB !== "undefined" && options.replaySavedReviews !== false) {
      try {
        if (savedPreparedQueue?.attempts.some((attempt) => !attempt.serverReviewId && !attempt.serverAcknowledged && !attempt.conflict))
          publishNotification({ severity: "info", source: "phone review sync", key: "phone-review-syncing",
            message: "Syncing reviews saved on this phone with the computer." });
        replayed = await awaitQueueStage(replayOfflineAttempts(), controller.signal);
        const syncing = notifications().find((record) => record.key === "phone-review-syncing" && !record.resolvedAt);
        if (syncing) {
          if (replayed?.attempts.some((attempt) => attempt.conflict))
            updateNotification(syncing.id, { severity: "warning", active: false,
              message: "Phone review sync needs attention. The unresolved attempts remain saved on this phone." });
          else resolveNotification(syncing.id, { severity: "success", message: "Phone review sync finished." });
        }
        for (const attempt of replayed?.attempts ?? []) {
          const previouslySaved = savedPreparedQueue?.attempts.find((item) => item.localEntryId === attempt.localEntryId);
          if (!previouslySaved || previouslySaved.serverReviewId || previouslySaved.serverAcknowledged || attempt.conflict ||
              (!attempt.serverReviewId && !attempt.serverAcknowledged)) continue;
          publishNotification({ severity: "success", source: "phone review sync",
            key: `phone-review-credited:${attempt.localEntryId}:${attempt.completedAt}`,
            message: `Phone review confirmed by the computer: ${attempt.outcome} at ${attempt.completedAt}.`,
            details: { cardId: attempt.cardId, outcome: attempt.outcome, completedAt: attempt.completedAt },
          });
        }
      } catch (error) {
        pendingReviewError = error instanceof Error ? error.message : String(error);
        const syncing = notifications().find((record) => record.key === "phone-review-syncing" && !record.resolvedAt);
        if (syncing) updateNotification(syncing.id, { severity: "warning", active: false,
          message: "Phone review sync paused. The saved attempts remain on this phone; reconnect and retry sync." });
        if (generation === requestGeneration) reportDebugError(error, {
          kind: "api", source: "training-offline-review-replay", operation: "replay saved offline reviews",
          endpoint: error instanceof OfflineReplayError ? error.endpoint : undefined,
        });
      }
    }
    if (options.replaySavedReviews === false && pendingReviews().length)
      pendingReviewError = "Previously saved reviews are waiting for confirmation.";
    if (options.replaySavedReviews !== false && pendingReviews().length) {
      try {
        await awaitQueueStage(flushPendingReviews(Infinity, true), controller.signal);
      } catch (error) {
        pendingReviewError = error instanceof Error ? error.message : String(error);
        if (generation === requestGeneration)
          reportDebugError(error, {
            kind: "api",
            source: "training-review-replay", notify: false,
            operation: "save pending review",
            endpoint: error instanceof ReviewReplayError ? error.endpoint : `${API_URL}/api/cards/review`,
            ...(error instanceof ReviewReplayError ? { cardId: error.backendId, queueEntryId: error.queueEntryId,
              attemptId: error.attemptId, status: error.status, code: error.code, retryable: error.retryable,
              classification: error.classification } : {}),
          });
      }
    }
    if (!pendingReviewError && pendingReviews().some(review => review.automaticRecoverySuppressed))
      pendingReviewError = "Previously saved reviews need explicit attention before retrying.";
    updateReviewConflictNotice();
    if (pendingFailureEntries.size)
      void flushTrainingFailures().then(() => {
        if (generation === requestGeneration && isStudyIntentStoreCurrent(intentStore)) showGuidedAttemptSaveNotice();
      }).catch((error) => {
        failureSaveError = `Could not confirm a guided attempt. Refresh the training queue. ${String(error)}`;
        if (generation === requestGeneration && isStudyIntentStoreCurrent(intentStore)) showGuidedAttemptSaveNotice(failureSaveError);
      });
    let raw: QueuePayload;
    try {
      raw = await awaitQueueStage(loadTodayQueueWithRetry(controller.signal), controller.signal);
    } catch (error) {
      queueRequestFailed = !(error instanceof QueueProcessingError);
      failedOperation = queueRequestFailed ? "load today queue" : "process today queue response";
      failedEndpoint = `${API_URL}/api/queue/window?limit=20`;
      throw error;
    }
    const processingStarted = performance.now();
    const cards = await awaitQueueStage(runStudyTask<PracticeCard[]>({
      kind: "queue",
      payload: raw,
    }, controller.signal), controller.signal);
    performance.measure("tempo.queue.processing", { start: processingStarted, end: performance.now(), detail: { generation } });
    if (generation !== requestGeneration || !expectedAttemptMatches()) return "superseded";
    if (controller.signal.aborted) throw controller.signal.reason;
    const rawHead = raw.cards?.[0] as { id?: unknown; queue_entry_id?: unknown } | undefined;
    if ((rawHead && (!cards[0] || cards[0].backendId !== rawHead.id || cards[0].queueEntryId !== rawHead.queue_entry_id)) ||
        (!rawHead && (raw.count ?? 0) > 0) || (raw.count !== undefined && raw.count < (raw.cards?.length ?? 0)))
      throw new QueueProcessingError("The authoritative queue head could not be prepared. Retry loading the queue.");
    const availableCards = eligibleQueueCards(retainPendingFailures(cards, pendingFailureContexts), (replayed ?? savedPreparedQueue)?.attempts);
    const returnedHead = cards[0];
    const headExcludedByConnectedReview = returnedHead && (
      pendingReviews().some(review => review.queueEntryId === returnedHead.queueEntryId &&
        review.backendId === String(returnedHead.backendId ?? returnedHead.id)) ||
      conflictedReviews().some(review => review.backendId === String(returnedHead.backendId ?? returnedHead.id)) ||
      useTrainingStore.getState().confirmedQueueEntryKeys?.includes(attemptEntryKey(returnedHead)));
    if (!options.preservePreparedAttempt && headExcludedByConnectedReview) {
      // Only the returned head was issued. Filtering a connected saved result
      // cannot authorize another prefetched successor. Offline phone attempts
      // retain their existing prepared-queue authorization.
      useTrainingStore.getState().setPendingReviewError(pendingReviewError ||
        "The authoritative queue head has unresolved local results. Check saved reviews and Notifications, then retry loading the queue.");
      throw new QueueProcessingError("The authoritative queue head is excluded by local review state. Check saved reviews and Notifications, then retry loading the queue.");
    }
    useTrainingStore.setState({ queueLocalDate: raw.local_date });
    if (options.preservePreparedAttempt) {
      // Reconciliation updates upcoming data, never authorizes advancement or
      // creates another attempt when the prepared board has just completed.
      const retainedAttempt = useTrainingStore.getState();
      const activeCard = retainedAttempt.getCard();
      const matchingActiveCard = availableCards.find(candidate => attemptEntryKey(candidate) === retainedAttempt.attempt.entryKey);
      const retainedActiveCard = matchingActiveCard ? { ...activeCard, priorityReason: matchingActiveCard.priorityReason,
        attemptFailed: activeCard.attemptFailed || matchingActiveCard.attemptFailed } : activeCard;
      const upcoming = availableCards.filter(candidate => attemptEntryKey(candidate) !== retainedAttempt.attempt.entryKey);
      const activeStillQueued = availableCards.some(candidate => attemptEntryKey(candidate) === retainedAttempt.attempt.entryKey);
      useTrainingStore.setState({
        practiceCards: [retainedActiveCard, ...upcoming], activeCardIndex: 0,
        dailyQueue: [retainedActiveCard, ...upcoming].map((_, index) => index),
        cardsLeft: Math.max(0, (raw.count ?? cards.length) - (cards.length - availableCards.length)) + Number(!activeStillQueued),
        queueLocalDate: raw.local_date, serviceError: "" });
      if (matchingActiveCard?.attemptFailed && !["feedbackPause", "complete"].includes(retainedAttempt.attempt.phase)) {
        retainedAttempt.setAttemptFailed(true);
        retainedAttempt.setShowHint(true);
        retainedAttempt.setFailureFen(retainedAttempt.failureFen ?? retainedAttempt.currentFenString);
      }
    } else useTrainingStore.getState().hydrateLocalQueue(
      availableCards, advance, Math.max(0, (raw.count ?? cards.length) - (cards.length - availableCards.length)),
    );
    useTrainingStore.getState().setPendingReviewError(pendingReviewError);
    useTrainingStore.getState().setOfflineQueue(false);
    queueInitialized = true;
    try { localStorage.setItem(queueCacheKey, JSON.stringify(raw)); } catch {
      // A full browser storage quota must not turn a successful queue read into a failure.
    }
    const conflicts = replayed?.attempts.filter((attempt) => attempt.conflict) ?? [];
    const creditedWarnings = replayed?.attempts.filter((attempt) => attempt.syncWarning) ?? [];
    for (const attempt of creditedWarnings) {
      publishNotification({ severity: "warning", source: "phone review sync",
        key: `phone-review-warning:${attempt.localEntryId}:${attempt.completedAt}`,
        message: `${attempt.syncWarning} Phone: ${attempt.outcome} at ${attempt.completedAt}. Card: ${attempt.cardId}.`,
        details: { cardId: attempt.cardId, outcome: attempt.outcome, completedAt: attempt.completedAt },
      });
    }
    showGuidedAttemptSaveNotice(failureSaveError);
    if (conflicts.length) {
      publishNotification({ severity: "warning", source: "phone review sync", key: "phone-review-conflicts",
        message: `${conflicts.length} ${isIPhoneHomeScreen() ? "phone" : "offline"} review(s) remain saved ${isIPhoneHomeScreen() ? "on this phone" : "in this browser"} and need attention. Open details for each result and reason, then reconnect and retry sync.`,
        details: { cardIds: conflicts.map((attempt) => attempt.cardId),
          attempts: conflicts.map((attempt) =>
            `${attempt.cardId}: ${attempt.outcome} at ${attempt.completedAt}; ${attempt.conflict}`) },
      });
      useTrainingStore.getState().setQueueNotice("");
    } else {
      const priorConflict = notifications().find((record) => record.key === "phone-review-conflicts" && !record.resolvedAt);
      if (priorConflict) resolveNotification(priorConflict.id, { severity: "success", message: "Phone review conflicts cleared." });
      if (!pendingTrainingFailures().length) useTrainingStore.getState().setQueueNotice("");
    }
    const hasConflicts = Boolean(replayed?.attempts.some((attempt) => attempt.conflict));
    if ((independentStudy || isIPhoneHomeScreen()) && typeof indexedDB !== "undefined" && options.preparePhoneQueue !== false) void fetch(`${API_URL}/api/queue/prepared?include_opening_evidence=true`, { signal: controller.signal })
      .then(async (response) => {
        if (response.status === 404)
          throw new Error("Tempo on the computer is an older version. Update it, then reopen Tempo on the phone.");
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json() as QueuePayload & { prepared_at: string };
        if (generation !== requestGeneration || !expectedAttemptMatches() || controller.signal.aborted) return null;
        const prepared = await savePreparedTraining(payload);
        if (generation !== requestGeneration || !expectedAttemptMatches() || controller.signal.aborted) return null;
        if (prepared.preparedAt !== payload.prepared_at || prepared.localDate !== payload.local_date)
          throw new Error(prepared.attempts.some((attempt) => !attempt.serverReviewId && !attempt.conflict)
            ? "Saved phone reviews are still waiting to sync. The older offline queue was kept."
            : "A newer phone queue is already saved. Retry when the live queue is available.");
        const liveWindowMatchesPrepared = raw.local_date === prepared.localDate &&
          raw.count === prepared.cards.length && Array.isArray(raw.cards) &&
          raw.cards.every((windowCard, index) =>
            typeof windowCard === "object" && windowCard !== null &&
            (windowCard as { queue_entry_id?: unknown }).queue_entry_id === prepared.cards[index]?.queue_entry_id);
        if (liveWindowMatchesPrepared &&
            !useTrainingStore.getState().serviceError.includes("no longer in today's queue")) {
          const completeCards = await runStudyTask<PracticeCard[]>({ kind: "queue", payload: {
            cards: payload.cards, count: payload.count, local_date: payload.local_date,
            projection: payload.projection,
          } }, controller.signal);
          if (generation !== requestGeneration || !expectedAttemptMatches() || controller.signal.aborted) return null;
          const availableCards = eligibleQueueCards(retainPendingFailures(completeCards, pendingFailureContexts), prepared.attempts);
          useTrainingStore.getState().hydrateLocalQueue(
            availableCards, false, Math.max(0, prepared.cards.length - (completeCards.length - availableCards.length)),
          );
        }
        return prepared;
      })
      .then(async (prepared) => {
        if (!prepared) return;
        await waitForOfflineShell();
        if (generation === requestGeneration && !hasConflicts && prepared.localDate === localDayKey() &&
            !useTrainingStore.getState().serviceError.includes("no longer in today's queue"))
          showPhoneQueuePreparationNotice(
            `Phone queue prepared for ${prepared.localDate}.` +
            (prepared.cards.some(requiresConnectedGrading)
              ? ` ${prepared.cards.filter(requiresConnectedGrading).length} exercise(s) still require the computer.`
              : ""), "info", true,
          );
      })
      .catch((error) => {
        if (generation === requestGeneration && !hasConflicts && !controller.signal.aborted)
          showPhoneQueuePreparationNotice(
            error instanceof PhoneQueueRefreshingError ? error.message :
              `Phone queue could not be prepared. ${error instanceof Error ? error.message : "Keep the computer connected and retry loading the queue."}`,
            error instanceof PhoneQueueRefreshingError ? "info" : "warning",
          );
      });
    return availableCards.length ? "installed" : "exhausted";
  } catch (error) {
    if (generation !== requestGeneration || !expectedAttemptMatches()) return "superseded";
    reportDebugError(error, {
      kind: "api", source: queueRequestFailed ? "training-queue" : "training-queue-processing",
      operation: failedOperation, endpoint: failedEndpoint,
    });
    if (options.preservePreparedAttempt) throw error;
    const prepared = queueRequestFailed && (independentStudy || isIPhoneHomeScreen())
      ? await awaitQueueStage(readPreparedTraining().catch(() => null), controller.signal) : null;
    if (prepared?.localDate === localDayKey()) {
      try {
        await awaitQueueStage(waitForOfflineShell(), controller.signal);
      } catch (shellError) {
        // A saved queue is usable offline only with the current verified app shell.
        useTrainingStore.getState().setServiceError(
          `The local queue could not be loaded. Your active attempt is retained. ${String(error)} ${String(shellError)}`,
        );
        throw error;
      }
      const availableCards = await awaitQueueStage(loadEligibleOfflineQueue(prepared), controller.signal);
      if (generation !== requestGeneration || !expectedAttemptMatches()) return "superseded";
      useTrainingStore.getState().hydrateLocalQueue(availableCards, advance, availableCards.length);
      useTrainingStore.getState().setOfflineQueue(true);
      queueInitialized = true;
      showQueueNotice(`${describeOfflineQueue(prepared)} Live service: ${String(error)}. Retry sync when connected.`, "warning");
      return availableCards.length ? "installed" : "exhausted";
    }
    useTrainingStore
      .getState()
      .setServiceError(
        `The local queue could not be loaded. Your active attempt is retained. ${String(error)}`,
      );
    throw error;
  } finally {
    releasePriority();
    clearTimeout(deadline);
    options.signal?.removeEventListener("abort", cancelAcquisition);
    if (generation === requestGeneration && (queueInitialized || expectedAttemptMatches()))
      useTrainingStore.setState({ queueReadiness: queueInitialized || options.preservePreparedAttempt ? "ready" : "unavailable" });
    if (activeQueueController === controller) activeQueueController = null;
  }
}
