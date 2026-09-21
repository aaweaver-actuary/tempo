import { API_URL } from "../const";
import { usesLocalApi } from "../utils/local";
import { useTrainingStore } from "../state/training-store";
import { runStudyTask } from "../lib/background-study";
import type { PracticeCard } from "../domain/cards";
import { reportDebugError } from "../lib/debug-reporting";

let requestGeneration = 0;

async function loadTodayQueueWithRetry(): Promise<unknown> {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const response = await fetch(`${API_URL}/api/queue/today`);
    if (response.ok) return response.json();

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
    if (!retryable || attempt === 2) throw new Error(detail);
    await new Promise((resolve) => window.setTimeout(resolve, 250 * (attempt + 1)));
  }
  throw new Error("The local queue could not be loaded.");
}

export async function fetchAndInitializeQueue(advance = false): Promise<void> {
  if (!usesLocalApi()) return;
  const generation = ++requestGeneration;
  try {
    const raw = await loadTodayQueueWithRetry();
    const cards = await runStudyTask<PracticeCard[]>({
      kind: "queue",
      payload: raw,
    });
    if (generation !== requestGeneration) return;
    useTrainingStore.getState().hydrateLocalQueue(cards, advance);
    useTrainingStore.getState().setServiceError("");
  } catch (error) {
    if (generation !== requestGeneration) return;
    reportDebugError(error, {
      kind: "api",
      source: "training-queue",
      operation: "load today queue",
      endpoint: `${API_URL}/api/queue/today`,
    });
    useTrainingStore
      .getState()
      .setServiceError(
        `The local queue could not be loaded. Your active attempt is retained. ${String(error)}`,
      );
    throw error;
  }
}
