import { API_URL } from "../const";
import { localDayKey, usesLocalApi } from "../utils/local";
import { useTrainingStore } from "../state/training-store";
import { runStudyTask } from "../lib/background-study";
import type { PracticeCard } from "../domain/cards";
import { reportDebugError } from "../lib/debug-reporting";
import { flushPendingReviews, pendingReviews } from "../lib/review-outbox";

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
    if (!useTrainingStore.getState().isDatabaseQueueActive && !pendingReviews().length) {
      const stored = localStorage.getItem(queueCacheKey);
      if (stored) {
        try {
          const cached = JSON.parse(stored) as QueuePayload;
          if (cached.local_date === localDayKey() && cached.cards?.length) {
            const cachedCards = await runStudyTask<PracticeCard[]>({ kind: "queue", payload: cached });
            if (generation === requestGeneration)
              useTrainingStore.getState().hydrateLocalQueue(cachedCards, false, cached.count);
          }
        } catch {
          localStorage.removeItem(queueCacheKey);
        }
      }
    }
    if (pendingReviews().length) await flushPendingReviews();
    const raw = await loadTodayQueueWithRetry(controller.signal);
    const cards = await runStudyTask<PracticeCard[]>({
      kind: "queue",
      payload: raw,
    });
    if (generation !== requestGeneration) return;
    useTrainingStore.getState().hydrateLocalQueue(cards, advance, raw.count);
    try { localStorage.setItem(queueCacheKey, JSON.stringify(raw)); } catch {
      // A full browser storage quota must not turn a successful queue read into a failure.
    }
    useTrainingStore.getState().setServiceError("");
  } catch (error) {
    if (generation !== requestGeneration) return;
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
