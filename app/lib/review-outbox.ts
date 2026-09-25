import { API_URL } from "../const";

export type PendingReview = {
  backendId: string;
  queueEntryId: number;
  outcome: "again" | "correct";
  guided: boolean;
};

const storageKey = "tempo-pending-training-reviews-v1";
let activeFlush: Promise<void> | undefined;

export function pendingReviews(): PendingReview[] {
  const stored = localStorage.getItem(storageKey);
  if (!stored) return [];
  const parsed: unknown = JSON.parse(stored);
  if (!Array.isArray(parsed) || parsed.some((item) =>
    !item || typeof item !== "object" ||
    typeof item.backendId !== "string" ||
    !Number.isInteger(item.queueEntryId) ||
    !["again", "correct"].includes(item.outcome) ||
    typeof item.guided !== "boolean")) {
    throw new Error("The saved training review is invalid. Restore your data before continuing.");
  }
  return parsed as PendingReview[];
}

export function enqueuePendingReview(review: PendingReview): void {
  const pending = pendingReviews();
  if (pending.some((item) => item.queueEntryId === review.queueEntryId)) return;
  localStorage.setItem(storageKey, JSON.stringify([...pending, review]));
}

async function savePendingReviews(): Promise<void> {
  for (const review of pendingReviews()) {
    if (review.guided) {
      const failureResponse = await fetch(`${API_URL}/api/queue/entries/${review.queueEntryId}/fail`, { method: "POST" });
      if (!failureResponse.ok) throw new Error(await responseDetail(failureResponse));
    }
    const reviewResponse = await fetch(`${API_URL}/api/cards/${review.backendId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        outcome: review.outcome,
        guided: review.guided,
        queue_entry_id: review.queueEntryId,
      }),
    });
    if (!reviewResponse.ok) throw new Error(await responseDetail(reviewResponse));
    const remaining = pendingReviews();
    localStorage.setItem(storageKey, JSON.stringify(
      remaining.filter((item) => item.queueEntryId !== review.queueEntryId),
    ));
  }
}

async function responseDetail(response: Response): Promise<string> {
  const body = await response.json().catch(() => ({})) as { detail?: string };
  return body.detail ?? `Local service returned HTTP ${response.status}.`;
}

export function flushPendingReviews(): Promise<void> {
  if (!activeFlush) {
    activeFlush = savePendingReviews().finally(() => { activeFlush = undefined; });
  }
  return activeFlush;
}
