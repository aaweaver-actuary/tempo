import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { capturePreparedNext, advancePreparedContinuation } from "../../app/lib/prepared-review-continuation";
import { enqueuePendingReview, flushPendingReviews } from "../../app/lib/review-outbox";
import { mapQueueCardToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";
import { useTrainingStore } from "../../app/state/training-store";
import { localDayKey } from "../../app/utils/local";

beforeEach(() => {
  localStorage.clear();
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  const cards = ["a", "b"].map((id, index) => mapQueueCardToPracticeCard({
    id, queue_entry_id: 501 + index, revision: 1,
    start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", moves: ["e2e4"],
    content_type: "tactic", repertoire_name: id, repertoire_source: "synthetic",
  }));
  useTrainingStore.getState().hydrateLocalQueue(cards, true, 2);
  useTrainingStore.setState({ queueReadiness: "loading", queueLocalDate: localDayKey(), isDatabaseQueueActive: true });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("issue225_loading_review_replay_does_not_invent_prepared_continuation", async () => {
  const attempt = useTrainingStore.getState().attempt;
  expect(capturePreparedNext()).toBeUndefined();
  await enqueuePendingReview({ backendId: "a", queueEntryId: 501, attemptId: attempt.attemptId!,
    outcome: "correct", guided: false, expectedRevision: 1, preparedNext: capturePreparedNext() });
  const frozenResult = { persisted: true, card_id: "a", review_id: 7 };
  let confirmed = false;
  const submittedBodies: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input, options) => {
    if (String(input).includes("/api/operations/")) return Response.json(confirmed
      ? { state: "complete", response: frozenResult } : { state: "queued" });
    submittedBodies.push(JSON.parse(options.body));
    return Response.json({ operation_id: `review-attempt:${attempt.attemptId}` }, { status: 202 });
  }));
  await expect(flushPendingReviews()).rejects.toThrow("Save is still pending");
  expect(submittedBodies).toHaveLength(1);
  expect(submittedBodies[0]).not.toHaveProperty("prepared_next");
  useTrainingStore.setState({ queueReadiness: "ready" });
  expect(capturePreparedNext()).toMatchObject({ queue_entry_id: 502, card_id: "b" });
  confirmed = true;
  const replay = await flushPendingReviews(Infinity, true, attempt.attemptId, undefined, attempt.attemptId);
  expect(replay.persistedAttemptIds).toEqual([attempt.attemptId]);
  expect(replay.preparedContinuations?.[attempt.attemptId!]).toBeUndefined();
  expect(frozenResult).not.toHaveProperty("prepared_continuation");
  expect(advancePreparedContinuation(undefined, attempt.attemptId!, attempt.generation)).toBe(false);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(501);
  expect(submittedBodies).toHaveLength(1);
});
