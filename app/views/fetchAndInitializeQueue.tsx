import { API_URL } from "../const";
import { isIPhoneHomeScreen, localDayKey, usesLocalApi } from "../utils/local";
import { useTrainingStore } from "../state/training-store";
import { runStudyTask } from "../lib/background-study";
import type { PracticeCard } from "../domain/cards";
import { reportDebugError } from "../lib/debug-reporting";
import { flushPendingReviews, pendingReviews, ReviewReplayError } from "../lib/review-outbox";
import { describeOfflineQueue, OfflineReplayError, readPreparedTraining, replayOfflineAttempts, requiresConnectedGrading, savePreparedTraining } from "../lib/offline-training";
import { waitForOfflineShell } from "../lib/offline-shell";
import { flushTrainingFailures, pendingTrainingFailures } from "../lib/training-failure-outbox";
import { hydrateNotifications, notifications, publishNotification, resolveNotification, updateNotification, type NotificationSeverity } from "../lib/notifications";

let requestGeneration = 0;
let activeQueueController: AbortController | null = null;
const queueCacheKey = "tempo-training-queue-window-v2";

function showQueueNotice(message: string, severity: NotificationSeverity = "info") {
  useTrainingStore.getState().setQueueNotice(message);
  if (message) publishNotification({ severity, source: "training queue", message });
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
  projection?: { state?: string; generation?: number; last_error?: string };
};

class QueueProcessingError extends Error {}

async function fetchQueueWindow(signal: AbortSignal): Promise<Response> {
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
    return await fetch(`${API_URL}/api/queue/window?limit=20&include_opening_evidence=true`, { signal: controller.signal });
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
    const response = await fetchQueueWindow(signal);
    if (response.ok) {
      failedRequests = 0;
      let payload: QueuePayload;
      try {
        payload = await response.json() as QueuePayload;
      } catch (error) {
        throw new QueueProcessingError(`The local queue returned invalid JSON. ${String(error)}`);
      }
      if (payload.projection?.state === "failed")
        throw new QueueProcessingError(payload.projection.last_error ?? "Daily queue refresh failed");
      if (payload.projection?.state !== "refreshing" || payload.cards?.length)
        return payload;
      if (attempt === 19) throw new QueueProcessingError("Daily queue is still preparing. Check the analysis worker and retry.");
      await waitForQueueRetry(250, signal);
      continue;
    }

    let detail = `HTTP ${response.status}`;
    let retryable = response.status === 503;
    try {
      const payload = (await response.json()) as {
        detail?: unknown;
        retryable?: unknown;
      };
      if (typeof payload.detail === "string") detail = payload.detail;
      retryable = payload.retryable === true || retryable;
    } catch {
      // Keep the stable HTTP fallback when the service returned no JSON body.
    }
    failedRequests += 1;
    if (!retryable || failedRequests >= 3) throw new Error(detail);
    await waitForQueueRetry(250 * (attempt + 1), signal);
  }
  throw new Error("The local queue could not be loaded.");
}

export async function fetchAndInitializeQueue(
  advance = false,
  options: { preparePhoneQueue?: boolean } = {},
): Promise<void> {
  if (!usesLocalApi()) return;
  const generation = ++requestGeneration;
  activeQueueController?.abort();
  const controller = new AbortController();
  activeQueueController = controller;
  useTrainingStore.setState({ queueReadiness: "loading" });
  let queueInitialized = false;
  let failedOperation = "process today queue";
  let failedEndpoint: string | undefined;
  let queueRequestFailed = false;
  try {
    let pendingOfflineCardIds = new Set<string>();
    const withoutPendingReviews = (cards: PracticeCard[]) => {
      const pendingEntryIds = new Set(pendingReviews().map((review) => review.queueEntryId));
      return cards.filter((card) =>
        (!card.queueEntryId || !pendingEntryIds.has(card.queueEntryId)) &&
        !pendingOfflineCardIds.has(String(card.backendId ?? card.id)));
    };
    const pendingFailureEntries = new Set(pendingTrainingFailures());
    let failureSaveError: string | null = null;
    const retainPendingFailures = (cards: PracticeCard[]) => cards.map((card) =>
      card.queueEntryId && pendingFailureEntries.has(card.queueEntryId)
        ? { ...card, attemptFailed: true } : card);
    const savedPreparedQueue = typeof indexedDB === "undefined" ? null : await readPreparedTraining().catch(() => null);
    if (isIPhoneHomeScreen() && !useTrainingStore.getState().isDatabaseQueueActive && !pendingReviews().length &&
        savedPreparedQueue?.localDate !== localDayKey()) {
      const stored = localStorage.getItem(queueCacheKey);
      if (stored) {
        try {
          const cached = JSON.parse(stored) as QueuePayload;
          if (cached.local_date === localDayKey() && cached.cards?.length) {
            const cachedCards = await runStudyTask<PracticeCard[]>({ kind: "queue", payload: cached });
            if (generation === requestGeneration) {
              const availableCards = withoutPendingReviews(retainPendingFailures(cachedCards));
              useTrainingStore.getState().hydrateLocalQueue(
                availableCards, false, Math.max(0, (cached.count ?? cachedCards.length) - (cachedCards.length - availableCards.length)),
              );
            }
          }
        } catch {
          localStorage.removeItem(queueCacheKey);
        }
      }
    }
    let replayed: Awaited<ReturnType<typeof replayOfflineAttempts>> | null = null;
    let pendingReviewError = "";
    if (typeof indexedDB !== "undefined") {
      try {
        if (savedPreparedQueue?.attempts.some((attempt) => !attempt.serverReviewId && !attempt.serverAcknowledged && !attempt.conflict))
          publishNotification({ severity: "info", source: "phone review sync", key: "phone-review-syncing",
            message: "Syncing reviews saved on this phone with the computer." });
        replayed = await replayOfflineAttempts();
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
    pendingOfflineCardIds = new Set((replayed ?? savedPreparedQueue)?.attempts
      .filter((attempt) => !attempt.serverReviewId && !attempt.serverAcknowledged)
      .map((attempt) => attempt.cardId) ?? []);
    if (pendingReviews().length) {
      try {
        await flushPendingReviews();
      } catch (error) {
        pendingReviewError = error instanceof Error ? error.message : String(error);
        if (generation === requestGeneration)
          reportDebugError(error, {
            kind: "api",
            source: "training-review-replay",
            operation: "save pending review",
            endpoint: error instanceof ReviewReplayError ? error.endpoint : `${API_URL}/api/cards/review`,
          });
      }
    }
    if (pendingFailureEntries.size)
      void flushTrainingFailures().then(() => {
        if (generation === requestGeneration) showGuidedAttemptSaveNotice();
      }).catch((error) => {
        failureSaveError = `Could not confirm a guided attempt. Refresh the training queue. ${String(error)}`;
        if (generation === requestGeneration) showGuidedAttemptSaveNotice(failureSaveError);
      });
    let raw: QueuePayload;
    try {
      raw = await loadTodayQueueWithRetry(controller.signal);
    } catch (error) {
      queueRequestFailed = !(error instanceof QueueProcessingError);
      failedOperation = queueRequestFailed ? "load today queue" : "process today queue response";
      failedEndpoint = `${API_URL}/api/queue/window?limit=20`;
      throw error;
    }
    const cards = await runStudyTask<PracticeCard[]>({
      kind: "queue",
      payload: raw,
    });
    if (generation !== requestGeneration) return;
    const availableCards = withoutPendingReviews(retainPendingFailures(cards));
    useTrainingStore.getState().hydrateLocalQueue(
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
    if (isIPhoneHomeScreen() && typeof indexedDB !== "undefined" && options.preparePhoneQueue !== false) void fetch(`${API_URL}/api/queue/prepared?include_opening_evidence=true`, { signal: controller.signal })
      .then(async (response) => {
        if (response.status === 404)
          throw new Error("Tempo on the computer is an older version. Update it, then reopen Tempo on the phone.");
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json() as QueuePayload & { prepared_at: string };
        if (generation !== requestGeneration) return null;
        const prepared = await savePreparedTraining(payload);
        if (generation !== requestGeneration) return null;
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
          } });
          if (generation !== requestGeneration) return null;
          const availableCards = withoutPendingReviews(retainPendingFailures(completeCards));
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
          showQueueNotice(
            `Phone queue prepared for ${prepared.localDate}.` +
            (prepared.cards.some(requiresConnectedGrading)
              ? ` ${prepared.cards.filter(requiresConnectedGrading).length} exercise(s) still require the computer.`
              : ""),
          );
      })
      .catch((error) => {
        if (generation === requestGeneration && !hasConflicts && !controller.signal.aborted)
          showQueueNotice(
            `Phone queue could not be prepared. ${error instanceof Error ? error.message : "Keep the computer connected and retry loading the queue."}`,
            "warning",
          );
      });
  } catch (error) {
    if (generation !== requestGeneration) return;
    reportDebugError(error, {
      kind: "api", source: queueRequestFailed ? "training-queue" : "training-queue-processing",
      operation: failedOperation, endpoint: failedEndpoint,
    });
    const prepared = queueRequestFailed && isIPhoneHomeScreen()
      ? await readPreparedTraining().catch(() => null) : null;
    if (prepared?.localDate === localDayKey()) {
      try {
        await waitForOfflineShell();
      } catch (shellError) {
        // A saved queue is usable offline only with the current verified app shell.
        useTrainingStore.getState().setServiceError(
          `The local queue could not be loaded. Your active attempt is retained. ${String(error)} ${String(shellError)}`,
        );
        throw error;
      }
      const pendingEntryIds = new Set(pendingReviews().map((review) => review.queueEntryId));
      const supportedCards = prepared.cards.filter((card) =>
        !requiresConnectedGrading(card) &&
        (!card.queue_entry_id || !pendingEntryIds.has(card.queue_entry_id)));
      const cards = await runStudyTask<PracticeCard[]>({
        kind: "queue", payload: { cards: supportedCards, count: supportedCards.length, local_date: prepared.localDate },
      });
      if (generation !== requestGeneration) return;
      useTrainingStore.getState().hydrateLocalQueue(cards, advance, cards.length);
      useTrainingStore.getState().setOfflineQueue(true);
      queueInitialized = true;
      showQueueNotice(`${describeOfflineQueue(prepared)} Live service: ${String(error)}. Retry sync when connected.`, "warning");
      return;
    }
    useTrainingStore
      .getState()
      .setServiceError(
        `The local queue could not be loaded. Your active attempt is retained. ${String(error)}`,
      );
    throw error;
  } finally {
    if (generation === requestGeneration)
      useTrainingStore.setState({ queueReadiness: queueInitialized ? "ready" : "unavailable" });
    if (activeQueueController === controller) activeQueueController = null;
  }
}
