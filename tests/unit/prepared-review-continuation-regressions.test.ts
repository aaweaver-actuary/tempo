import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { advancePreparedContinuation, capturePreparedNext } from "../../app/lib/prepared-review-continuation";
import { enqueuePendingReview, flushPendingReviews, pendingReviews } from "../../app/lib/review-outbox";
import { fetchAndInitializeQueue, rememberConfirmedQueueEntry } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";
import { mapQueueCardToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";
import { localDayKey } from "../../app/utils/local";

vi.mock("../../app/lib/background-study", async () => {
  const { createStudyPositionStore } = await import("../../app/lib/study-position-store");
  const compute = createStudyPositionStore();
  return { runStudyTask: vi.fn(async task => compute(task)) };
});
const queue = ["a", "b", "c"].map((id, index) => ({ id, queue_entry_id: 501 + index, revision: 1,
  start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", moves: ["e2e4", "e7e5", "g1f3"],
  content_type: "opening", repertoire_name: id, repertoire_source: "PGN" }));
const cards = queue.map(mapQueueCardToPracticeCard);
beforeEach(() => {
  localStorage.clear();
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  useTrainingStore.getState().hydrateLocalQueue(cards, true, 3);
  useTrainingStore.setState({ queueReadiness: "ready", queueLocalDate: localDayKey(), isDatabaseQueueActive: true });
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(["revision", "cycle", "day", "generation", "pending", "window", "readiness", "database"])(
  "prepared_continuation_requires_current_head_and_matching_identity: local %s invalidation", invalidation => {
    const candidate = capturePreparedNext()!;
    const { attempt } = useTrainingStore.getState();
    if (invalidation === "revision") candidate.revision++;
    if (invalidation === "cycle") candidate.cycle++;
    if (invalidation === "day") candidate.local_date = "1999-01-01";
    if (invalidation === "pending") enqueuePendingReview({ backendId: "b", queueEntryId: 502, outcome: "again", guided: false });
    if (invalidation === "window") useTrainingStore.setState({ practiceCards: [cards[0]] });
    if (invalidation === "readiness") useTrainingStore.setState({ queueReadiness: "loading" });
    if (invalidation === "database") useTrainingStore.setState({ isDatabaseQueueActive: false });
    expect(advancePreparedContinuation(candidate, attempt.attemptId!, attempt.generation + (invalidation === "generation" ? 1 : 0))).toBe(false);
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(501);
  });

it("async_reconciliation_preserves_active_attempt_and_excludes_completed_entries", async () => {
  const initialAttempt = useTrainingStore.getState().attempt;
  rememberConfirmedQueueEntry(cards[0]);
  expect(advancePreparedContinuation(capturePreparedNext(), initialAttempt.attemptId!, initialAttempt.generation)).toBe(true);
  useTrainingStore.setState({ step: 1 });
  const activeAttempt = useTrainingStore.getState().attempt;
  let releaseOld!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => new Promise(resolve => { releaseOld = resolve; }))
    .mockImplementation(() => Response.json({ cards: queue, count: 3, local_date: localDayKey() })));
  const oldRead = fetchAndInitializeQueue(false, { replaySavedReviews: false, preservePreparedAttempt: true });
  await vi.waitFor(() => expect(releaseOld).toBeTypeOf("function"));
  await fetchAndInitializeQueue(false, { replaySavedReviews: false, preservePreparedAttempt: true });
  expect(useTrainingStore.getState().attempt).toEqual(activeAttempt);
  expect(useTrainingStore.getState().step).toBe(1);
  const secondAttempt = useTrainingStore.getState().attempt;
  rememberConfirmedQueueEntry(cards[1]);
  expect(advancePreparedContinuation(capturePreparedNext(), secondAttempt.attemptId!, secondAttempt.generation)).toBe(true);
  const finalAttempt = useTrainingStore.getState().attempt;
  releaseOld(Response.json({ cards: queue, count: 3, local_date: localDayKey() }));
  await oldRead;
  await fetchAndInitializeQueue(false, { replaySavedReviews: false, preservePreparedAttempt: true });
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(503);
  expect(useTrainingStore.getState().attempt).toEqual(finalAttempt);
  expect(useTrainingStore.getState().practiceCards.map(card => card.queueEntryId)).toEqual([503]);
});

it("unconfirmed_review_does_not_advance_prepared_card: frozen retry and malformed optional result", async () => {
  const candidate = capturePreparedNext()!;
  const review = { backendId: "a", queueEntryId: 501, attemptId: "frozen-attempt", outcome: "correct" as const,
    guided: false, expectedRevision: 1, preparedNext: candidate };
  enqueuePendingReview(review);
  const requestBodies: unknown[] = [];
  let confirmed = false;
  vi.stubGlobal("fetch", vi.fn(async (_url, options) => {
    requestBodies.push(JSON.parse(options.body));
    return Response.json(confirmed ? { persisted: true, prepared_continuation: "invalid" } : { persisted: false });
  }));
  await expect(flushPendingReviews()).rejects.toThrow("did not confirm");
  expect(pendingReviews()[0].preparedNext).toEqual(candidate);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(501);
  enqueuePendingReview({ ...review, preparedNext: { ...candidate, revision: 8 } });
  confirmed = true;
  const result = await flushPendingReviews();
  expect(requestBodies[1]).toEqual(requestBodies[0]);
  expect(result.persistedAttemptIds).toEqual([review.attemptId]);
  expect(result.preparedContinuations).toBeUndefined();
  expect(pendingReviews()).toEqual([]);
});

it("foreground review confirmation yields before unrelated later outbox delivery", async () => {
  const first = { backendId: "a", queueEntryId: 501, attemptId: "first", outcome: "correct" as const, guided: false };
  enqueuePendingReview(first);
  enqueuePendingReview({ ...first, backendId: "unrelated", queueEntryId: 900, attemptId: "later" });
  let releaseFirst!: (response: Response) => void;
  let releaseLater!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => new Promise(resolve => { releaseFirst = resolve; }))
    .mockImplementationOnce(() => new Promise(resolve => { releaseLater = resolve; })));
  const existingFlush = flushPendingReviews();
  await vi.waitFor(() => expect(releaseFirst).toBeTypeOf("function"));
  const foreground = flushPendingReviews(Infinity, false, undefined, undefined, "first");
  releaseFirst(Response.json({ persisted: true }));
  expect((await foreground).persistedAttemptIds).toEqual(["first"]);
  await vi.waitFor(() => expect(releaseLater).toBeTypeOf("function"));
  expect(pendingReviews().map(review => review.attemptId)).toEqual(["later"]);
  releaseLater(Response.json({ persisted: true }));
  await existingFlush;
});

it("prepared_continuation_replay_preserves_exactly_once_scheduling: confirmed frozen receipt", async () => {
  const candidate = capturePreparedNext()!;
  const attempt = useTrainingStore.getState().attempt;
  enqueuePendingReview({ backendId: "a", queueEntryId: 501, attemptId: attempt.attemptId!,
    outcome: "correct", guided: false, expectedRevision: 1, preparedNext: candidate });
  let confirmed = false;
  let submissions = 0;
  vi.stubGlobal("fetch", vi.fn(async input => {
    if (String(input).includes("/api/operations/")) return Response.json(confirmed
      ? { state: "complete", response: { persisted: true, prepared_continuation: candidate } }
      : { state: "queued" });
    submissions++;
    return Response.json({ operation_id: `review-attempt:${attempt.attemptId}` }, { status: 202 });
  }));
  await expect(flushPendingReviews()).rejects.toThrow("Save is still pending");
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(501);
  confirmed = true;
  const result = await flushPendingReviews(Infinity, true, attempt.attemptId, undefined, attempt.attemptId);
  expect(result.preparedContinuations?.[attempt.attemptId!]).toEqual(candidate);
  expect(advancePreparedContinuation(result.preparedContinuations?.[attempt.attemptId!], attempt.attemptId!, attempt.generation)).toBe(true);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(502);
  expect(submissions).toBe(1);
});

it("foreground review submission waits only for its same-card predecessors", async () => {
  const original = { backendId: "a", queueEntryId: 500, attemptId: "earlier", outcome: "again" as const, guided: false };
  enqueuePendingReview({ ...original, backendId: "unrelated", attemptId: "independent", queueEntryId: 900 });
  enqueuePendingReview(original);
  enqueuePendingReview({ ...original, queueEntryId: 501, attemptId: "submitted", outcome: "correct" });
  const deliveries: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url, options) => {
    deliveries.push(JSON.parse(options.body).attempt_id);
    return Response.json({ persisted: true });
  }));
  const result = await flushPendingReviews(Infinity, false, undefined, undefined, "submitted");
  expect(deliveries).toEqual(["earlier", "submitted"]);
  expect(result.persistedAttemptIds).toEqual(deliveries);
  expect(pendingReviews().map(review => review.attemptId)).toEqual(["independent"]);
});
