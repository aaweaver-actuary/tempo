import { API_URL } from "../const";
import { localDayKey, usesLocalApi } from "../utils/local";
import { useTrainingStore } from "../state/training-store";
import { runStudyTask } from "../lib/background-study";
import type { PracticeCard } from "../domain/cards";
import { reportDebugError } from "../lib/debug-reporting";
import { flushPendingReviews, pendingReviews } from "../lib/review-outbox";
import { readPreparedTraining, replayOfflineAttempts, requiresConnectedGrading, savePreparedTraining } from "../lib/offline-training";
import { waitForOfflineShell } from "../lib/offline-shell";
import { flushTrainingFailures, pendingTrainingFailures } from "../lib/training-failure-outbox";

let requestGeneration = 0;
let activeQueueController: AbortController | null = null;
const queueCacheKey = "tempo-training-queue-window-v2";

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
    return await fetch(`${API_URL}/api/queue/window?limit=20`, { signal: controller.signal });
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
      const payload = await response.json() as QueuePayload;
      if (payload.projection?.state === "failed")
        throw new Error(payload.projection.last_error ?? "Daily queue refresh failed");
      if (payload.projection?.state !== "refreshing" || payload.cards?.length)
        return payload;
      if (attempt === 19) throw new Error("Daily queue is still preparing. Check the analysis worker and retry.");
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

export async function fetchAndInitializeQueue(advance = false): Promise<void> {
  if (!usesLocalApi()) return;
  const generation = ++requestGeneration;
  activeQueueController?.abort();
  const controller = new AbortController();
  activeQueueController = controller;
  try {
    const pendingFailureEntries = new Set(pendingTrainingFailures());
    let failureSaveError: string | null = null;
    if (pendingFailureEntries.size)
      void flushTrainingFailures().catch((error) => {
        failureSaveError = `Could not confirm a guided attempt. Refresh the training queue. ${String(error)}`;
        useTrainingStore.getState().setQueueNotice(
          failureSaveError,
        );
      });
    const retainPendingFailures = (cards: PracticeCard[]) => cards.map((card) =>
      card.queueEntryId && pendingFailureEntries.has(card.queueEntryId)
        ? { ...card, attemptFailed: true } : card);
    const savedPhoneQueue = typeof indexedDB === "undefined" ? null : await readPreparedTraining().catch(() => null);
    if (!useTrainingStore.getState().isDatabaseQueueActive && !pendingReviews().length &&
        savedPhoneQueue?.localDate !== localDayKey()) {
      const stored = localStorage.getItem(queueCacheKey);
      if (stored) {
        try {
          const cached = JSON.parse(stored) as QueuePayload;
          if (cached.local_date === localDayKey() && cached.cards?.length) {
            const cachedCards = await runStudyTask<PracticeCard[]>({ kind: "queue", payload: cached });
            if (generation === requestGeneration)
              useTrainingStore.getState().hydrateLocalQueue(retainPendingFailures(cachedCards), false, cached.count);
          }
        } catch {
          localStorage.removeItem(queueCacheKey);
        }
      }
    }
    const replayed = typeof indexedDB === "undefined" ? null : await replayOfflineAttempts();
    if (pendingReviews().length) await flushPendingReviews();
    const raw = await loadTodayQueueWithRetry(controller.signal);
    const cards = await runStudyTask<PracticeCard[]>({
      kind: "queue",
      payload: raw,
    });
    if (generation !== requestGeneration) return;
    useTrainingStore.getState().hydrateLocalQueue(retainPendingFailures(cards), advance, raw.count);
    useTrainingStore.getState().setOfflineQueue(false);
    try { localStorage.setItem(queueCacheKey, JSON.stringify(raw)); } catch {
      // A full browser storage quota must not turn a successful queue read into a failure.
    }
    useTrainingStore.getState().setServiceError("");
    if (replayed?.attempts.some((attempt) => attempt.conflict))
      useTrainingStore.getState().setQueueNotice(
        `${replayed.attempts.filter((attempt) => attempt.conflict).length} phone review conflict(s) remain saved on this phone: ${replayed.attempts.filter((attempt) => attempt.conflict).map((attempt) => attempt.cardId).join(", ")}. The computer's saved results take priority.`,
      );
    else useTrainingStore.getState().setQueueNotice(failureSaveError ??
      (pendingTrainingFailures().length ? "Guided attempt save pending. Tempo will retry." : ""));
    const hasConflicts = Boolean(replayed?.attempts.some((attempt) => attempt.conflict));
    if (typeof indexedDB !== "undefined") void fetch(`${API_URL}/api/queue/prepared`)
      .then(async (response) => {
        if (response.status === 404)
          throw new Error("Tempo on the computer is an older version. Update it, then reopen Tempo on the phone.");
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return savePreparedTraining(await response.json());
      })
      .then(async (prepared) => {
        await waitForOfflineShell();
        if (generation === requestGeneration && !hasConflicts && prepared.localDate === localDayKey())
          useTrainingStore.getState().setQueueNotice(
            `Phone queue prepared for ${prepared.localDate}.` +
            (prepared.cards.some(requiresConnectedGrading)
              ? ` ${prepared.cards.filter(requiresConnectedGrading).length} exercise(s) still require the computer.`
              : ""),
          );
      })
      .catch((error) => {
        if (generation === requestGeneration && !hasConflicts)
          useTrainingStore.getState().setQueueNotice(
            `Phone queue could not be prepared. ${error instanceof Error ? error.message : "Keep the computer connected and retry loading the queue."}`,
          );
      });
  } catch (error) {
    if (generation !== requestGeneration) return;
    const prepared = await readPreparedTraining().catch(() => null);
    if (prepared?.localDate === localDayKey()) {
      const supportedCards = prepared.cards.filter((card) => !requiresConnectedGrading(card));
      const cards = await runStudyTask<PracticeCard[]>({
        kind: "queue", payload: { cards: supportedCards, count: supportedCards.length, local_date: prepared.localDate },
      });
      if (generation !== requestGeneration) return;
      useTrainingStore.getState().hydrateLocalQueue(cards, advance, cards.length);
      useTrainingStore.getState().setOfflineQueue(true);
      useTrainingStore.getState().setServiceError("");
      const remainingConnectedExercises = prepared.cards.length - supportedCards.length;
      const unsynced = prepared.attempts.filter((attempt) => !attempt.serverReviewId && !attempt.conflict).length;
      useTrainingStore.getState().setQueueNotice(
        `Prepared phone queue for ${prepared.localDate} · ${unsynced} review${unsynced === 1 ? "" : "s"} saved on phone${remainingConnectedExercises ? ` · ${remainingConnectedExercises} exercise${remainingConnectedExercises === 1 ? " requires" : "s require"} the computer` : ""}.`,
      );
      return;
    }
    reportDebugError(error, {
      kind: "api",
      source: "training-queue",
      operation: "load today queue",
      endpoint: `${API_URL}/api/queue/window?limit=20`,
    });
    useTrainingStore
      .getState()
      .setServiceError(
        `The local queue could not be loaded. Your active attempt is retained. ${String(error)}`,
      );
    throw error;
  } finally {
    if (activeQueueController === controller) activeQueueController = null;
  }
}
