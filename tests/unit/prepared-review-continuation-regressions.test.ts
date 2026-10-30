import { updateStudyIntent } from "../../database/browser/study-intent-storage";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { advancePreparedContinuation, capturePreparedNext } from "../../app/lib/prepared-review-continuation";
import { enqueuePendingReview, flushPendingReviews, pendingReviews } from "../../app/lib/review-outbox";
import { fetchAndInitializeQueue, rememberConfirmedQueueEntry } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";
import { mapQueueCardToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";
import { localDayKey } from "../../app/utils/local";
import { asQueueEntryId } from "../../app/types";
import { enqueueTrainingFailure, pendingTrainingFailureContexts } from "../../app/lib/training-failure-outbox";
import * as offlineTraining from "../../app/lib/offline-training";

vi.mock("../../app/lib/background-study", async () => {
  const { computeStudyTask } = await import("../../app/lib/study-computation");
  return { runStudyTask: vi.fn(async task => computeStudyTask(task)) };
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

it('determined_local_priority_prepares_before_new_head_without_showing_an_ordinary_successor', async () => {
  const active = useTrainingStore.getState().attempt;
  let releasePreparation!: (response: Response) => void;
  const preparation = new Promise<Response>(resolve => { releasePreparation = resolve; });
  let queueReads = 0;
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/api/queue/prepare-next')) {
      expect(options?.method).toBe('POST');
      return preparation;
    }
    queueReads++;
    if (queueReads === 1) return Response.json({ detail: { code: 'study_feedback_preparation_pending' } }, { status:503 });
    return Response.json({ cards:[queue[2],queue[1]],count:2,local_date:localDayKey() });
  });
  vi.stubGlobal('fetch', fetcher);
  const seen: number[] = [];
  const unsubscribe = useTrainingStore.subscribe(state => {
    const id = state.getCard()?.queueEntryId;
    if (id && seen.at(-1) !== id) seen.push(id);
  });
  try {
    const loading = fetchAndInitializeQueue(true,{replaySavedReviews:false});
    await vi.waitFor(() => expect(fetcher.mock.calls.some(([url]) => url.endsWith('/api/queue/prepare-next'))).toBe(true));
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(501);
    expect(useTrainingStore.getState().attempt).toEqual(active);
    releasePreparation(Response.json({progressed:true,pending:false,reason:null}));
    await loading;
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(503);
    expect(seen).not.toContain(502);
    expect(fetcher.mock.calls.find(([url]) => url.endsWith('/api/queue/prepare-next'))?.[1]?.method).toBe('POST');
    // This bounded local next-card path must progress during continuous Study;
    // it does not borrow the optional provider/background quiet-window gate.
    expect(fetcher.mock.calls.filter(([url]) => url.includes('/api/queue/window'))
      .every(([,options]) => !options?.headers)).toBe(true);
  } finally { unsubscribe(); }
});

it('next_card_preparation_lost_response_recovers_original_operation_across_acquisitions', async () => {
  let ready = false;
  const submitted: string[] = [];
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes('/api/operations/')) {
      expect(url).toContain(encodeURIComponent(submitted[0])); ready = true;
      return Response.json({ operation_id: submitted[0], state: 'complete', response: { progressed: true, pending: false, reason: null } });
    }
    if (url.endsWith('/api/queue/prepare-next')) {
      submitted.push(new Headers(options?.headers).get('Idempotency-Key')!);
      if (submitted.length === 1) throw new TypeError('Lost preparation response');
      ready = true; return Response.json({ progressed: true, pending: false, reason: null });
    }
    return ready ? Response.json({ cards: [queue[2]], count: 1, local_date: localDayKey() })
      : Response.json({ detail: { code: 'study_feedback_preparation_pending' } }, { status: 503 });
  });
  vi.stubGlobal('fetch', fetcher);
  await expect(fetchAndInitializeQueue(true, { replaySavedReviews: false })).rejects.toThrow('Lost preparation response');
  await fetchAndInitializeQueue(true, { replaySavedReviews: false });
  expect(submitted).toHaveLength(1);
  expect(fetcher.mock.calls.some(([url]) => url.includes('/api/operations/'))).toBe(true);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(503);
});

it('next_card_preparation_terminal_no_progress_never_rotates_ids_automatically', async () => {
  const submitted: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/api/queue/prepare-next')) {
      submitted.push(new Headers(options?.headers).get('Idempotency-Key')!);
      return Response.json({ progressed: false, pending: true, reason: 'source_unavailable' });
    }
    return Response.json({ detail: { code: 'study_feedback_preparation_pending' } }, { status: 503 });
  }));
  await expect(fetchAndInitializeQueue(true, { replaySavedReviews: false })).rejects.toThrow();
  expect(submitted).toHaveLength(1);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(501);
});

it('priority_reconciliation_retains_issued_board_and_replaces_only_upcoming_work', async () => {
  const active = useTrainingStore.getState().attempt;
  const board = useTrainingStore.getState().getCard();
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({
    cards:[queue[0],queue[2],queue[1]],count:3,local_date:localDayKey(),
  })));
  await fetchAndInitializeQueue(false,{replaySavedReviews:false,preservePreparedAttempt:true});
  expect(useTrainingStore.getState().getCard()).toEqual(board);
  expect(useTrainingStore.getState().attempt).toEqual(active);
  expect(useTrainingStore.getState().practiceCards.map(card => card.queueEntryId)).toEqual([501,503,502]);
});

it.each(["none", "missing", "mismatch", "attempt", "generation", "entry", "revision", "cycle", "date", "failed-flag", "failed-marker"])(
  "flush_settlement_audit overlapping continuation observers preserve %s authorization boundary", async invalidation => {
    const submitted = useTrainingStore.getState().attempt;
    const preparedNext = capturePreparedNext()!;
    const candidate = { ...preparedNext, attempt_failed: false };
    await enqueuePendingReview({ backendId: "a", queueEntryId: 501, attemptId: submitted.attemptId!,
      outcome: "correct", guided: false, expectedRevision: 1, preparedNext });
    let release!: (response: Response) => void;
    const response = new Promise<Response>(resolve => { release = resolve; });
    const fetcher = vi.fn(() => response);
    vi.stubGlobal("fetch", fetcher);
    const owner = flushPendingReviews();
    const first = flushPendingReviews(Infinity, false, undefined, undefined, submitted.attemptId);
    const second = flushPendingReviews(Infinity, true, submitted.attemptId, undefined, submitted.attemptId);
    if (invalidation === "attempt") useTrainingStore.setState({ attempt: { ...submitted, attemptId: "replacement-attempt" } });
    if (invalidation === "generation") useTrainingStore.setState({ attempt: { ...submitted, generation: submitted.generation + 1 } });
    if (["entry", "revision", "cycle", "failed-flag"].includes(invalidation)) {
      const next = { ...cards[1],
        ...(invalidation === "entry" ? { queueEntryId: asQueueEntryId(777) } : {}),
        ...(invalidation === "revision" ? { revision: 2 } : {}),
        ...(invalidation === "cycle" ? { queueCycle: 1 } : {}),
        ...(invalidation === "failed-flag" ? { attemptFailed: true } : {}) };
      useTrainingStore.setState({ practiceCards: [cards[0], next, cards[2]] });
    }
    if (invalidation === "date") useTrainingStore.setState({ queueLocalDate: "1999-01-01" });
    if (invalidation === "failed-marker") await enqueueTrainingFailure(502, "b", 1);
    const before = useTrainingStore.getState();
    release(Response.json({ persisted: true, ...(invalidation === "missing" ? {} : {
      prepared_continuation: invalidation === "mismatch" ? { ...candidate, queue_entry_id: 777 } : candidate }) }));
    const [firstResult, secondResult] = await Promise.all([first, second]);
    await owner;
    const advances = [firstResult, secondResult].map(result => advancePreparedContinuation(
      result.preparedContinuations?.[submitted.attemptId!], submitted.attemptId!, submitted.generation));
    expect(advances).toEqual(invalidation === "none" ? [true, false] : [false, false]);
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(invalidation === "none" ? 502 : 501);
    if (invalidation !== "none") {
      expect(useTrainingStore.getState().attempt).toEqual(before.attempt);
      expect(useTrainingStore.getState().practiceCards).toEqual(before.practiceCards);
    }
    if (invalidation === "failed-flag") expect(useTrainingStore.getState().practiceCards[1].attemptFailed).toBe(true);
    if (invalidation === "failed-marker") expect(pendingTrainingFailureContexts()).toContainEqual(expect.objectContaining({ queueEntryId: 502, backendId: "b" }));
    expect(firstResult.persistedAttemptIds).toEqual([submitted.attemptId]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

it.each([true, false])("unconfirmed_review_does_not_advance_prepared_card: unresolved authoritative head cannot issue a filtered successor (advance=%s)", async advance => {
  const activeAttempt = useTrainingStore.getState().attempt;
  rememberConfirmedQueueEntry(cards[0]);
  await enqueuePendingReview({ backendId: "c", queueEntryId: 503, attemptId: "independent-pending", outcome: "correct", guided: false });
  // C was promoted ahead of prepared B. The server issued C, while its saved
  // local review remains unconfirmed; filtering C cannot confer issuance on B.
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ cards: [queue[2], queue[1]], count: 2, local_date: localDayKey() })));
  await expect(fetchAndInitializeQueue(advance, { replaySavedReviews: false })).rejects.toThrow("authoritative queue head");
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(501);
  expect(useTrainingStore.getState().attempt).toEqual(activeAttempt);
  expect(useTrainingStore.getState().queueReadiness).toBe("unavailable");
  expect(useTrainingStore.getState().pendingReviewError).toContain("confirmation");
  expect(pendingReviews().map(review => review.attemptId)).toEqual(["independent-pending"]);
});

it.each(["revision", "cycle", "day", "generation", "attempt", "pending", "conflict", "guided_pending", "guided_flag", "window", "readiness", "database"])(
  "prepared_continuation_requires_current_head_and_matching_identity: local %s invalidation", async invalidation => {
    const candidate = { ...capturePreparedNext()!, attempt_failed: false };
    const { attempt } = useTrainingStore.getState();
    if (invalidation === "revision") candidate.revision++;
    if (invalidation === "cycle") candidate.cycle++;
    if (invalidation === "day") candidate.local_date = "1999-01-01";
    if (invalidation === "pending") await enqueuePendingReview({ backendId: "b", queueEntryId: 502, outcome: "again", guided: false });
    if (invalidation === "guided_pending") await enqueueTrainingFailure(502, "b", 1);
    if (invalidation === "guided_flag") useTrainingStore.setState({ practiceCards: [cards[0], { ...cards[1], attemptFailed: true }, cards[2]] });
    if (invalidation === "conflict") {
      await enqueuePendingReview({ backendId: "b", queueEntryId: 502, outcome: "again", guided: false });
      const review = pendingReviews()[0];
      await updateStudyIntent(`review:${review.attemptId}`, current => ({ ...current, state: "conflicted", data: { ...review,
        state: "conflicted", conflict: { code: "queue_attempt_unprovable", message: "Needs review", retryable: false } } }));
    }
    if (invalidation === "window") useTrainingStore.setState({ practiceCards: [cards[0]] });
    if (invalidation === "readiness") useTrainingStore.setState({ queueReadiness: "loading" });
    if (invalidation === "database") useTrainingStore.setState({ isDatabaseQueueActive: false });
    expect(advancePreparedContinuation(candidate, invalidation === "attempt" ? "another-attempt" : attempt.attemptId!,
      attempt.generation + (invalidation === "generation" ? 1 : 0))).toBe(false);
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(501);
  });

it("prepared_continuation_requires_current_head_and_matching_identity: confirmed guided successor retains guidance", () => {
  const continuation = { ...capturePreparedNext()!, attempt_failed: true };
  const attempt = useTrainingStore.getState().attempt;
  expect(advancePreparedContinuation(continuation, attempt.attemptId!, attempt.generation)).toBe(true);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(502);
  expect(useTrainingStore.getState().isAttemptFailed).toBe(true);
});

it.each(["removed", "revision", "cycle", "replacement", "promotion", "day"])(
  "pr229_observed_authoritative_invalidation_blocks_delayed_continuation: %s", async invalidation => {
    const submittedAttempt = useTrainingStore.getState().attempt;
    const preparedNext = capturePreparedNext()!;
    await enqueuePendingReview({ backendId: "a", queueEntryId: 501, attemptId: submittedAttempt.attemptId!,
      outcome: "correct", guided: false, expectedRevision: 1, preparedNext });
    let releaseReview!: (response: Response) => void;
    const heldReview = new Promise<Response>(resolve => { releaseReview = resolve; });
    const revisedSuccessor = { ...queue[1], cycle: 0,
      ...(invalidation === "revision" ? { revision: 2 } : {}),
      ...(invalidation === "cycle" ? { cycle: 1 } : {}),
      ...(invalidation === "replacement" ? { id: "replacement-b", queue_entry_id: 777 } : {}) };
    const authoritativeCards = invalidation === "removed" ? [queue[2]]
      : invalidation === "promotion" ? [queue[2], revisedSuccessor] : [revisedSuccessor, queue[2]];
    const fetcher = vi.fn(async (_input, options?: RequestInit) => options?.method === "POST" ? heldReview
      : Response.json({ cards: authoritativeCards, count: authoritativeCards.length,
        local_date: invalidation === "day" ? "1999-01-01" : localDayKey(), projection: { state: "ready", generation: 2, updated_at: null, refresh_pending: 0, last_error: null, blocked_count: 0 } }));
    vi.stubGlobal("fetch", fetcher);
    const settlement = flushPendingReviews();
    await vi.waitFor(() => expect(releaseReview).toBeTypeOf("function"));
    // Retain the active attempt so rejection must follow the newly observed
    // authoritative successor, rather than an unrelated attempt-token change.
    await fetchAndInitializeQueue(false, { replaySavedReviews: false, preservePreparedAttempt: true });
    expect(useTrainingStore.getState().attempt).toEqual(submittedAttempt);
    releaseReview(Response.json({ persisted: true, prepared_continuation: { ...preparedNext, attempt_failed: false } }));
    const result = await settlement;
    expect(result.persistedAttemptIds).toEqual([submittedAttempt.attemptId]);
    expect(pendingReviews()).toEqual([]);
    expect(advancePreparedContinuation(result.preparedContinuations?.[submittedAttempt.attemptId!],
      submittedAttempt.attemptId!, submittedAttempt.generation)).toBe(false);
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(501);
    expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
  });

it("pr229_new_publication_retaining_authorized_successor_preserves_continuation", async () => {
  const submittedAttempt = useTrainingStore.getState().attempt;
  const continuation = { ...capturePreparedNext()!, attempt_failed: false };
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ cards: queue.slice(1), count: 2,
    local_date: localDayKey(), projection: { state: "ready", generation: 2, updated_at: null, refresh_pending: 0, last_error: null, blocked_count: 0 } })));
  await fetchAndInitializeQueue(false, { replaySavedReviews: false, preservePreparedAttempt: true });
  expect(useTrainingStore.getState().attempt).toEqual(submittedAttempt);
  expect(advancePreparedContinuation(continuation, submittedAttempt.attemptId!, submittedAttempt.generation)).toBe(true);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(502);
});

const preparedBoardSnapshot = () => {
  const state = useTrainingStore.getState();
  return { attempt: state.attempt, card: state.getCard(), fen: state.currentFenString, step: state.step,
    lastMove: state.lastMove, opponentLastMove: state.opponentLastMove, failed: state.isAttemptFailed, hint: state.showHint };
};

it("prepared_reconciliation_failure_preserves_authorized_attempt_and_later_recovers", async () => {
  const initialAttempt = useTrainingStore.getState().attempt;
  rememberConfirmedQueueEntry(cards[0]);
  expect(advancePreparedContinuation(capturePreparedNext(), initialAttempt.attemptId!, initialAttempt.generation)).toBe(true);
  useTrainingStore.setState({ step: 1, lastMove: ["e2", "e4"], isAttemptFailed: true, showHint: true });
  const activeBoard = preparedBoardSnapshot();
  let rejectReconciliation!: (error: Error) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((_resolve, reject) => { rejectReconciliation = reject; })));
  const reconciliation = fetchAndInitializeQueue(false, { replaySavedReviews: false, preservePreparedAttempt: true });
  const rejected = expect(reconciliation).rejects.toThrow("queue processing failed");
  await vi.waitFor(() => expect(rejectReconciliation).toBeTypeOf("function"));
  rejectReconciliation(new Error("queue processing failed"));
  await rejected;
  expect(preparedBoardSnapshot()).toEqual(activeBoard);
  // Ready describes the retained authorized attempt, not issuance of C.
  expect(useTrainingStore.getState().queueReadiness).toBe("ready");
  expect(advancePreparedContinuation(undefined, activeBoard.attempt.attemptId!, activeBoard.attempt.generation)).toBe(false);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ cards: queue.slice(1), count: 2, local_date: localDayKey() })));
  await fetchAndInitializeQueue(false, { replaySavedReviews: false, preservePreparedAttempt: true });
  expect(preparedBoardSnapshot()).toEqual(activeBoard);
  expect(useTrainingStore.getState().practiceCards.map(card => card.queueEntryId)).toEqual([502, 503]);
});

it.each(["success", "failure"])("superseded_prepared_reconciliation_cannot_change_active_board_or_readiness: %s", async outcome => {
  const initialAttempt = useTrainingStore.getState().attempt;
  expect(advancePreparedContinuation(capturePreparedNext(), initialAttempt.attemptId!, initialAttempt.generation)).toBe(true);
  useTrainingStore.setState({ step: 1 });
  const activeBoard = preparedBoardSnapshot();
  let resolveStale!: (response: Response) => void;
  let rejectStale!: (error: Error) => void;
  vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve, reject) => {
    resolveStale = resolve; rejectStale = reject;
  })).mockResolvedValue(Response.json({ detail: "Authoritative refresh unavailable" }, { status: 422 })));
  const stale = fetchAndInitializeQueue(false, { replaySavedReviews: false, preservePreparedAttempt: true });
  await vi.waitFor(() => expect(resolveStale).toBeTypeOf("function"));
  await expect(fetchAndInitializeQueue(false, { replaySavedReviews: false })).rejects.toThrow("Authoritative refresh unavailable");
  const failedRefreshState = useTrainingStore.getState();
  expect(failedRefreshState.queueReadiness).toBe("unavailable");
  const serviceError = failedRefreshState.serviceError;
  if (outcome === "success") resolveStale(Response.json({ cards: queue, count: 3, local_date: localDayKey() }));
  else rejectStale(new Error("Superseded request failed"));
  await stale;
  expect(preparedBoardSnapshot()).toEqual(activeBoard);
  expect(useTrainingStore.getState().queueReadiness).toBe("unavailable");
  expect(useTrainingStore.getState().serviceError).toBe(serviceError);
});

it("async_reconciliation_preserves_active_attempt_and_excludes_completed_entries", async () => {
  const initialAttempt = useTrainingStore.getState().attempt;
  rememberConfirmedQueueEntry(cards[0]);
  expect(advancePreparedContinuation(capturePreparedNext(), initialAttempt.attemptId!, initialAttempt.generation)).toBe(true);
  useTrainingStore.setState({ step: 1 });
  const activeAttempt = useTrainingStore.getState().attempt;
  let releaseOld!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => new Promise(resolve => { releaseOld = resolve; }))
    .mockImplementation(async () => Response.json({ cards: queue, count: 3, local_date: localDayKey() })));
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

it("issue186_reconciliation_preserves_completed_prepared_attempt", async () => {
  const originalAttempt = useTrainingStore.getState().attempt;
  rememberConfirmedQueueEntry(cards[0]);
  expect(advancePreparedContinuation(capturePreparedNext(), originalAttempt.attemptId!, originalAttempt.generation)).toBe(true);
  useTrainingStore.getState().setAttemptPhase("complete");
  const completedAttempt = useTrainingStore.getState().attempt;
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ cards: queue, count: 3, local_date: localDayKey() })));
  await fetchAndInitializeQueue(false, { preservePreparedAttempt: true, replaySavedReviews: false,
    timeoutMs: 15_000, expectedAttempt: completedAttempt });
  expect(useTrainingStore.getState().attempt).toEqual(completedAttempt);
  expect(useTrainingStore.getState().practiceCards.map(card => card.queueEntryId)).toEqual([502, 503]);
});

it("issue186_late_body_after_deadline_cannot_install", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
  let finishBody!: (value: unknown) => void;
  const response = Response.json({});
  vi.spyOn(response, "json").mockImplementation(() => new Promise(resolve => { finishBody = resolve; }));
  vi.stubGlobal("fetch", vi.fn(async () => response));
  const activeAttempt = useTrainingStore.getState().attempt;
  const loading = fetchAndInitializeQueue(true, { timeoutMs: 15_000, replaySavedReviews: false, expectedAttempt: activeAttempt });
  const failure = loading.catch(error => error);
  await vi.waitFor(() => expect(finishBody).toBeTypeOf("function"));
  await vi.advanceTimersByTimeAsync(15_001);
  expect(await failure).toBeInstanceOf(Error);
  finishBody({ cards: queue.slice(1), count: 2, local_date: localDayKey() });
  await vi.advanceTimersByTimeAsync(0);
  expect(useTrainingStore.getState().attempt).toEqual(activeAttempt);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(501);
  expect(useTrainingStore.getState().queueReadiness).toBe("unavailable");
});

it("issue186_queue_prerequisite_is_included_in_acquisition_deadline", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });

  vi.spyOn(offlineTraining, "readPreparedTraining").mockImplementation(() => new Promise(() => {}));
  const fetcher = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetcher);
  const activeAttempt = useTrainingStore.getState().attempt;
  const loading = fetchAndInitializeQueue(true, { timeoutMs: 15_000, replaySavedReviews: false, expectedAttempt: activeAttempt });
  const failure = loading.catch(error => error);
  await vi.advanceTimersByTimeAsync(15_001);
  expect(await failure).toBeInstanceOf(Error);
  expect(fetcher).not.toHaveBeenCalled();
  expect(useTrainingStore.getState().attempt).toEqual(activeAttempt);
  expect(useTrainingStore.getState().queueReadiness).toBe("unavailable");
});

it("unconfirmed_review_does_not_advance_prepared_card: frozen retry and malformed optional result", async () => {
  const candidate = capturePreparedNext()!;
  const review = { backendId: "a", queueEntryId: 501, attemptId: "frozen-attempt", outcome: "correct" as const,
    guided: false, expectedRevision: 1, preparedNext: candidate };
  await enqueuePendingReview(review);
  const requestBodies: unknown[] = [];
  let confirmed = false;
  vi.stubGlobal("fetch", vi.fn(async (_url, options) => {
    if (String(_url).includes("/api/operations/")) return new Response(null, { status: 404 });
    requestBodies.push(JSON.parse(options.body));
    return Response.json(confirmed ? { persisted: true, prepared_continuation: "invalid" } : { persisted: false });
  }));
  await expect(flushPendingReviews()).rejects.toThrow("did not confirm");
  expect(pendingReviews()[0].preparedNext).toEqual(candidate);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(501);
  await expect(enqueuePendingReview({ ...review, preparedNext: { ...candidate, revision: 8 } })).rejects.toThrow("different completed result");
  confirmed = true;
  const result = await flushPendingReviews();
  expect(requestBodies[1]).toEqual(requestBodies[0]);
  expect(result.persistedAttemptIds).toEqual([review.attemptId]);
  expect(result.preparedContinuations).toBeUndefined();
  expect(pendingReviews()).toEqual([]);
});

it("foreground review confirmation yields before unrelated later outbox delivery", async () => {
  const first = { backendId: "a", queueEntryId: 501, attemptId: "first", outcome: "correct" as const, guided: false };
  await enqueuePendingReview(first);
  await enqueuePendingReview({ ...first, backendId: "unrelated", queueEntryId: 900, attemptId: "later" });
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
  await enqueuePendingReview({ backendId: "a", queueEntryId: 501, attemptId: attempt.attemptId!,
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
  await enqueuePendingReview({ ...original, backendId: "unrelated", attemptId: "independent", queueEntryId: 900 });
  await enqueuePendingReview(original);
  await enqueuePendingReview({ ...original, queueEntryId: 501, attemptId: "submitted", outcome: "correct" });
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

it('study_priority_queue_body_remains_essential_until_queue_installation_settles', async () => {
  vi.useFakeTimers();
  const priority=await import('../../app/lib/study-priority');priority.setStudyPriorityScope(true);
  let finishBody!:(value:unknown)=>void;
  const response=Response.json({});
  vi.spyOn(response,'json').mockImplementation(()=>new Promise(resolve=>{finishBody=resolve;}));
  vi.stubGlobal('fetch',vi.fn(async()=>response));
  const loading=fetchAndInitializeQueue(false,{replaySavedReviews:false,preservePreparedAttempt:true});
  await vi.waitFor(()=>expect(finishBody).toBeTypeOf('function'));
  await vi.advanceTimersByTimeAsync(3500);expect(priority.studyInteractionActive()).toBe(true);
  finishBody({cards:queue,count:queue.length,local_date:localDayKey()});await loading;
  await vi.advanceTimersByTimeAsync(3000);expect(priority.studyInteractionActive()).toBe(false);
  priority.setStudyPriorityScope(false);
});
