import { withMissingReceipts, resetStudyStorage, interceptIntentWrites } from "./support/study-intent-fixtures";
import { initializeStudyIntentStorage, closeStudyIntentStorage } from "../../app/lib/study-intent-storage";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { conflictedReviews, enqueuePendingReview, flushPendingReviews, pendingReviews, retryReviewConflict, ReviewReplayError } from "../../app/lib/review-outbox";
import { enqueueTrainingFailure, pendingTrainingFailures } from "../../app/lib/training-failure-outbox";
import manifest from "../fixtures/opening-evidence-manifest.json";
import { openingEvidenceCheckpointSchema } from "../../app/domain/opening-evidence";
import * as notificationModule from "../../app/lib/notifications";
import * as openingEvidenceJournal from "../../app/lib/opening-evidence-journal";

function auditDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}
const auditReview = (attemptId: string, queueEntryId: number, backendId = attemptId) => ({
  backendId, queueEntryId, attemptId, outcome: "correct" as const, guided: false,
});
const auditSuccessor = { card_id: "audit-successor", queue_entry_id: 9999, revision: 1, cycle: 0, local_date: "2026-10-11" };

it("flush_settlement_audit reentrant submitted owner cannot reject after concurrent authoritative recovery", async () => {
  await enqueuePendingReview({ ...auditReview("startup-predecessor", 10001, "startup-conflict-card"), state: "conflicted",
    conflict: { code: "retired", message: "Needs attention", retryable: false } });
  await enqueuePendingReview(auditReview("startup-conflict", 10002, "startup-conflict-card"));
  await enqueuePendingReview({ ...auditReview("startup-target", 10003), preparedNext: auditSuccessor });
  const heldPost = auditDeferred<Response>();
  const postRequested = auditDeferred<void>();
  const heldReceipt = auditDeferred<Response>();
  const receiptRequested = auditDeferred<void>();
  const trace: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input, options) => {
    if (String(input).includes("/api/operations/")) {
      trace.push("receipt requested"); receiptRequested.resolve(); return heldReceipt.promise;
    }
    expect(JSON.parse(options!.body as string).attempt_id).toBe("startup-target");
    trace.push("submitted POST started"); postRequested.resolve(); return heldPost.promise;
  }));
  let enteredReentrantCall = false;
  let submittedOutcome: Promise<{ result?: Awaited<ReturnType<typeof flushPendingReviews>>; error?: ReviewReplayError }> | undefined;
  const submitFromInitialConflict = () => {
    if (enteredReentrantCall || !conflictedReviews().some(review => review.attemptId === "startup-conflict")) return;
    enteredReentrantCall = true;
    trace.push("initial conflict storage event");
    submittedOutcome = flushPendingReviews(Infinity, false, undefined, undefined, "startup-target")
      .then(result => ({ result }), error => { trace.push("submitted owner rejected"); return { error }; });
  };
  window.addEventListener("tempo:review-outbox", submitFromInitialConflict);
  const firstFlush = flushPendingReviews(1, false, undefined, undefined, "startup-conflict");
  let recoveredOutcome: ReturnType<typeof flushPendingReviews> | undefined;
  try {
    expect((await firstFlush).conflictedAttemptIds).toEqual(["startup-conflict"]);
    await postRequested.promise;
    expect(enteredReentrantCall).toBe(true);
    recoveredOutcome = flushPendingReviews(Infinity, true, "startup-target", undefined, "startup-target")
      .then(result => { trace.push("authoritative recovery confirmed"); return result; });
    // Receipt-first delivery begins synchronously only if the initial owner's
    // cleanup discarded the reentrant submission's active ownership.
    const concurrentRecoveryStarted = trace.includes("receipt requested");
    const confirmedReceipt = Response.json({ state: "complete", response: { persisted: true,
      prepared_continuation: auditSuccessor } });
    if (concurrentRecoveryStarted) {
      heldReceipt.resolve(confirmedReceipt);
      expect((await recoveredOutcome).persistedAttemptIds).toEqual(["startup-target"]);
      expect(pendingReviews().some(review => review.attemptId === "startup-target")).toBe(false);
      heldPost.reject(new Error("Lost POST response after the authoritative receipt"));
      const submitted = await submittedOutcome!;
      console.info("flush_settlement_audit initial ownership", { concurrentRecoveryStarted, trace, submitted });
      expect(submitted).toMatchObject({ result: { persistedAttemptIds: ["startup-target"] } });
    } else {
      expect(pendingReviews().some(review => review.attemptId === "startup-target")).toBe(true);
      heldPost.reject(new Error("Lost POST response before any receipt observation"));
      expect((await submittedOutcome!).error).toMatchObject({ attemptId: "startup-target", classification: "transient" });
      await receiptRequested.promise;
      heldReceipt.resolve(confirmedReceipt);
      const recovered = await recoveredOutcome;
      expect(recovered.persistedAttemptIds).toEqual(["startup-target"]);
      expect(recovered.preparedContinuations?.["startup-target"]).toEqual(auditSuccessor);
      expect(trace.indexOf("submitted owner rejected")).toBeLessThan(trace.indexOf("authoritative recovery confirmed"));
    }
    console.info("flush_settlement_audit initial ownership", { concurrentRecoveryStarted, trace });
  } finally {
    window.removeEventListener("tempo:review-outbox", submitFromInitialConflict);
    heldPost.reject(new Error("Release held POST"));
    heldReceipt.resolve(Response.json({ state: "complete", response: { persisted: true, prepared_continuation: auditSuccessor } }));
    await firstFlush; await submittedOutcome; await recoveredOutcome;
  }
});

it.each(["persisted", "terminal"])("flush_settlement_audit synchronous outbox subscriber observes subsequent %s publication", async outcome => {
  await enqueuePendingReview({ ...auditReview("reentrant-a", 9901), preparedNext: auditSuccessor });
  await enqueuePendingReview(auditReview("reentrant-b", 9902));
  const heldA = auditDeferred<Response>();
  const requestedA = auditDeferred<void>();
  const heldB = auditDeferred<Response>();
  const requestedB = auditDeferred<void>();
  const trace: string[] = [];
  const deliveries: string[] = [];
  vi.stubGlobal("fetch", withMissingReceipts(vi.fn(async (input, options) => {
    const attempt = JSON.parse(options!.body as string).attempt_id;
    deliveries.push(attempt);
    if (attempt === "reentrant-b") { requestedB.resolve(); return heldB.promise; }
    if (String(input).endsWith("/reconcile")) return Response.json({ persisted: false,
      conflict: { code: "retired", message: "Needs attention", retryable: false } });
    requestedA.resolve(); return heldA.promise;
  })));
  let ownerCompleted = false;
  const owner = flushPendingReviews().then(result => { ownerCompleted = true; return result; });
  await requestedA.promise;
  let reentrant: ReturnType<typeof flushPendingReviews> | undefined;
  const subscribeDuringStorageChange = () => {
    const target = [...pendingReviews(), ...conflictedReviews()].find(review => review.attemptId === "reentrant-a");
    if (reentrant || (outcome === "persisted" ? !!target : target?.state !== "conflicted")) return;
    trace.push("target storage transition");
    reentrant = flushPendingReviews(Infinity, false, undefined, undefined, "reentrant-a")
      .then(result => { trace.push("target observer settled"); return result; });
    trace.push("synchronous subscriber returned");
  };
  window.addEventListener("tempo:review-outbox", subscribeDuringStorageChange);
  try {
    heldA.resolve(outcome === "persisted" ? Response.json({ persisted: true, prepared_continuation: auditSuccessor })
      : Response.json({ detail: "Conflict", retryable: false }, { status: 409 }));
    await requestedB.promise;
    expect(reentrant).toBeDefined();
    const result = await reentrant!;
    expect(ownerCompleted).toBe(false);
    expect(result.persistedAttemptIds).toEqual(outcome === "persisted" ? ["reentrant-a"] : []);
    expect(result.conflictedAttemptIds).toEqual(outcome === "terminal" ? ["reentrant-a"] : []);
    expect(result.preparedContinuations).toEqual(outcome === "persisted" ? { "reentrant-a": auditSuccessor } : {});
    expect(trace).toEqual(["target storage transition", "synchronous subscriber returned", "target observer settled"]);
    heldB.resolve(Response.json({ persisted: true }));
    await owner;
    expect(result.persistedAttemptIds).not.toContain("reentrant-b");
    expect(deliveries).toEqual(outcome === "persisted" ? ["reentrant-a", "reentrant-b"] : ["reentrant-a", "reentrant-a", "reentrant-b"]);
    console.info("flush_settlement_audit synchronous subscriber", outcome, trace);
  } finally {
    window.removeEventListener("tempo:review-outbox", subscribeDuringStorageChange);
    heldA.resolve(Response.json({ persisted: true })); heldB.resolve(Response.json({ persisted: true }));
    await owner; await reentrant;
  }
});

it.each(["unlimited", "submitted"])("flush_settlement_audit %s follow-up failure retains earlier settled evidence", async caller => {
  await enqueuePendingReview({ ...auditReview("partial-a", 9801, "partial-card"), preparedNext: auditSuccessor });
  await enqueuePendingReview(auditReview("partial-b", 9802, "partial-card"));
  await enqueuePendingReview(auditReview("partial-c", 9803, "partial-card"));
  const responseA = auditDeferred<Response>();
  const requestedA = auditDeferred<void>();
  const responseB = auditDeferred<Response>();
  const requestedB = auditDeferred<void>();
  vi.stubGlobal("fetch", withMissingReceipts(vi.fn(async (_input, options) => {
    const attempt = JSON.parse(options!.body as string).attempt_id;
    if (attempt === "partial-a") { requestedA.resolve(); return responseA.promise; }
    requestedB.resolve(); return responseB.promise;
  })));
  const owner = flushPendingReviews(1);
  await requestedA.promise;
  const joinedOutcome = flushPendingReviews(Infinity, false, undefined, undefined, caller === "submitted" ? "partial-c" : undefined)
    .catch(error => error);
  const duplicateOutcome = flushPendingReviews(Infinity, false, undefined, undefined, caller === "submitted" ? "partial-c" : undefined)
    .catch(error => error);
  responseA.resolve(Response.json({ persisted: true, prepared_continuation: auditSuccessor }));
  expect((await owner).persistedAttemptIds).toEqual(["partial-a"]);
  await requestedB.promise;
  const originalBOutcome = flushPendingReviews(1).catch(error => error);
  responseB.reject(new Error("B failed before C delivery"));
  const error = await joinedOutcome;
  const duplicateError = await duplicateOutcome;
  const originalError = await originalBOutcome;
  expect(error).toBeInstanceOf(ReviewReplayError);
  expect(error.message).toBe(originalError.message);
  expect(error.stack).toBe(originalError.stack);
  expect(error.cause).toBe(originalError.cause);
  expect(error).toMatchObject({ attemptId: "partial-b", classification: "transient" });
  expect(pendingReviews().map(item => item.attemptId)).toEqual(["partial-b", "partial-c"]);
  expect(error.flushResult).toMatchObject({ persistedAttemptIds: ["partial-a"], conflictedAttemptIds: [],
    preparedContinuations: { "partial-a": auditSuccessor } });
  expect(duplicateError.flushResult).toEqual(error.flushResult);
  expect(originalError.flushResult).toEqual({ persistedAttemptIds: [], conflictedAttemptIds: [] });
  expect(error).not.toBe(originalError);
});

it.each(["persisted", "failed"])("flush_settlement_audit different explicit retry identities retain suppression after %s owner", async outcome => {
  await enqueuePendingReview({ ...auditReview("retry-a", 9701), automaticRecoverySuppressed: "failed" });
  await enqueuePendingReview({ ...auditReview("retry-b", 9702), automaticRecoverySuppressed: "failed" });
  const heldA = auditDeferred<Response>();
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input, options) => {
    if (String(input).includes("/api/operations/")) { requests.push(String(input).split("/").at(-1)!); return new Response(null, { status: 404 }); }
    const attempt = JSON.parse(options!.body as string).attempt_id;
    requests.push(attempt);
    return attempt === "retry-a" ? heldA.promise : Response.json({ persisted: true });
  }));
  const retryA = flushPendingReviews(1, false, "retry-a").catch(error => error);
  const retryB = flushPendingReviews(Infinity, false, "retry-b");
  heldA.resolve(outcome === "persisted" ? Response.json({ persisted: true })
    : Response.json({ detail: "Failed retry", retryable: false }, { status: 422 }));
  const [resultA, resultB] = await Promise.all([retryA, retryB]);
  if (outcome === "persisted") expect(resultA.persistedAttemptIds).toEqual(["retry-a"]);
  else expect(resultA).toMatchObject({ attemptId: "retry-a", classification: "failed" });
  expect(resultB.persistedAttemptIds).toEqual(["retry-b"]);
  expect(requests).toEqual(["review-attempt%3Aretry-a", "retry-a", "review-attempt%3Aretry-b", "retry-b"]);
  expect(pendingReviews().map(item => [item.attemptId, item.automaticRecoverySuppressed])).toEqual(outcome === "persisted" ? [] : [["retry-a", "failed"]]);
});

it.each(["completion", "rejection"])("flush_settlement_audit durable next generation settles waiters with its own terminal result: %s", async outcome => {
  const trace: string[] = [];
  const initialResponse = auditDeferred<Response>();
  const initialRequested = auditDeferred<void>();
  await enqueuePendingReview(auditReview("handoff-a", 9601));
  await enqueuePendingReview({ ...auditReview("handoff-predecessor", 9602, "handoff-card"), state: "conflicted",
    conflict: { code: "retired", message: "Needs attention", retryable: false } });
  await enqueuePendingReview(auditReview("handoff-b", 9603, "handoff-card"));
  vi.stubGlobal("fetch", withMissingReceipts(vi.fn(() => { initialRequested.resolve(); return initialResponse.promise; })));
  const owner = flushPendingReviews(1).then(result => { trace.push("A caller completion"); return { result }; },
    error => { trace.push("A caller rejection"); return { error }; });
  await initialRequested.promise;
  const oldWaiter = flushPendingReviews(Infinity, false, undefined, undefined, "handoff-b")
    .then(result => { trace.push("old B waiter completion"); return { result }; }, error => { trace.push("old B waiter rejection"); return { error }; });
  let nextFlush: ReturnType<typeof flushPendingReviews> | undefined;
  let scheduled = false;
  const scheduleNext = () => {
    if (scheduled) return;
    const remaining = pendingReviews().find(item => item.attemptId === "handoff-a");
    if (outcome === "completion" ? !!remaining : remaining?.automaticRecoverySuppressed !== "failed") return;
    scheduled = true;
    trace.push("A storage transition");
    queueMicrotask(() => {
      trace.push("first microtask");
      queueMicrotask(() => {
        trace.push("B starts");
        nextFlush = flushPendingReviews(Infinity, false, undefined, undefined, "handoff-b").then(result => {
          expect(conflictedReviews().some(item => item.attemptId === "handoff-b")).toBe(true);
          trace.push("B published terminal settlement"); return result;
        });
      });
    });
  };
  window.addEventListener("tempo:review-outbox", scheduleNext);
  try {
    initialResponse.resolve(outcome === "completion" ? Response.json({ persisted: true })
      : Response.json({ detail: "Invalid request", retryable: false }, { status: 422 }));
    const [ownerResult, submittedResult] = await Promise.all([owner, oldWaiter]);
    expect(scheduled).toBe(true);
    expect(nextFlush).toBeDefined();
    const nextResult = await nextFlush!;
    expect(trace.indexOf("B published terminal settlement")).toBeGreaterThan(trace.indexOf("B starts"));
    expect(trace).toContain("old B waiter completion");
    expect(submittedResult).toMatchObject({ result: { persistedAttemptIds: outcome === "completion" ? ["handoff-a"] : [], conflictedAttemptIds: ["handoff-b"] } });
    expect(nextResult).toMatchObject({ persistedAttemptIds: outcome === "completion" ? ["handoff-a"] : [], conflictedAttemptIds: ["handoff-b"] });
    expect(nextResult.preparedContinuations ?? {}).toEqual({});
    if (outcome === "completion") expect(ownerResult).toMatchObject({ result: { persistedAttemptIds: ["handoff-a"] } });
    else expect(ownerResult).toMatchObject({ error: { attemptId: "handoff-a" } });
    console.info("flush_settlement_audit handoff", outcome, trace);
  } finally { window.removeEventListener("tempo:review-outbox", scheduleNext); await owner; await nextFlush; }
});

it.each([1, 2])("flush_settlement_audit bounded %s then unlimited preserves all continuation capabilities", async maximum => {
  const response = auditDeferred<Response>();
  const firstRequested = auditDeferred<void>();
  await enqueuePendingReview({ ...auditReview("bounded-a", 9101), preparedNext: auditSuccessor });
  await enqueuePendingReview(auditReview("bounded-b", 9102));
  await enqueuePendingReview({ ...auditReview("bounded-c", 9103), preparedNext: auditSuccessor });
  const deliveries: string[] = [];
  vi.stubGlobal("fetch", withMissingReceipts(vi.fn(async (_input, options) => {
    const attempt = JSON.parse(options!.body as string).attempt_id;
    deliveries.push(attempt);
    if (attempt === "bounded-a") { firstRequested.resolve(); return response.promise; }
    return Response.json({ persisted: true, prepared_continuation: auditSuccessor });
  })));
  const bounded = flushPendingReviews(maximum);
  await firstRequested.promise;
  const unlimited = flushPendingReviews();
  response.resolve(Response.json({ persisted: true, prepared_continuation: auditSuccessor }));
  const [firstResult, completeResult] = await Promise.all([bounded, unlimited]);
  expect(firstResult.persistedAttemptIds).toHaveLength(maximum);
  expect(completeResult.persistedAttemptIds).toEqual(["bounded-a", "bounded-b", "bounded-c"]);
  expect(deliveries).toEqual(completeResult.persistedAttemptIds);
  expect(pendingReviews()).toEqual([]);
  expect(completeResult.preparedContinuations).toEqual({ "bounded-a": auditSuccessor, "bounded-c": auditSuccessor });
});

it.each([1, 2, Infinity])("flush_settlement_audit submitted identities coalesce with limit %s and retain immutable early snapshots", async maximum => {
  await enqueuePendingReview({ ...auditReview("identity-a", 9201), preparedNext: auditSuccessor });
  await enqueuePendingReview(auditReview("identity-b", 9202));
  const responseA = auditDeferred<Response>();
  const responseB = auditDeferred<Response>();
  const requestedA = auditDeferred<void>();
  const requestedB = auditDeferred<void>();
  const fetcher = vi.fn(async (input, options) => {
    if (String(input).includes("/api/operations/")) return new Response(null, { status: 404 });
    const attempt = JSON.parse(options!.body as string).attempt_id;
    if (attempt === "identity-a") { requestedA.resolve(); return responseA.promise; }
    requestedB.resolve(); return responseB.promise;
  });
  vi.stubGlobal("fetch", fetcher);
  const owner = flushPendingReviews(maximum);
  await requestedA.promise;
  const a1 = flushPendingReviews(Infinity, false, undefined, undefined, "identity-a");
  const a2 = flushPendingReviews(1, true, "identity-a", undefined, "identity-a");
  const b = flushPendingReviews(Infinity, true, undefined, undefined, "identity-b");
  responseA.resolve(Response.json({ persisted: true, prepared_continuation: auditSuccessor }));
  const [settledA1, settledA2] = await Promise.all([a1, a2]);
  expect(settledA1.persistedAttemptIds).toEqual(["identity-a"]);
  expect(settledA2).toEqual(settledA1);
  if (maximum > 1) expect(await flushPendingReviews(Infinity, false, undefined, undefined, "identity-a")).toEqual(settledA1);
  await requestedB.promise;
  responseB.resolve(Response.json({ persisted: true }));
  expect((await b).persistedAttemptIds).toContain("identity-b");
  await owner;
  expect(settledA1.persistedAttemptIds).toEqual(["identity-a"]);
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(2);
});

it.each(["terminal", "retryable", "network-before", "network-after", "missing-confirmation"])(
  "flush_settlement_audit overlapping submitted outcome remains truthful: %s", async outcome => {
    const attempt = "outcome-attempt";
    await enqueuePendingReview({ ...auditReview(attempt, 9301), preparedNext: auditSuccessor });
    const response = auditDeferred<Response>();
    const requested = auditDeferred<void>();
    let committed = false;
    let posts = 0;
    const fetcher = vi.fn(async (input, options) => {
      if (String(input).includes("/api/operations/")) return Response.json(committed
        ? { state: "complete", response: { persisted: true, prepared_continuation: auditSuccessor } }
        : { state: "queued" });
      posts++;
      if (String(input).endsWith("/reconcile")) return Response.json({ persisted: false,
        conflict: { code: "queue_attempt_unprovable", message: "Needs attention", retryable: false }, prepared_continuation: auditSuccessor });
      requested.resolve();
      expect(JSON.parse(options!.body as string).attempt_id).toBe(attempt);
      return response.promise;
    });
    vi.stubGlobal("fetch", fetcher);
    const ownerOutcome = flushPendingReviews().then(result => ({ result }), error => ({ error }));
    await requested.promise;
    const waiterOutcome = flushPendingReviews(Infinity, false, undefined, undefined, attempt)
      .then(result => ({ result }), error => ({ error }));
    if (outcome.startsWith("network")) {
      committed = outcome === "network-after";
      response.reject(new Error("Lost transport"));
    } else if (outcome === "missing-confirmation") response.resolve(Response.json({ prepared_continuation: auditSuccessor }));
    else response.resolve(Response.json({ detail: "Conflict", retryable: outcome === "retryable" }, { status: 409 }));
    const [ownerResult, waiterResult] = await Promise.all([ownerOutcome, waiterOutcome]);
    if (outcome === "terminal") {
      expect(waiterResult).toMatchObject({ result: { persistedAttemptIds: [], conflictedAttemptIds: [attempt], preparedContinuations: {} } });
      expect(conflictedReviews()[0].attemptId).toBe(attempt);
      expect(posts).toBe(2);
    } else {
      expect(waiterResult).toEqual(ownerResult);
      expect(waiterResult).toMatchObject({ error: { attemptId: attempt, flushResult: { persistedAttemptIds: [], conflictedAttemptIds: [] } } });
      expect(pendingReviews()[0].attemptId).toBe(attempt);
      expect(posts).toBe(1);
      if (committed) {
        const recovered = await flushPendingReviews(Infinity, true, undefined, undefined, attempt);
        expect(recovered.persistedAttemptIds).toEqual([attempt]);
        expect(recovered.preparedContinuations?.[attempt]).toEqual(auditSuccessor);
        expect(posts).toBe(1);
        expect(pendingReviews()).toEqual([]);
      }
    }
  });

it.each([false, true])("flush_settlement_audit unrelated rejection attributes its own attempt and explicit retry=%s", async explicitRetry => {
  await enqueuePendingReview(auditReview("reject-a", 9401));
  await enqueuePendingReview(auditReview("waiting-b", 9402));
  const rejectedResponse = auditDeferred<Response>();
  const requested = auditDeferred<void>();
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input, options) => {
    if (String(input).includes("/api/operations/")) return new Response(null, { status: 404 });
    const attempt = JSON.parse(options!.body as string).attempt_id;
    requests.push(attempt);
    if (attempt === "reject-a") { requested.resolve(); return rejectedResponse.promise; }
    return Response.json({ persisted: true });
  }));
  const owner = flushPendingReviews().catch(error => error);
  await requested.promise;
  const waiting = flushPendingReviews(Infinity, false, explicitRetry ? "waiting-b" : undefined, undefined, "waiting-b").catch(error => error);
  rejectedResponse.reject(new Error("A failed"));
  expect(await owner).toMatchObject({ attemptId: "reject-a" });
  const result = await waiting;
  if (explicitRetry) {
    expect(result.persistedAttemptIds).toEqual(["waiting-b"]);
    expect(requests).toEqual(["reject-a", "waiting-b"]);
    expect(pendingReviews().map(item => item.attemptId)).toEqual(["reject-a"]);
  } else {
    expect(result).toMatchObject({ attemptId: "reject-a", flushResult: { persistedAttemptIds: [] } });
    expect(pendingReviews().map(item => item.attemptId)).toEqual(["reject-a", "waiting-b"]);
  }
});

it.each(["persisted", "suppressed", "conflicted"])("flush_settlement_audit target cannot bypass same-card %s predecessor", async predecessor => {
  await enqueuePendingReview(auditReview("independent-before", 9500));
  await enqueuePendingReview({ ...auditReview("same-earlier", 9501, "same-card"),
    ...(predecessor === "suppressed" ? { automaticRecoverySuppressed: "failed" as const } : {}),
    ...(predecessor === "conflicted" ? { state: "conflicted" as const, conflict: { code: "retired", message: "Needs review", retryable: false } } : {}) });
  await enqueuePendingReview(auditReview("same-target", 9502, "same-card"));
  await enqueuePendingReview(auditReview("independent-after", 9503));
  const deliveries: string[] = [];
  vi.stubGlobal("fetch", withMissingReceipts(vi.fn(async (_input, options) => {
    deliveries.push(JSON.parse(options!.body as string).attempt_id);
    return Response.json({ persisted: true });
  })));
  const result = await flushPendingReviews(Infinity, false, undefined, undefined, "same-target");
  if (predecessor === "persisted") expect(result.persistedAttemptIds).toEqual(["same-earlier", "same-target"]);
  else {
    expect(result.persistedAttemptIds).toEqual([]);
    expect(result.conflictedAttemptIds).toEqual(predecessor === "conflicted" ? ["same-target"] : []);
  }
  expect(deliveries).toEqual(predecessor === "persisted" ? ["same-earlier", "same-target"] : []);
  expect(pendingReviews().map(item => item.attemptId)).toContain("independent-before");
  expect(pendingReviews().map(item => item.attemptId)).toContain("independent-after");
});

function heldReviewResponse() {
  let resolveResponse!: (response: Response) => void;
  let rejectResponse!: (error: Error) => void;
  const response = new Promise<Response>((resolve, reject) => { resolveResponse = resolve; rejectResponse = reject; });
  return { response, resolveResponse, rejectResponse };
}

it.each(["success", "failure"])("overlapping_review_flushes_settle_only_their_submitted_attempt: %s", async laterOutcome => {
  const preparedNext = { card_id: "successor-a", queue_entry_id: 104, revision: 1, cycle: 0, local_date: "2026-10-11" };
  const firstReview = { backendId: "a", queueEntryId: 101, attemptId: "overlap-a", outcome: "correct" as const,
    guided: false, preparedNext };
  await enqueuePendingReview(firstReview);
  await enqueuePendingReview({ ...firstReview, backendId: "b", queueEntryId: 102, attemptId: "overlap-b", preparedNext: undefined });
  const firstResponse = heldReviewResponse();
  const secondResponse = heldReviewResponse();
  const thirdResponse = heldReviewResponse();
  const fetcher = vi.fn().mockImplementationOnce(() => firstResponse.response)
    .mockImplementationOnce(() => secondResponse.response).mockImplementationOnce(() => thirdResponse.response);
  vi.stubGlobal("fetch", withMissingReceipts(fetcher));
  const originalFlush = flushPendingReviews();
  const originalOutcome = originalFlush.then(result => ({ result }), error => ({ error }));
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  const firstWaiter = flushPendingReviews(Infinity, false, undefined, undefined, firstReview.attemptId);
  firstResponse.resolveResponse(Response.json({ persisted: true, prepared_continuation: preparedNext }));
  const firstSettlement = await firstWaiter;
  expect(firstSettlement.persistedAttemptIds).toEqual(["overlap-a"]);
  expect(firstSettlement.preparedContinuations).toEqual({ "overlap-a": preparedNext });
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  // Joining after publication must consume A's snapshot only for A.
  expect(await flushPendingReviews(Infinity, false, undefined, undefined, "overlap-a")).toEqual(firstSettlement);
  await enqueuePendingReview({ ...firstReview, backendId: "c", queueEntryId: 103, attemptId: "overlap-c", preparedNext: undefined });
  let thirdSettled = false;
  const thirdWaiter = flushPendingReviews(Infinity, false, undefined, undefined, "overlap-c");
  const thirdOutcome = thirdWaiter.then(result => { thirdSettled = true; return { result }; }, error => ({ error }));
  expect(thirdSettled).toBe(false);
  expect(firstSettlement.preparedContinuations?.["overlap-c"]).toBeUndefined();
  if (laterOutcome === "failure") {
    secondResponse.rejectResponse(new Error("B delivery failed after A persisted"));
    const failedThird = await thirdOutcome;
    expect(failedThird).toMatchObject({ error: { attemptId: "overlap-b", flushResult: { persistedAttemptIds: ["overlap-a"] } } });
    expect(thirdSettled).toBe(false);
    await originalOutcome;
    expect(pendingReviews().map(review => review.attemptId)).toEqual(["overlap-b", "overlap-c"]);
    // A new target flush must not settle C from the earlier shared snapshot.
    const retry = flushPendingReviews(Infinity, false, undefined, undefined, "overlap-c");
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
    thirdResponse.resolveResponse(Response.json({ persisted: true }));
    expect((await retry).persistedAttemptIds).toEqual(["overlap-c"]);
    expect(pendingReviews().map(review => review.attemptId)).toEqual(["overlap-b"]);
  } else {
    secondResponse.resolveResponse(Response.json({ persisted: true }));
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
    expect(thirdSettled).toBe(false);
    thirdResponse.resolveResponse(Response.json({ persisted: true }));
    expect(await thirdOutcome).toMatchObject({ result: { persistedAttemptIds: ["overlap-a", "overlap-b", "overlap-c"] } });
    await originalOutcome;
    expect(pendingReviews()).toEqual([]);
  }
  expect(firstSettlement.persistedAttemptIds).toEqual(["overlap-a"]);
  expect(firstSettlement.preparedContinuations).toEqual({ "overlap-a": preparedNext });
  expect(fetcher.mock.calls.map(([, options]) => JSON.parse(options.body).attempt_id)).toEqual(["overlap-a", "overlap-b", "overlap-c"]);
});

it.each(["success", "rejection", "timeout"])("review_flush_waiters_release_listeners_on_settlement: %s", async outcome => {
  const review = { backendId: "listener-card", queueEntryId: 201, attemptId: "listener-attempt", outcome: "correct" as const, guided: false };
  await enqueuePendingReview(review);
  const heldResponse = heldReviewResponse();
  let requestStarted = false;
  vi.stubGlobal("fetch", withMissingReceipts(vi.fn((_url, options) => {
    requestStarted = true;
    options.signal.addEventListener("abort", () => heldResponse.rejectResponse(options.signal.reason), { once: true });
    return heldResponse.response;
  })));
  if (outcome === "timeout") vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
  const active = flushPendingReviews();
  const activeOutcome = active.catch(error => error);
  const originalAdd = Set.prototype.add;
  const originalDelete = Set.prototype.delete;
  const registrations: { owner: Set<unknown>; listener: unknown }[] = [];
  const removals: { owner: Set<unknown>; listener: unknown }[] = [];
  let registeringWaiter = true;
  // Native wrappers avoid instrumenting Vitest's own Set-based spy bookkeeping.
  Set.prototype.add = function (listener: unknown) {
    if (registeringWaiter && typeof listener === "function") registrations.push({ owner: this, listener });
    return originalAdd.call(this, listener);
  };
  Set.prototype.delete = function (listener: unknown) {
    if (registrations.some(registration => registration.owner === this && registration.listener === listener))
      removals.push({ owner: this, listener });
    return originalDelete.call(this, listener);
  };
  try {
    const waiter = flushPendingReviews(Infinity, false, undefined, undefined, review.attemptId);
    registeringWaiter = false;
    const registration = registrations[0];
    expect(registration).toBeDefined();
    const waiterOutcome = waiter.catch(error => error);
    await vi.waitFor(() => expect(requestStarted).toBe(true));
    if (outcome === "success") heldResponse.resolveResponse(Response.json({ persisted: true }));
    if (outcome === "rejection") heldResponse.rejectResponse(new Error("Transport failed"));
    if (outcome === "timeout") { await vi.waitFor(() => expect(requestStarted).toBe(true)); await vi.advanceTimersByTimeAsync(15_000); }
    const settlement = await waiterOutcome;
    await activeOutcome;
    expect(removals.some(removal => removal.owner === registration.owner && removal.listener === registration.listener)).toBe(true);
    if (outcome === "success") expect(settlement.persistedAttemptIds).toEqual([review.attemptId]);
    else expect(settlement).toBeInstanceOf(ReviewReplayError);
    if (outcome === "timeout") expect(settlement.message).toContain("timed out");
  } finally {
    Set.prototype.add = originalAdd; Set.prototype.delete = originalDelete;
    heldResponse.resolveResponse(Response.json({ persisted: true }));
    await activeOutcome;
    vi.useRealTimers();
  }
});

it("new_review_flush_and_restart_receipt_cannot_reuse_previous_attempt_authorization", async () => {
  const preparedNext = { card_id: "next-a", queue_entry_id: 304, revision: 1, cycle: 0, local_date: "2026-10-11" };
  await enqueuePendingReview({ backendId: "a", queueEntryId: 301, attemptId: "receipt-a", outcome: "correct", guided: false, preparedNext });
  vi.stubGlobal("fetch", withMissingReceipts(vi.fn(async () => Response.json({ persisted: true, prepared_continuation: preparedNext }))));
  await flushPendingReviews();
  await enqueuePendingReview({ backendId: "b", queueEntryId: 302, attemptId: "receipt-b", outcome: "correct", guided: false });
  const heldResponse = heldReviewResponse();
  const fetcher = vi.fn(() => heldResponse.response);
  vi.stubGlobal("fetch", withMissingReceipts(fetcher));
  const active = flushPendingReviews();
  const waiter = flushPendingReviews(Infinity, false, undefined, undefined, "receipt-b");
  heldResponse.resolveResponse(Response.json({ persisted: true, prepared_continuation: preparedNext }));
  const result = await waiter;
  await active;
  expect(result.persistedAttemptIds).toEqual(["receipt-b"]);
  expect(result.preparedContinuations).toEqual({});
  // A restarted module has no in-memory flush; the original receipt is authoritative.
  await enqueuePendingReview({ backendId: "a", queueEntryId: 301, attemptId: "receipt-a", outcome: "correct", guided: false, preparedNext });
  vi.resetModules();
  const restartedOutbox = await import("../../app/lib/review-outbox");
  const receiptReader = vi.fn<typeof fetch>(async () => Response.json({ state: "complete", response: { persisted: true, prepared_continuation: preparedNext } }));
  vi.stubGlobal("fetch", receiptReader);
  const recovered = await restartedOutbox.flushPendingReviews(Infinity, true, undefined, undefined, "receipt-a");
  expect(recovered.persistedAttemptIds).toEqual(["receipt-a"]);
  expect(recovered.preparedContinuations).toEqual({ "receipt-a": preparedNext });
  expect(receiptReader).not.toHaveBeenCalled(); // The original confirmed server receipt is now durable.
});

it("conflicted_review_settlement_never_returns_prepared_authorization", async () => {
  const preparedNext = { card_id: "successor", queue_entry_id: 402, revision: 1, cycle: 0, local_date: "2026-10-11" };
  await enqueuePendingReview({ backendId: "conflicted", queueEntryId: 401, attemptId: "conflicted-attempt",
    outcome: "correct", guided: false, preparedNext });
  const heldResponse = heldReviewResponse();
  vi.stubGlobal("fetch", withMissingReceipts(vi.fn().mockImplementationOnce(() => heldResponse.response)
    .mockResolvedValueOnce(Response.json({ persisted: false,
      conflict: { code: "queue_attempt_unprovable", message: "Needs attention", retryable: false }, prepared_continuation: preparedNext }))));
  const active = flushPendingReviews();
  const waiter = flushPendingReviews(Infinity, false, undefined, undefined, "conflicted-attempt");
  heldResponse.resolveResponse(Response.json({ detail: "Retired", retryable: false }, { status: 409 }));
  const settlement = await waiter;
  await active;
  expect(settlement.persistedAttemptIds).toEqual([]);
  expect(settlement.conflictedAttemptIds).toEqual(["conflicted-attempt"]);
  expect(settlement.preparedContinuations).toEqual({});
  expect(conflictedReviews()[0].attemptId).toBe("conflicted-attempt");
});

beforeEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  notificationModule.clearNotificationHistory();
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function enqueueOpeningEvidenceReview(attemptId: string) {
  const completion = openingEvidenceCheckpointSchema.parse({ attempt_id: attemptId, manifest,
    origin_queue_entry_id: 101, queue_entry_id: 101, started_at: "2026-09-30T12:00:00Z", study_timezone: "UTC",
    events: [{ sequence: 1, decision_index: 0, decision_id: manifest.decisions[0].decision_id,
      expected_uci: "e2e4", kind: "first_response", response_uci: "e2e4",
      observed_at: "2026-09-30T12:00:01Z", disposition: "expected" }],
    terminal: { state: "complete", final_sequence: 1, ended_at: "2026-09-30T12:01:00Z" } });
  await enqueuePendingReview({ backendId: "shadow-card", queueEntryId: 101, outcome: "correct", guided: false,
    attemptId, completedAt: completion.terminal!.ended_at, openingEvidenceCompletion: completion });
  return pendingReviews()[0];
}

describe("optimistic training review outbox", () => {
  it("legacy identity storage failure attributes the retained result before any request", async () => {
    const original = JSON.stringify([{ backendId: "legacy-a", queueEntryId: 71, outcome: "correct", guided: false }]);
    localStorage.setItem("tempo-pending-training-reviews-v1", original);
    const storageFailure = new Error("Storage unavailable");
    const write = interceptIntentWrites(() => { throw storageFailure; });
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    try {
      await expect(initializeStudyIntentStorage(true)).rejects.toMatchObject({ backendId: "legacy-a", queueEntryId: 71,
        attemptId: "legacy-online:71", cause: storageFailure });
      expect(localStorage.getItem("tempo-pending-training-reviews-v1")).toBe(original);
      expect(fetchMock).not.toHaveBeenCalled();
    } finally { write.mockRestore(); }
  });

  it("review timeout also bounds deferred operation receipt polling", async () => {
    await enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: false });
    const fetchMock = vi.fn().mockResolvedValueOnce(Response.json({ operation_id: "pending-a" }, { status: 202 }))
      .mockImplementationOnce((_input, options) => new Promise<Response>((_resolve, reject) => {
        options.signal.addEventListener("abort", () => reject(options.signal.reason));
      }));
    vi.stubGlobal("fetch", fetchMock);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    try {
      const saving = flushPendingReviews();
      const rejected = expect(saving).rejects.toMatchObject({ queueEntryId: 17, message: expect.stringContaining("timed out") });
      await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
      await vi.advanceTimersByTimeAsync(15_000);
      await rejected;
      expect(pendingReviews()).toHaveLength(1);
      expect(fetchMock.mock.calls[1][1].signal.aborted).toBe(true);
    } finally { vi.useRealTimers(); }
  });
  it("legacy reload persists deterministic identity without inventing a completion time", async () => {
    localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([
      { backendId: "legacy-card", queueEntryId: 71, outcome: "correct", guided: false },
    ]));
  await initializeStudyIntentStorage(true);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "historical rejection" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ detail: "busy" }, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", withMissingReceipts(fetchMock));
    await expect(flushPendingReviews()).rejects.toMatchObject({ status: 503, attemptId: "legacy-online:71" });
    expect(pendingReviews()[0]).toMatchObject({ attemptId: "legacy-online:71", state: "reconciling" });
    expect(pendingReviews()[0].completedAt).toBeUndefined();
    await flushPendingReviews();
    const bodies = fetchMock.mock.calls.map(([, options]) => JSON.parse(options.body));
    expect(bodies.every(body => body.attempt_id === "legacy-online:71" && body.recorded_at === undefined)).toBe(true);
    expect(fetchMock.mock.calls[1][1].headers).toEqual(fetchMock.mock.calls[2][1].headers);
  });

  it("explicit conflict retry changes transport identity while preserving completed evidence", async () => {
    const review = { backendId: "changed-card", queueEntryId: 72, outcome: "again" as const, guided: true,
      attemptId: "immutable-attempt", completedAt: "2026-09-30T12:00:00Z", expectedRevision: 3,
      state: "conflicted", reconciliationSequence: 1,
      conflict: { code: "card_revision_changed", message: "Card changed", retryable: false } };
    localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([review]));
  await initializeStudyIntentStorage(true);
    await retryReviewConflict(review.attemptId);
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", withMissingReceipts(fetchMock));
    await flushPendingReviews();
    expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toBe("review-reconcile:immutable-attempt:2");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      outcome: "again", guided: true, queue_entry_id: 72, attempt_id: review.attemptId,
      recorded_at: review.completedAt, expected_revision: 3,
    });
  });

  it("same-card dependent result waits while an unrelated card persists", async () => {
    localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([
      { backendId: "card-a", queueEntryId: 70, outcome: "correct", guided: false, attemptId: "older-a", state: "conflicted",
        conflict: { code: "card_revision_changed", message: "Card changed", retryable: false } },
    ]));
  await initializeStudyIntentStorage(true);
    await enqueuePendingReview({ backendId: "card-a", queueEntryId: 71, outcome: "again", guided: false });
    await enqueuePendingReview({ backendId: "card-b", queueEntryId: 72, outcome: "correct", guided: false });
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", withMissingReceipts(fetchMock));
    await flushPendingReviews();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("card-b/review");
    expect(conflictedReviews()).toHaveLength(2);
    expect(conflictedReviews()[1].conflict?.code).toBe("earlier_review_conflict");
  });

  it("failed atomic conflict write preserves A and pauses B without data loss", async () => {
    await enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: false });
    await enqueuePendingReview({ backendId: "card-b", queueEntryId: 18, outcome: "correct", guided: false });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "stale" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ persisted: false, conflict: { code: "queue_attempt_unprovable", message: "No proof", retryable: false } }));
    vi.stubGlobal("fetch", withMissingReceipts(fetchMock));
    const storageFailure = new DOMException("Storage full", "QuotaExceededError");
    const write = interceptIntentWrites(record => { if (record.state === "conflicted") throw storageFailure; });
    try {
      await expect(flushPendingReviews()).rejects.toMatchObject({ queueEntryId: 17, classification: "storage", cause: { cause: storageFailure } });
      expect(pendingReviews().map(review => review.queueEntryId)).toEqual([17, 18]);
      expect(conflictedReviews()).toEqual([]);
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally { write.mockRestore(); }
  });

  it("deferred failed receipt retains conflict classification and reconciles the same attempt", async () => {
    await enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: false });
    const attemptId = pendingReviews()[0].attemptId;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ operation_id: "old-save" }, { status: 202 }))
      .mockResolvedValueOnce(Response.json({ state: "failed", error: { status_code: 409, code: "queue_attempt_retired", retryable: false, detail: "Retired attempt" } }))
      .mockResolvedValueOnce(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", fetchMock);
    await flushPendingReviews();
    expect(String(fetchMock.mock.calls[2][0])).toContain("/review/reconcile");
    expect(JSON.parse(fetchMock.mock.calls[2][1].body).attempt_id).toBe(attemptId);
    expect(pendingReviews()).toEqual([]);
  });
  it("terminal stale A remains inspectable while independent B and C persist", async () => {
    for (const [backendId, queueEntryId] of [["card-a", 17], ["card-b", 18], ["card-c", 19]] as const)
      await enqueuePendingReview({ backendId, queueEntryId, outcome: "correct", guided: false });
    const initialAttempts = pendingReviews();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const endpoint = String(input);
      if (endpoint.endsWith("/card-a/review"))
        return Response.json({ detail: "This queue attempt is no longer available", code: "queue_attempt_unavailable", retryable: false }, { status: 409 });
      if (endpoint.endsWith("/card-a/review/reconcile"))
        return Response.json({ persisted: false, conflict: { code: "queue_attempt_unprovable", message: "The original queue context is unavailable.", retryable: false } });
      return Response.json({ persisted: true });
    });
    vi.stubGlobal("fetch", withMissingReceipts(fetchMock));
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    const retained = conflictedReviews();
    expect(retained).toEqual([expect.objectContaining({ ...initialAttempts[0], state: "conflicted" })]);
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      expect.stringContaining("/card-a/review"), expect.stringContaining("/card-a/review/reconcile"),
      expect.stringContaining("/card-b/review"), expect.stringContaining("/card-c/review"),
    ]);
  });

  it("replay rejection identifies old A rather than the current B", async () => {
    await enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: false });
    await enqueuePendingReview({ backendId: "card-b", queueEntryId: 18, outcome: "correct", guided: false });
    const attemptId = pendingReviews()[0].attemptId;
    vi.stubGlobal("fetch", withMissingReceipts(vi.fn().mockResolvedValue(Response.json({ detail: "busy" }, { status: 503 }))));
    await expect(flushPendingReviews()).rejects.toMatchObject({
      backendId: "card-a", queueEntryId: 17, attemptId, status: 503, retryable: true,
    });
  });
  it("confirmed guided review clears its earlier contextual failure marker", async () => {
    await enqueueTrainingFailure(17, "card-a", 1);
    await enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, expectedRevision: 1,
      outcome: "again", guided: true });
    vi.stubGlobal("fetch", withMissingReceipts(vi.fn().mockResolvedValue(Response.json({ persisted: true }))));
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    expect(pendingTrainingFailures()).toEqual([]);
  });
  it("retains a failed review for retry after reload without duplicating the queue entry", async () => {
    const review = { backendId: "card-a", queueEntryId: 17, outcome: "correct" as const, guided: false };
    await enqueuePendingReview(review);
    await enqueuePendingReview(review);
    expect(pendingReviews()).toEqual([expect.objectContaining(review)]);
    vi.stubGlobal("fetch", withMissingReceipts(vi.fn().mockResolvedValueOnce(Response.json({ detail: "busy" }, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ persisted: true }))));
    await expect(flushPendingReviews()).rejects.toThrow("busy");
    expect(pendingReviews()).toEqual([expect.objectContaining(review)]);
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
  });

  it("replays failed-attempt marking before grading and preserves order", async () => {
    await enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "again", guided: true });
    await enqueuePendingReview({ backendId: "card-b", queueEntryId: 18, outcome: "correct", guided: false });
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", withMissingReceipts(fetchMock));
    await flushPendingReviews();
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringContaining("/api/queue/entries/17/fail"),
      expect.stringContaining("/api/cards/card-a/review"),
      expect.stringContaining("/api/cards/card-b/review"),
    ]);
    expect(pendingReviews()).toEqual([]);
  });

  it("stale guided failure marking still saves the guided review once", async () => {
    await enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: true });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", withMissingReceipts(fetchMock));

    await flushPendingReviews();

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringContaining("/api/queue/entries/17/fail"),
      expect.stringContaining("/api/cards/card-a/review"),
    ]);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ guided: true, queue_entry_id: 17 });
    expect(pendingReviews()).toEqual([]);
  });

  it("unprovable guided result becomes a durable nonblocking conflict", async () => {
    const review = { backendId: "card-a", queueEntryId: 17, outcome: "correct" as const, guided: true };
    await enqueuePendingReview(review);
    vi.stubGlobal("fetch", withMissingReceipts(vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer available" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ persisted: false, conflict: { code: "queue_attempt_unprovable", message: "Original attempt unavailable", retryable: false } }))));

    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    expect(conflictedReviews()).toEqual([expect.objectContaining({ ...review, state: "conflicted" })]);
  });

  it("drains a review appended while an earlier review request is still in flight", async () => {
    await enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: false });
    let finishFirst: ((response: Response) => void) | undefined;
    const savedEntries: number[] = [];
    vi.stubGlobal("fetch", withMissingReceipts(vi.fn((input) => {
      const queueEntryId = String(input).includes("card-a") ? 17 : 18;
      savedEntries.push(queueEntryId);
      return queueEntryId === 17
        ? new Promise<Response>((resolve) => { finishFirst = resolve; })
        : Promise.resolve(Response.json({ persisted: true }));
    })));
    const saving = flushPendingReviews();
    expect(flushPendingReviews()).toBe(saving);
    await enqueuePendingReview({ backendId: "card-b", queueEntryId: 18, outcome: "correct", guided: false });
    await vi.waitFor(() => expect(finishFirst).toBeTypeOf("function"));
    finishFirst!(Response.json({ persisted: true }));
    await saving;
    expect(savedEntries).toEqual([17, 18]);
    expect(pendingReviews()).toEqual([]);
  });

  it("times out an unresponsive review save and keeps it available for retry", async () => {
    const review = { backendId: "card-stalled", queueEntryId: 19, outcome: "correct" as const, guided: false };
    await enqueuePendingReview(review);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    let requestStarted = false;
    vi.stubGlobal("fetch", withMissingReceipts(vi.fn((_input, options) => new Promise<Response>((_resolve, reject) => {
      requestStarted = true;
      options.signal.addEventListener("abort", () => reject(options.signal.reason));
    }))));
    try {
      const saving = flushPendingReviews();
      const rejected = expect(saving).rejects.toThrow(/timed out/i);
      await vi.waitFor(() => expect(requestStarted).toBe(true));
      await vi.advanceTimersByTimeAsync(15_000);
      await rejected;
      expect(pendingReviews()).toEqual([expect.objectContaining(review)]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a Celery-accepted review until its operation receipt confirms the save", async () => {
    const review = { backendId: "card-pending", queueEntryId: 81, outcome: "correct" as const, guided: false };
    await enqueuePendingReview(review);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ operation_id: "review:81", state: "pending" }, { status: 202 }))
      .mockResolvedValueOnce(Response.json({ operation_id: "review:81", state: "pending" }))
      .mockResolvedValueOnce(Response.json({ operation_id: "review:81", state: "complete", response: { persisted: true } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(flushPendingReviews()).rejects.toThrow("still pending");
    expect(pendingReviews()).toEqual([expect.objectContaining(review)]);
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toMatch(/^review-attempt:/);
    expect(String(fetchMock.mock.calls[2][0])).toContain(encodeURIComponent(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]));
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
  });

  it("retries one phone review identity after an uncertain save and clears it only on confirmation", async () => {
    await enqueuePendingReview({ backendId: "phone-card", queueEntryId: 91, outcome: "correct", guided: false });
    const saved = pendingReviews()[0];
    expect(saved.attemptId).toBeTruthy();
    expect(saved.completedAt).toBeTruthy();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "Temporarily unavailable" }, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ persisted: true, review_id: 32,
        reconciliation: "chronological", warning: null }));
    vi.stubGlobal("fetch", withMissingReceipts(fetchMock));
    await expect(flushPendingReviews()).rejects.toThrow("Temporarily unavailable");
    expect(pendingReviews()[0]).toEqual(saved);
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toBe(fetchMock.mock.calls[1][1].headers["Idempotency-Key"]);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({
      attempt_id: saved.attemptId, recorded_at: saved.completedAt,
    });
  });

  it("keeps a phone review when the server omits persistence confirmation", async () => {
    await enqueuePendingReview({ backendId: "phone-card", queueEntryId: 92, outcome: "again", guided: false });
    vi.stubGlobal("fetch", withMissingReceipts(vi.fn().mockResolvedValue(Response.json({}))));
    await expect(flushPendingReviews()).rejects.toThrow("did not confirm");
    expect(pendingReviews()).toHaveLength(1);
  });
  it.each(["QuotaExceededError", "NS_ERROR_DOM_QUOTA_REACHED"])("AS-16 evidence outbox quota falls back to durable aggregate-only review (%s)", async quotaName => {
    const original = await enqueueOpeningEvidenceReview("quota-attempt"); const unchanged = structuredClone(original);
    await resetStudyStorage();
    const retainEvidence = vi.spyOn(openingEvidenceJournal, "retainOpeningEvidenceForStorageFallback");
    const writes: unknown[] = [];
    const write = interceptIntentWrites(record => {
      writes.push(record.data);
      if ((record.data as typeof original)?.openingEvidenceCompletion) throw new DOMException("Storage quota exceeded", quotaName);
    });
    await enqueuePendingReview(original); write.mockRestore();
    expect(original).toEqual(unchanged);
    const aggregate = { ...unchanged }; delete aggregate.openingEvidenceCompletion;
    expect(pendingReviews()).toEqual([{ ...aggregate, evidenceFallbackReason: "local_storage_quota" }]);
    expect(writes).toEqual([unchanged, pendingReviews()[0]]);
    expect(retainEvidence).toHaveBeenCalledExactlyOnceWith(unchanged.openingEvidenceCompletion);
    const request = vi.fn(async (_url, options: RequestInit) => {
      expect(new Headers(options.headers).get("Idempotency-Key")).toBe("review-attempt:quota-attempt:aggregate-only");
      expect(JSON.parse(options.body as string)).toEqual({ outcome: original.outcome, guided: original.guided,
        queue_entry_id: original.queueEntryId, attempt_id: original.attemptId, recorded_at: original.completedAt });
      return Response.json({ persisted: true });
    });
    vi.stubGlobal("fetch", withMissingReceipts(request)); await flushPendingReviews();
    expect(request).toHaveBeenCalledOnce(); expect(pendingReviews()).toEqual([]);
  });

  it.each(["QuotaExceededError", "NS_ERROR_DOM_QUOTA_REACHED"])("AS-16 aggregate-only outbox storage failure remains blocking and retryable (%s)", async quotaName => {
    const original = await enqueueOpeningEvidenceReview("quota-blocked"); await resetStudyStorage();
    const request = vi.fn(); vi.stubGlobal("fetch", withMissingReceipts(request));
    const failure = new DOMException("All review storage is full", quotaName); let writes = 0;
    const write = interceptIntentWrites(() => { writes++; throw failure; });
    await expect(enqueuePendingReview(original)).rejects.toMatchObject({ cause: failure });
    expect(writes).toBe(2); expect(pendingReviews()).toEqual([]);
    await flushPendingReviews(); expect(request).not.toHaveBeenCalled();
    write.mockRestore(); await enqueuePendingReview(original); expect(pendingReviews()).toEqual([original]);
  });

  it.each(["QuotaExceededError", "NS_ERROR_DOM_QUOTA_REACHED"])("AS-16 quota fallback reload and ambiguous retries retain the compact payload and key (%s)", async quotaName => {
    const original = await enqueueOpeningEvidenceReview("quota-retry"); await resetStudyStorage();
    const write = interceptIntentWrites(record => { if ((record.data as typeof original)?.openingEvidenceCompletion) throw new DOMException("Evidence is too large", quotaName); });
    await enqueuePendingReview(original); write.mockRestore();
    const durable = pendingReviews()[0];
    const posts: RequestInit[] = [];
    vi.stubGlobal("fetch", withMissingReceipts(vi.fn(async (_url, options: RequestInit) => {
      posts.push(options); if (posts.length === 1) throw new TypeError("Lost response"); return Response.json({ persisted: true });
    })));
    await expect(flushPendingReviews()).rejects.toThrow("Lost response");
    expect(pendingReviews()).toEqual([durable]);
    closeStudyIntentStorage(); await initializeStudyIntentStorage(); await flushPendingReviews();
    expect(posts[1].body).toBe(posts[0].body);
    expect(new Headers(posts[1].headers).get("Idempotency-Key")).toBe("review-attempt:quota-retry:aggregate-only");
    expect(pendingReviews()).toEqual([]);
  });

  it.each(["SecurityError", "InvalidStateError", "NotAllowedError"])("AS-16 denied initial review storage never switches to aggregate-only (%s)", async storageErrorName => {
    const original = await enqueueOpeningEvidenceReview("denied-initial"); await resetStudyStorage();
    const denied = new DOMException("Storage access denied", storageErrorName); let writes = 0;
    const write = interceptIntentWrites(() => { writes++; throw denied; });
    await expect(enqueuePendingReview(original)).rejects.toMatchObject({ cause: denied });
    expect(writes).toBe(1); expect(pendingReviews()).toEqual([]); write.mockRestore();
  });

  it.each([new Error("Unknown storage failure"), Object.assign(new Error("Unverified quota failure"), { name: "NS_ERROR_DOM_QUOTA_REACHED" })])("AS-16 unknown initial review storage failure remains blocking (%s)", async storageFailure => {
    const original = await enqueueOpeningEvidenceReview("unknown-storage"); await resetStudyStorage(); let writes = 0;
    const write = interceptIntentWrites(() => { writes++; throw storageFailure; });
    await expect(enqueuePendingReview(original)).rejects.toMatchObject({ cause: storageFailure });
    expect(writes).toBe(1); expect(pendingReviews()).toEqual([]); write.mockRestore();
  });

  it.each([
    ["immediate", "opening_evidence_conflict"], ["immediate", "opening_evidence_unavailable"],
    ["deferred", "opening_evidence_conflict"], ["deferred", "opening_evidence_unavailable"],
  ] as const)("AS-16 %s %s primary rejection marker failure prevents fallback and preserves retry", async (timing, code) => {
    vi.stubGlobal("indexedDB", undefined);
    const original = await enqueueOpeningEvidenceReview(`required-marker-${timing}-${code}`);
    const storageFailure = new DOMException("Primary review storage denied", "SecurityError");
    const primaryWrite = interceptIntentWrites(record => {
      if ((record.data as { evidenceRejected?: string } | undefined)?.evidenceRejected !== undefined) throw storageFailure;
    });
    const detail = { code, message: "Rejected immutable evidence", aggregate_review_allowed: true };
    const receiptMessage = `409: ${code}: Rejected immutable evidence`;
    const posts: RequestInit[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url, options?: RequestInit) => {
      if (options?.method !== "POST" && decodeURIComponent(String(_url)).endsWith(":aggregate-only")) return new Response(null, { status: 404 });
      if (options?.method !== "POST") return Response.json({ state: "failed",
        error: { status_code: 409, message: receiptMessage, detail } });
      posts.push(options);
      if (JSON.parse(options.body as string).opening_evidence_completion)
        return timing === "immediate" ? Response.json({ detail }, { status: 409 })
          : Response.json({ operation_id: `review-attempt:${original.attemptId}`, state: "pending" }, { status: 202 });
      expect(pendingReviews()).toEqual([{ ...original,
        evidenceRejected: receiptMessage }]);
      return Response.json({ persisted: true });
    }));

    await expect(flushPendingReviews()).rejects.toMatchObject({ cause: storageFailure });
    expect(posts).toHaveLength(1);
    expect(pendingReviews()).toEqual([original]);
    expect(localStorage.getItem("tempo-rejected-opening-reviews-v1")).toBeNull();

    primaryWrite.mockRestore();
    await flushPendingReviews();
    expect(posts).toHaveLength(2);
    const originalKey = new Headers(posts[0].headers).get("Idempotency-Key");
    expect(new Headers(posts[1].headers).get("Idempotency-Key")).toBe(`${originalKey}:aggregate-only`);
    const aggregateBody = JSON.parse(posts[0].body as string); delete aggregateBody.opening_evidence_completion;
    expect(JSON.parse(posts[1].body as string)).toEqual(aggregateBody);
    expect(pendingReviews()).toEqual([]);
  });

  it.each(["denied read", "malformed data"])("AS-16 diagnostic archive %s cannot block aggregate-only delivery", async (failure) => {
    vi.stubGlobal("indexedDB", undefined);
    const original = await enqueueOpeningEvidenceReview(`archive-${failure}`);
    if (failure === "malformed data") localStorage.setItem("tempo-rejected-opening-reviews-v1", "invalid JSON");
    else {
      const originalGetItem = Storage.prototype.getItem;
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, key) {
        if (key === "tempo-rejected-opening-reviews-v1") throw new DOMException("Archive read denied", "SecurityError");
        return originalGetItem.call(this, key);
      });
    }
    const fetchMock = vi.fn(async (_url, options: RequestInit) => {
      if (JSON.parse(options.body as string).opening_evidence_completion) return Response.json({ detail: {
        code: "opening_evidence_conflict", message: "Rejected evidence", aggregate_review_allowed: true,
      } }, { status: 409 });
      expect(pendingReviews()).toEqual([{ ...original, evidenceRejected: "Rejected evidence" }]);
      return Response.json({ persisted: true });
    });
    vi.stubGlobal("fetch", withMissingReceipts(fetchMock));
    await flushPendingReviews();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(pendingReviews()).toEqual([]);
  });

  it("AS-16 diagnostic warning failure cannot block aggregate-only delivery", async () => {
    vi.stubGlobal("indexedDB", undefined);
    const original = await enqueueOpeningEvidenceReview("archive-warning-failure");
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === "tempo-rejected-opening-reviews-v1") throw new DOMException("Storage quota exceeded", "QuotaExceededError");
      return originalSetItem.call(this, key, value);
    });
    const warning = vi.spyOn(notificationModule, "publishNotification").mockImplementation(() => {
      throw new Error("Notification subscriber failed");
    });
    const fetchMock = vi.fn(async (_url, options: RequestInit) => {
      if (JSON.parse(options.body as string).opening_evidence_completion) return Response.json({ detail: {
        code: "opening_evidence_unavailable", message: "Rejected evidence", aggregate_review_allowed: true,
      } }, { status: 409 });
      expect(pendingReviews()).toEqual([{ ...original, evidenceRejected: "Rejected evidence" }]);
      return Response.json({ persisted: true });
    });
    vi.stubGlobal("fetch", withMissingReceipts(fetchMock));
    await flushPendingReviews();
    expect(warning.mock.calls.filter(([input]) => input.key === `opening-evidence-archive:${original.attemptId}`))
      .toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(pendingReviews()).toEqual([]);
  });

  it("AS-16 failed diagnostic archive retains aggregate-only identity across uncertain save retries", async () => {
    vi.stubGlobal("indexedDB", undefined);
    const original = await enqueueOpeningEvidenceReview("archive-uncertain-fallback");
    const originalSetItem = Storage.prototype.setItem;
    const archiveWrite = vi.fn(() => { throw new DOMException("Storage quota exceeded", "QuotaExceededError"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === "tempo-rejected-opening-reviews-v1") return archiveWrite();
      return originalSetItem.call(this, key, value);
    });
    const posts: RequestInit[] = [];
    vi.stubGlobal("fetch", withMissingReceipts(vi.fn(async (_url, options: RequestInit) => {
      posts.push(options);
      if (posts.length === 1) return Response.json({ detail: {
        code: "opening_evidence_conflict", message: "Rejected evidence", aggregate_review_allowed: true,
      } }, { status: 409 });
      if (posts.length === 2) throw new TypeError("Connection lost after submission");
      expect(pendingReviews()).toEqual([{ ...original, evidenceRejected: "Rejected evidence" }]);
      return Response.json({ persisted: true });
    })));

    await expect(flushPendingReviews()).rejects.toThrow("Connection lost after submission");
    expect(pendingReviews()).toEqual([{ ...original, evidenceRejected: "Rejected evidence" }]);
    await flushPendingReviews();
    expect(posts).toHaveLength(3);
    expect(posts[2].body).toBe(posts[1].body);
    expect(JSON.parse(posts[2].body as string)).not.toHaveProperty("opening_evidence_completion");
    const originalKey = new Headers(posts[0].headers).get("Idempotency-Key");
    for (const fallback of posts.slice(1))
      expect(new Headers(fallback.headers).get("Idempotency-Key")).toBe(`${originalKey}:aggregate-only`);
    expect(archiveWrite).toHaveBeenCalledOnce();
    expect(notificationModule.notifications().filter((notice) => notice.key === `opening-evidence-archive:${original.attemptId}`))
      .toEqual([expect.objectContaining({ occurrenceCount: 1 })]);
    expect(pendingReviews()).toEqual([]);
  });

  it.each([
    ["immediate", "opening_evidence_conflict"], ["immediate", "opening_evidence_unavailable"],
    ["deferred", "opening_evidence_conflict"], ["deferred", "opening_evidence_unavailable"],
  ] as const)("AS-16 %s %s diagnostic archive quota failure still saves the aggregate review", async (timing, code) => {
    vi.stubGlobal("indexedDB", undefined);
    const original = await enqueueOpeningEvidenceReview(`archive-quota-${timing}-${code}`);
    const originalSetItem = Storage.prototype.setItem;
    const archiveWrite = vi.fn(() => { throw new DOMException("Storage quota exceeded", "QuotaExceededError"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === "tempo-rejected-opening-reviews-v1") return archiveWrite();
      return originalSetItem.call(this, key, value);
    });
    const detail = { code, message: "Rejected immutable evidence", aggregate_review_allowed: true };
    const receiptMessage = `409: ${code}: Rejected immutable evidence`;
    const rejection = timing === "immediate" ? detail.message : receiptMessage;
    const posts: RequestInit[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url, options?: RequestInit) => {
      if (options?.method !== "POST") return Response.json({ state: "failed",
        error: { status_code: 409, message: receiptMessage, detail } });
      posts.push(options);
      if (posts.length === 1) return timing === "immediate" ? Response.json({ detail }, { status: 409 })
        : Response.json({ operation_id: `review-attempt:${original.attemptId}`, state: "pending" }, { status: 202 });
      // The required marker must already be durable when fallback crosses the transport boundary.
      expect(pendingReviews()).toEqual([{ ...original, evidenceRejected: rejection }]);
      expect(localStorage.getItem("tempo-rejected-opening-reviews-v1")).toBeNull();
      return Response.json({ persisted: true });
    }));

    await expect(flushPendingReviews()).resolves.toEqual({ persistedAttemptIds: [original.attemptId], conflictedAttemptIds: [] });
    expect(archiveWrite).toHaveBeenCalledOnce();
    expect(posts).toHaveLength(2);
    const firstBody = JSON.parse(posts[0].body as string);
    expect(firstBody.opening_evidence_completion).toEqual(original.openingEvidenceCompletion);
    const aggregateBody = { ...firstBody }; delete aggregateBody.opening_evidence_completion;
    expect(JSON.parse(posts[1].body as string)).toEqual(aggregateBody);
    const firstKey = new Headers(posts[0].headers).get("Idempotency-Key");
    expect(new Headers(posts[1].headers).get("Idempotency-Key")).toBe(`${firstKey}:aggregate-only`);
    expect(pendingReviews()).toEqual([]);
    expect(original.evidenceRejected).toBeUndefined();
    expect(notificationModule.notifications()).toContainEqual(expect.objectContaining({
      key: `opening-evidence-archive:${original.attemptId}`, severity: "warning",
    }));
  });

  it.each([
    ["immediate", "opening_evidence_conflict"], ["immediate", "opening_evidence_unavailable"],
    ["deferred", "opening_evidence_conflict"], ["deferred", "opening_evidence_unavailable"],
  ] as const)("AS-16 %s %s archives rejected journal before confirmed aggregate-only save", async (timing, code) => {
    vi.stubGlobal("indexedDB", undefined);
    const completion=openingEvidenceCheckpointSchema.parse({ attempt_id:"rejected-journal", manifest,
      origin_queue_entry_id:101,queue_entry_id:101,started_at:"2026-09-30T12:00:00Z",study_timezone:"UTC",
      events:[{sequence:1,decision_index:0,decision_id:manifest.decisions[0].decision_id,
        expected_uci:"e2e4",kind:"first_response",response_uci:"e2e4",observed_at:"2026-09-30T12:00:01Z",disposition:"expected"}],
      terminal:{state:"complete",final_sequence:1,ended_at:"2026-09-30T12:01:00Z"} });
    await enqueuePendingReview({backendId:"shadow-card",queueEntryId:101,outcome:"correct",guided:false,
      attemptId:completion.attempt_id,openingEvidenceCompletion:completion});
    const original=pendingReviews()[0];
    const detail={code,message:"Rejected immutable evidence",aggregate_review_allowed:true};
    const storedMessage=`409: {'code': '${code}', 'message': 'Rejected immutable evidence', 'aggregate_review_allowed': True}`;
    const rejection=timing==="immediate"?detail.message:storedMessage;
    const posts: RequestInit[]=[];
    vi.stubGlobal("fetch",vi.fn(async (_url,options?:RequestInit) => {
      if (options?.method!=="POST") return Response.json({state:"failed",error:{status_code:409,message:storedMessage,detail}});
      posts.push(options);
      if (posts.length===1) return timing==="immediate"?Response.json({detail},{status:409})
        :Response.json({operation_id:"review-attempt:rejected-journal",state:"pending"},{status:202});
      expect(JSON.parse(localStorage.getItem("tempo-rejected-opening-reviews-v1") ?? "[]")).toEqual([
        {...original,evidenceRejected:rejection},
      ]);
      return Response.json({persisted:true});
    }));
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    expect(JSON.parse(localStorage.getItem("tempo-rejected-opening-reviews-v1") ?? "[]")).toEqual([
      {...original,evidenceRejected:rejection},
    ]);
    expect(posts).toHaveLength(2);
    const firstBody=JSON.parse(posts[0].body as string);
    expect(firstBody.opening_evidence_completion).toEqual(completion);
    const aggregateBody={...firstBody};delete aggregateBody.opening_evidence_completion;
    expect(JSON.parse(posts[1].body as string)).toEqual(aggregateBody);
    const firstKey=new Headers(posts[0].headers).get("Idempotency-Key");
    expect(new Headers(posts[1].headers).get("Idempotency-Key")).toBe(`${firstKey}:aggregate-only`);
  });

  it("unresolved guided review remains inspectable when reconciliation rejects it", async () => {
    const review = { backendId: "card-a", queueEntryId: 17, outcome: "correct" as const, guided: true };
    await enqueuePendingReview(review);
    vi.stubGlobal("fetch", withMissingReceipts(vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer available" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ persisted: false, conflict: {
        code: "queue_attempt_unprovable", message: "This queue attempt is no longer available", retryable: false,
      } }))));

    const saved = pendingReviews()[0];
    expect(await flushPendingReviews()).toEqual({ persistedAttemptIds: [], conflictedAttemptIds: [saved.attemptId] });
    expect(pendingReviews()).toEqual([]);
    expect(conflictedReviews()).toEqual([expect.objectContaining({ ...review, state: "conflicted" })]);
  });
  it("reconciliation preserves valid opening evidence and original result across retry and reload", async () => {
    const original = await enqueueOpeningEvidenceReview("evidence-reconcile");
    const posts: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", withMissingReceipts(vi.fn(async (url: string, init: RequestInit) => {
      posts.push({ url, init });
      if (posts.length === 1) return Response.json({ detail: "Historical failed receipt", retryable: false }, { status: 409 });
      if (posts.length === 2) throw new TypeError("Lost reconciliation response");
      return Response.json({ persisted: true });
    })));
    await expect(flushPendingReviews()).rejects.toThrow("Lost reconciliation response");
    const saved = pendingReviews();
    expect(saved).toEqual([{ ...original, state: "reconciling", reconciliationSequence: 1 }]);
    closeStudyIntentStorage();
    await initializeStudyIntentStorage();
    await flushPendingReviews();
    expect(posts).toHaveLength(3);
    for (const post of posts) expect(JSON.parse(post.init.body as string)).toEqual({
      outcome: original.outcome, guided: original.guided, queue_entry_id: original.queueEntryId,
      attempt_id: original.attemptId, recorded_at: original.completedAt,
      opening_evidence_completion: original.openingEvidenceCompletion,
    });
    expect(posts[1].url).toContain("/review/reconcile");
    expect(posts[2].init.body).toBe(posts[1].init.body);
    expect(posts.slice(1).map(post => new Headers(post.init.headers).get("Idempotency-Key")))
      .toEqual(["review-reconcile:evidence-reconcile:1", "review-reconcile:evidence-reconcile:1"]);
    expect(pendingReviews()).toEqual([]);
  });

});


it("terminal review survives passive flushes and explicit retry preserves its complete envelope", async () => {
  const original = await enqueueOpeningEvidenceReview("terminal-evidence-attempt");
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ detail: "Invalid review" }, { status: 422 }));
  vi.stubGlobal("fetch", withMissingReceipts(fetcher));
  await expect(flushPendingReviews()).rejects.toMatchObject({ classification: "failed" });
  expect(pendingReviews()[0]).toMatchObject({ ...original, automaticRecoverySuppressed: "failed" });
  const retained = localStorage.getItem("tempo-pending-training-reviews-v1");
  await flushPendingReviews(); await flushPendingReviews(1, true);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(localStorage.getItem("tempo-pending-training-reviews-v1")).toBe(retained);
  fetcher.mockResolvedValueOnce(new Response(null, { status: 404 })).mockResolvedValueOnce(Response.json({ persisted: true }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingReviews(Infinity, true, original.attemptId);
  expect(fetcher.mock.calls[2][1].body).toBe(fetcher.mock.calls[0][1].body);
  expect(fetcher.mock.calls[2][1].headers).toEqual(fetcher.mock.calls[0][1].headers);
  expect(pendingReviews()).toEqual([]);
});

it.each([
  [409, false, "conflict"], [409, true, "transient"], [422, false, "failed"],
  [503, true, "transient"], [408, true, "transient"], [429, true, "transient"],
] as const)("review replay HTTP %s retryable=%s classifies as %s", (status, retryable, classification) => {
  expect(new ReviewReplayError("Save rejected", "/review", { backendId: "card", queueEntryId: 1,
    attemptId: "immutable", outcome: "again", guided: false }, status, undefined, retryable).classification).toBe(classification);
});


it("explicit terminal retry becoming transient releases only its attempt for automatic recovery", async () => {
  const first = { backendId: "card-a", queueEntryId: 1, attemptId: "first", outcome: "again" as const,
    guided: false, completedAt: "2026-10-08T12:00:00Z" };
  const second = { ...first, backendId: "card-b", queueEntryId: 2, attemptId: "second" };
  localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([
    { ...first, automaticRecoverySuppressed: "failed" }, { ...second, automaticRecoverySuppressed: "failed" },
  ]));
  await initializeStudyIntentStorage(true);
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(null, { status: 404 }))
    .mockResolvedValueOnce(Response.json({ detail: "Busy" }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ state: "complete", response: { persisted: true } }));
  vi.stubGlobal("fetch", fetcher);
  await expect(flushPendingReviews(Infinity, true, first.attemptId)).rejects.toMatchObject({ classification: "transient" });
  expect(pendingReviews()).toEqual([first, { ...second, automaticRecoverySuppressed: "failed" }]);
  await flushPendingReviews(1, true);
  expect(pendingReviews()).toEqual([{ ...second, automaticRecoverySuppressed: "failed" }]);
});

it("explicit retry cannot bypass an earlier suppressed result for the same card", async () => {
  const original = { backendId: "card", queueEntryId: 1, attemptId: "earlier", outcome: "again", guided: false,
    automaticRecoverySuppressed: "failed" };
  const later = { ...original, queueEntryId: 2, attemptId: "later" };
  const retained = JSON.stringify([original, later]);
  localStorage.setItem("tempo-pending-training-reviews-v1", retained);
  await initializeStudyIntentStorage(true);
  const fetcher = vi.fn(); vi.stubGlobal("fetch", withMissingReceipts(fetcher));
  await expect(flushPendingReviews(Infinity, true, "later")).rejects.toThrow("earlier result");
  expect(fetcher).not.toHaveBeenCalled();
  expect(localStorage.getItem("tempo-pending-training-reviews-v1")).toBe(retained);
});

it("retryable failed operation receipt remains transient and retains the original review", async () => {
  await enqueuePendingReview({ backendId: "card", queueEntryId: 1, attemptId: "retryable-receipt", outcome: "again", guided: false });
  const original = pendingReviews()[0];
  vi.stubGlobal("fetch", withMissingReceipts(vi.fn().mockResolvedValue(Response.json({ state: "failed", error: {
    status_code: 409, retryable: true, message: "Worker will recover" } }))));
  await expect(flushPendingReviews(1, true)).rejects.toMatchObject({ classification: "transient", retryable: true });
  expect(pendingReviews()).toEqual([original]);
});


it("PR105 guided recovery consumes a complete review receipt despite an unavailable advisory marker", async () => {
  const review = { backendId: "guided-persisted", queueEntryId: 811, attemptId: "guided-original",
    completedAt: "2026-10-08T12:00:00Z", outcome: "correct" as const, guided: true, expectedRevision: 3 };
  await enqueuePendingReview(review);
  const fetcher = vi.fn<typeof fetch>(async (input, options) => options?.method === "POST"
    ? Response.json({ detail: "Command broker unavailable" }, { status: 503 })
    : Response.json({ state: "complete", response: { persisted: true, review_id: 88 } }));
  vi.stubGlobal("fetch", fetcher);
  await expect(flushPendingReviews(1, true)).resolves.toEqual({ persistedAttemptIds: [review.attemptId], conflictedAttemptIds: [] });
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][0]).toContain("/api/operations/review-attempt%3Aguided-original");
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toEqual([]);
  expect(pendingReviews()).toEqual([]);
});
