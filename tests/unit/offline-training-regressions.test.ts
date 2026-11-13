import { describe, expect, it } from "vitest";
import { describeOfflineQueue, offlineRepeatPosition } from "../../app/lib/offline-training";
import type { BackendQueueCard } from "../../app/domain/transport";

const preparedCard = {
  id: "phone-card", queue_entry_id: 1,
  start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  moves: ["e2e4"], content_type: "opening", repertoire_name: "Phone",
  repertoire_source: "PGN", scheduling_mode: "normal",
} as BackendQueueCard;

describe("prepared phone queue repeat rules", () => {
  it("repeats an Again after four cards and a first clean pass at the end", () => {
    expect(offlineRepeatPosition(preparedCard, "again")).toBe(4);
    expect(offlineRepeatPosition(preparedCard, "correct")).toBe(Number.POSITIVE_INFINITY);
  });

  it("does not create a clean repeat for an already studied, light, or hard card", () => {
    expect(offlineRepeatPosition({ ...preparedCard, first_correct_at: "2026-09-25T12:00:00+00:00" }, "correct")).toBeNull();
    expect(offlineRepeatPosition({ ...preparedCard, scheduling_mode: "light" }, "correct")).toBeNull();
    expect(offlineRepeatPosition({ ...preparedCard, scheduling_mode: "hard" }, "correct")).toBeNull();
  });
});

describe("phone queue sync status", () => {
  it("labels a saved phone queue as offline with its preparation time and pending review count", () => {
    const notice = describeOfflineQueue({
      localDate: "2026-09-26", preparedAt: "2026-09-26T12:00:00Z",
      cards: [preparedCard], nextTemporaryId: 1_000_000_000_000,
      attempts: [{ localEntryId: 1, cardId: "phone-card", outcome: "correct", guided: false,
        completedAt: "2026-09-26T12:05:00Z", expectedReviewId: 0, expectedRevision: 1 }],
    });
    expect(notice).toContain("Offline queue prepared");
    expect(notice).toContain("1 review saved on phone");
    expect(notice).toContain("live count may differ until you reconnect");
  });
});

it.each([["submit", "missing"], ["submit", "foreign"], ["assessment", "missing"], ["assessment", "foreign"]] as const)(
  "phone %s rejects %s acknowledgment and replays original attempt and answer", async (stage, fault) => {
    const { vi } = await import("vitest");
    const { openingEvidenceStorage } = await import("../fixtures/opening-evidence-storage");
    const browserStorage = localStorage;
    const storage = await openingEvidenceStorage();
    vi.stubGlobal("localStorage", browserStorage);
    const attempt = { localEntryId: 1, cardId: "phone-card", outcome: "correct", guided: false,
      completedAt: "2026-09-26T12:05:00Z", expectedReviewId: 0, expectedRevision: 1,
      studyId: "study", exerciseId: "exercise", attemptId: "original-attempt", answer: { type: "choice", option_ids: ["a"], displayed_order: ["a", "b"] }, selfRating: "correct" };
    storage.stores.training.set("prepared-daily-queue", { localDate: "2026-09-26", preparedAt: attempt.completedAt,
      cards: [preparedCard], nextTemporaryId: 1000000, attempts: [attempt] });
    let faulted = false; const requests: { url: string; key: string | null; body: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const assessment = url.includes("self-assess");
      requests.push({ url, key: new Headers(init?.headers).get("Idempotency-Key"), body: String(init?.body) });
      if (!faulted && (assessment ? "assessment" : "submit") === stage) {
        faulted = true;
        return Response.json(fault === "foreign" ? { operation_id: "foreign", state: "queued" } : { state: "queued" }, { status: 202 });
      }
      return Response.json(assessment ? { attempt_id: "original-attempt", assessment: { outcome: "correct", feedback: "ok" },
        rating: "correct", requested_rating: "correct", persisted: true, review: { review_id: 41, requeue_entry_id: null } } :
        { attempt_id: "original-attempt", assessment: { outcome: "needs_self_assessment", feedback: "compare" }, pending_self_assessment: true });
    }));
    const offline = await import("../../app/lib/offline-training");
    await expect(offline.replayOfflineAttempts()).rejects.toThrow("still pending");
    expect((storage.stores.training.get("prepared-daily-queue") as { attempts: unknown[] }).attempts).toEqual([stage === "assessment" ? { ...attempt, serverAssessment: { outcome: "needs_self_assessment", feedback: "compare" } } : attempt]);
    const result = await offline.replayOfflineAttempts();
    expect(result?.attempts[0]).toMatchObject({ attemptId: "original-attempt", answer: attempt.answer, serverAcknowledged: true, serverReviewId: 41 });
    expect(requests.filter(x => x.url.includes("/operations/"))).toHaveLength(0);
    expect(requests.filter(x => x.url.includes("self-assess")).every(x => x.key === "phone-assessment:original-attempt")).toBe(true);
    const submits = requests.filter(x => !x.url.includes("self-assess"));
    expect(submits.every(x => x.key === "phone-study:original-attempt" && x.body === submits[0].body)).toBe(true);
    vi.unstubAllGlobals(); vi.restoreAllMocks();
  });

it("C21 holds an ambiguous phone exercise and its repeat while syncing an unrelated review", async () => {
  const { vi } = await import("vitest");
  const { openingEvidenceStorage } = await import("../fixtures/opening-evidence-storage");
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal("localStorage", browserStorage);
  const base = { outcome: "correct", guided: false, completedAt: "2026-09-26T12:05:00Z", expectedReviewId: 0, expectedRevision: 1 } as const;
  const held = { ...base, localEntryId: 1, cardId: "held", studyId: "study", attemptId: "original" };
  const child = { ...base, localEntryId: 2, parentLocalEntryId: 1, cardId: "held", attemptId: "repeat" };
  const unrelated = { ...base, localEntryId: 3, cardId: "opening", attemptId: "opening-original" };
  storage.stores.training.set("prepared-daily-queue", { localDate: "2026-09-26", preparedAt: base.completedAt, cards: [], nextTemporaryId: 100, attempts: [held, child, unrelated] });
  const fetcher = vi.fn(async () => Response.json({ review_id: 41, requeue_entry_id: null })); vi.stubGlobal("fetch", fetcher);
  try {
    const result = await (await import("../../app/lib/offline-training")).replayOfflineAttempts();
    expect(result?.attempts.slice(0, 2)).toEqual([held, child]);
    expect(result?.attempts[2]).toMatchObject({ attemptId: "opening-original", serverReviewId: 41, serverAcknowledged: true });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(String(fetcher.mock.calls[0][0])).toContain("/api/cards/opening/review");
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it("C21 phone replay holds unsupported original answers and missing assessment while opening progresses", async () => {
  const { vi } = await import("vitest");
  const { openingEvidenceStorage } = await import("../fixtures/opening-evidence-storage");
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal("localStorage", browserStorage);
  const base = { outcome: "correct", guided: false, completedAt: "2026-09-26T12:05:00Z", expectedReviewId: 0, expectedRevision: 1 } as const;
  const retired = { ...base, localEntryId: 1, cardId: "retired", studyId: "study", exerciseId: "old", attemptId: "old-original", answer: { type: "defense", move: "e2e4" } };
  const unknown = { ...base, localEntryId: 2, cardId: "unknown", studyId: "study", exerciseId: "old-unknown", attemptId: "unknown-original", answer: { type: "unknown" } };
  const awaiting = { ...base, localEntryId: 3, cardId: "awaiting", studyId: "study", exerciseId: "explanation", attemptId: "answer-original", answer: { type: "explanation", text: "answer", ready: true } };
  const repeat = { ...base, localEntryId: 4, parentLocalEntryId: 3, cardId: "awaiting", attemptId: "repeat-original" };
  const opening = { ...base, localEntryId: 5, cardId: "opening", attemptId: "opening-original" };
  const originals = [retired, unknown, awaiting, repeat, opening];
  storage.stores.training.set("prepared-daily-queue", { localDate: "2026-09-26", preparedAt: base.completedAt, cards: [], nextTemporaryId: 100, attempts: originals });
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input); requests.push(url);
    if (url.includes("/explanation/attempts")) return Response.json({ attempt_id: "answer-original", assessment: { outcome: "needs_self_assessment", feedback: "compare" }, pending_self_assessment: true });
    if (url.includes("/cards/opening/review")) return Response.json({ review_id: 41, requeue_entry_id: null });
    throw new Error(`Unexpected dispatch ${url}`);
  }));
  try {
    const result = await (await import("../../app/lib/offline-training")).replayOfflineAttempts();
    expect(result?.attempts.slice(0, 4)).toEqual([retired, unknown, { ...awaiting, serverAssessment: { outcome: "needs_self_assessment", feedback: "compare" } }, repeat]);
    expect(result?.attempts[4]).toMatchObject({ attemptId: "opening-original", serverReviewId: 41, serverAcknowledged: true });
    expect(requests).toHaveLength(2); expect(requests.some(url => /old|self-assess|retired|unknown/.test(url))).toBe(false);
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it("C24 classifies provenance without inventing history", async () => {
  const { classifyOfflineGradingProvenance: classify } = await import("../../app/domain/offline-training");
  expect(classify(undefined)).toBe("legacy_unknown");
  expect(classify({ server_contract_version: 1, browser_implementation_revision: 2 })).toBe("current");
  for (const value of [null, {}, { server_contract_version: 1, browser_implementation_revision: 1 }, { server_contract_version: 2, browser_implementation_revision: 2 }, { server_contract_version: 1, browser_implementation_revision: 3 }]) expect(classify(value)).toBe("unsupported");
});

it("C24 keeps rejected and unsupported originals while later server receipts settle once", async () => {
  const { vi } = await import("vitest");
  const { openingEvidenceStorage } = await import("../fixtures/opening-evidence-storage");
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal("localStorage", browserStorage);
  const base = { outcome: "correct", guided: false, completedAt: "2026-09-26T12:05:00Z", expectedReviewId: 0, expectedRevision: 1 } as const;
  const study = { ...base, studyId: "study", exerciseId: "exercise" };
  const san = { ...study, localEntryId: 1, cardId: "san", attemptId: "san-id", answer: { type: "move_line", moves: ["Nf3"] }, assessment: { outcome: "unrecognized", feedback: "local" }, selfRating: "correct" };
  const empty = { ...study, localEntryId: 2, cardId: "empty", attemptId: "empty-id", answer: { type: "move_line", moves: [] } };
  const future = { ...study, localEntryId: 3, cardId: "future", attemptId: "future-id", answer: { type: "explanation", text: "x", ready: true }, gradingProvenance: { server_contract_version: 9, browser_implementation_revision: 2 } };
  const valid = { ...study, localEntryId: 4, cardId: "valid", attemptId: "valid-id", answer: { type: "choice", option_ids: ["a"] }, assessment: { outcome: "correct", feedback: "local" } };
  const repeat = { ...base, localEntryId: 5, parentLocalEntryId: 4, cardId: "valid", attemptId: "repeat-id" };
  const opening = { ...base, localEntryId: 6, cardId: "opening", attemptId: "opening-id" };
  storage.stores.training.set("prepared-daily-queue", { localDate: "2026-09-26", preparedAt: base.completedAt, cards: [], nextTemporaryId: 100, attempts: [san, empty, future, valid, repeat, opening] });
  const requests: { url: string; key: string | null; body: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); requests.push({ url, key: new Headers(init?.headers).get("Idempotency-Key"), body: String(init?.body) });
    if (url.includes("/cards/opening/")) return Response.json({ review_id: 43, requeue_entry_id: null });
    const body = JSON.parse(String(init?.body));
    if (body.attempt_id === "san-id") return Response.json({ detail: "Use legal UCI moves" }, { status: 422 });
    if (body.attempt_id === "valid-id") return Response.json({ attempt_id: "valid-id", assessment: { outcome: "incorrect", feedback: "server" }, rating: "again", requested_rating: "again", persisted: true, review: { review_id: 42, requeue_entry_id: null } });
    throw new Error(`unexpected ${url}`);
  }));
  try {
    const offline = await import("../../app/lib/offline-training"); const result = await offline.replayOfflineAttempts();
    expect(result?.attempts[0]).toMatchObject({ ...san, conflict: "Computer rejected the saved answer: Use legal UCI moves" });
    expect(result?.attempts[1]).toEqual(empty); expect(result?.attempts[2]).toEqual(future);
    expect(result?.attempts[3]).toMatchObject({ ...valid, serverReviewId: 42, serverAssessment: { outcome: "incorrect", feedback: "server" }, syncWarning: expect.stringContaining("differently") });
    expect(result?.attempts[4]).toEqual(repeat); expect(result?.attempts[5]).toMatchObject({ serverReviewId: 43 });
    expect(requests).toHaveLength(3); expect(requests[0].key).toBe("phone-study:san-id");
    expect(JSON.parse(requests[0].body).answer).toEqual(san.answer); expect(requests[0].body).not.toContain("gradingProvenance");
    await offline.replayOfflineAttempts(); expect(requests).toHaveLength(3);
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it("C24 records provenance with a newly committed frozen answer", async () => {
  const { vi } = await import("vitest");
  const { openingEvidenceStorage } = await import("../fixtures/opening-evidence-storage");
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal("localStorage", browserStorage);
  const { learningDayKey } = await import("../../app/lib/study-connection");
  const card = { ...preparedCard, content_type: "study_exercise", study_exercise_id: "exercise", revision: 1, cycle: 0,
    study_snapshot: { schema_version: 1, grader_version: 1, exercise_id: "exercise", revision: 1,
      fen: "4k3/8/8/8/8/8/8/4K1N1 w - - 0 1", specification: { type: "choice", prompt: "Choose", options: [{ id: "a", text: "A" }, { id: "b", text: "B" }], correct_option_ids: ["a"] } } };
  storage.stores.training.set("prepared-daily-queue", { localDate: learningDayKey(), preparedAt: new Date().toISOString(), cards: [card], nextTemporaryId: 100, attempts: [] });
  try {
    const offline = await import("../../app/lib/offline-training");
    const answer = { type: "choice" as const, option_ids: ["a"], displayed_order: ["a", "b"] };
    const result = await offline.recordOfflineStudyAttempt(1, "study", answer);
    expect(result.prepared.attempts[0]).toMatchObject({ answer, gradingProvenance: { server_contract_version: 1, browser_implementation_revision: 2 } });
    expect((await offline.readPreparedTraining())?.attempts[0]).toEqual(result.prepared.attempts[0]);
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it("C24 holds a Study repeat when the receipt does not prove its cycle", async () => {
  const { vi } = await import("vitest");
  const { openingEvidenceStorage } = await import("../fixtures/opening-evidence-storage");
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal("localStorage", browserStorage);
  const base = { cardId: "exercise-card", outcome: "correct", guided: false, completedAt: "2026-09-26T12:05:00Z", expectedReviewId: 0, expectedRevision: 1, studyId: "study", exerciseId: "exercise", answer: { type: "choice", option_ids: ["a"] } } as const;
  const parent = { ...base, localEntryId: 1, attemptId: "parent" };
  const child = { ...base, localEntryId: 2, parentLocalEntryId: 1, attemptId: "child", queueCycle: 1 };
  storage.stores.training.set("prepared-daily-queue", { localDate: "2026-09-26", preparedAt: base.completedAt, cards: [{ ...preparedCard, id: "exercise-card", queue_entry_id: 77, cycle: 3 }], nextTemporaryId: 100, attempts: [parent, child] });
  const bodies: Array<Record<string, unknown>> = [];
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)); bodies.push(body);
    return Response.json({ attempt_id: body.attempt_id, assessment: { outcome: "correct", feedback: "ok" }, rating: "correct", requested_rating: "correct", persisted: true,
      review: { review_id: body.attempt_id === "parent" ? 41 : 42, requeue_entry_id: body.attempt_id === "parent" ? 77 : null } });
  }));
  try {
    const offline = await import("../../app/lib/offline-training"); const result = await offline.replayOfflineAttempts();
    expect(bodies.map(body => [body.attempt_id, body.queue_entry_id, body.expected_review_id])).toEqual([["parent", 1, 0]]);
    expect(result?.attempts[0].serverReviewId).toBe(41); expect(result?.attempts[1]).toEqual(child);
    await offline.replayOfflineAttempts(); expect(bodies).toHaveLength(1);
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it.each(['terminal', 'terminal_false', 'foreign', 'missing', 'pending', 'retryable'] as const)("C24 preserves identity and scopes receipt case %s", async (scenario) => {
  const { vi } = await import('vitest'); const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal('localStorage', browserStorage);
  const base = { outcome: 'correct', guided: false, completedAt: '2026-09-26T12:05:00Z', expectedReviewId: 0, expectedRevision: 1 } as const;
  const original = { ...base, localEntryId: 1, cardId: 'study-card', studyId: 'study', exerciseId: 'exercise', attemptId: 'original', answer: { type: 'move_line', moves: ['Nf3'] }, assessment: { outcome: 'unrecognized', feedback: 'old' }, selfRating: 'correct' };
  const other = { ...base, localEntryId: 2, cardId: 'opening', attemptId: 'opening-original' };
  storage.stores.training.set('prepared-daily-queue', { localDate: '2026-09-26', preparedAt: base.completedAt, cards: [], nextTemporaryId: 100, attempts: [original, other] });
  const calls: { url: string; key: string | null; body: string }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); calls.push({ url, key: new Headers(init?.headers).get('Idempotency-Key'), body: String(init?.body) });
    if (url.includes('/cards/opening/')) return Response.json({ review_id: 41, requeue_entry_id: null });
    if (!url.includes('/api/operations/')) return Response.json({ operation_id: 'phone-study:original', state: 'queued' }, { status: 202 });
    return Response.json({ ...(scenario === 'missing' ? {} : { operation_id: scenario === 'foreign' ? 'foreign' : 'phone-study:original' }),
      state: scenario === 'pending' ? 'queued' : 'failed', error: { status_code: 422, message: 'invalid answer', detail: 'Use legal UCI moves', ...(scenario === 'retryable' ? { retryable: true } : scenario === 'terminal_false' ? { retryable: false } : {}) } });
  }));
  try {
    const offline = await import('../../app/lib/offline-training');
    if (scenario === 'terminal' || scenario === 'terminal_false') {
      const result = await offline.replayOfflineAttempts();
      expect(result?.attempts[0]).toEqual({ ...original, conflict: 'Computer rejected the saved answer: Use legal UCI moves', serverRejection: { operationId: 'phone-study:original', status: 422, message: 'invalid answer', detail: 'Use legal UCI moves', ...(scenario === 'terminal_false' ? { retryable: false } : {}) } });
      expect(result?.attempts[1]).toMatchObject({ serverReviewId: 41 });
    } else {
      await expect(offline.replayOfflineAttempts()).rejects.toThrow();
      expect((storage.stores.training.get('prepared-daily-queue') as { attempts: unknown[] }).attempts).toEqual([original, other]);
      expect(calls.some(call => call.url.includes('/cards/opening/'))).toBe(false);
    }
    expect(calls[0].key).toBe('phone-study:original'); expect(JSON.parse(calls[0].body).answer).toEqual(original.answer);
    expect(calls[0].body).not.toContain('gradingProvenance');
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it.each(['missing', 'foreign', 'own'] as const)('C24 self-assessment receipt %s cannot settle another identity', async (receipt) => {
  const { vi } = await import('vitest'); const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal('localStorage', browserStorage);
  const attempt = { localEntryId: 1, cardId: 'study-card', outcome: 'correct', guided: false, completedAt: '2026-09-26T12:05:00Z', expectedReviewId: 0, expectedRevision: 1, studyId: 'study', exerciseId: 'exercise', attemptId: 'original', answer: { type: 'explanation', text: 'answer', ready: true }, selfRating: 'correct' };
  storage.stores.training.set('prepared-daily-queue', { localDate: '2026-09-26', preparedAt: attempt.completedAt, cards: [], nextTemporaryId: 100, attempts: [attempt] });
  const calls: { url: string; key: string | null; body: string }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); calls.push({ url, key: new Headers(init?.headers).get('Idempotency-Key'), body: String(init?.body) });
    if (url.includes('/api/operations/')) return Response.json({ ...(receipt === 'missing' ? {} : { operation_id: receipt === 'foreign' ? 'foreign' : 'phone-assessment:original' }), state: 'complete', response: { attempt_id: 'original', assessment: { outcome: 'needs_self_assessment', feedback: 'compare' }, rating: 'correct', requested_rating: 'correct', persisted: true, review: { review_id: 42, requeue_entry_id: null } } });
    if (url.includes('/self-assess')) return Response.json({ operation_id: 'phone-assessment:original', state: 'queued' }, { status: 202 });
    return Response.json({ attempt_id: 'original', assessment: { outcome: 'needs_self_assessment', feedback: 'compare' }, pending_self_assessment: true });
  }));
  try {
    const offline = await import('../../app/lib/offline-training');
    if (receipt === 'own') {
      const result = await offline.replayOfflineAttempts(); expect(result?.attempts[0]).toMatchObject({ ...attempt, serverReviewId: 42 });
    } else {
      await expect(offline.replayOfflineAttempts()).rejects.toThrow();
      expect((await offline.readPreparedTraining())?.attempts).toEqual([{ ...attempt, serverAssessment: { outcome: 'needs_self_assessment', feedback: 'compare' } }]);
    }
    const child = calls.find(call => call.url.includes('/self-assess'))!;
    expect(child.key).toBe('phone-assessment:original'); expect(JSON.parse(child.body)).toEqual({ rating: 'correct' });
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it('C24 recovers an unavailable repeat with the actual nonconsecutive cycle and stable child request', async () => {
  const { vi } = await import('vitest'); const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal('localStorage', browserStorage);
  const base = { cardId: 'card', outcome: 'correct', guided: false, completedAt: '2026-09-26T12:05:00Z', expectedReviewId: 0, expectedRevision: 1, studyId: 'study', exerciseId: 'exercise', answer: { type: 'choice', option_ids: ['a'] } } as const;
  const parent = { ...base, localEntryId: 1, attemptId: 'parent', serverReviewId: 41, serverAcknowledged: true, serverRepeatEntryId: 77 };
  const child = { ...base, localEntryId: 2, parentLocalEntryId: 1, attemptId: 'child', queueCycle: 1 };
  const other = { localEntryId: 3, cardId: 'opening', outcome: 'correct', guided: false, completedAt: base.completedAt, expectedReviewId: 0, expectedRevision: 1, attemptId: 'opening-original' };
  storage.stores.training.set('prepared-daily-queue', { localDate: '2026-09-26', preparedAt: base.completedAt, cards: [], nextTemporaryId: 100, attempts: [parent, child, other] });
  let available = false, lost = true; const sends: string[] = []; let reads = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/requeue')) { reads++; return available ? Response.json({ parent_attempt_id: 'parent', review_id: 41, queue_entry_id: 77, card_id: 'card', cycle: 4, revision: 1 }) : new Response(null, { status: 404 }); }
    if (url.includes('/cards/opening/')) return Response.json({ review_id: 43, requeue_entry_id: null });
    sends.push(String(init?.body)); if (lost) { lost = false; throw new Error('lost acknowledgment'); }
    return Response.json({ attempt_id: 'child', assessment: { outcome: 'correct', feedback: 'ok' }, rating: 'correct', requested_rating: 'correct', persisted: true, review: { review_id: 42, requeue_entry_id: null } });
  }));
  try {
    const offline = await import('../../app/lib/offline-training'); const first = await offline.replayOfflineAttempts();
    expect(first?.attempts[1]).toEqual(child); expect(first?.attempts[2]).toMatchObject({ serverReviewId: 43 });
    available = true; await expect(offline.replayOfflineAttempts()).rejects.toThrow('lost acknowledgment');
    const retained = (await offline.readPreparedTraining())!.attempts[1];
    expect(retained).toMatchObject({ ...child, repeatBinding: { parentAttemptId: 'parent', reviewId: 41, entryId: 77, cycle: 4, cardId: 'card', revision: 1 } });
    const done = await offline.replayOfflineAttempts(); expect(done?.attempts[1]).toMatchObject({ serverReviewId: 42 });
    expect(reads).toBe(2); expect(sends).toHaveLength(2); expect(sends[0]).toBe(sends[1]);
    expect(JSON.parse(sends[0])).toMatchObject({ attempt_id: 'child', queue_entry_id: 77, queue_cycle: 4, expected_review_id: 41, answer: child.answer });
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it.each(['parent', 'review', 'entry', 'card', 'revision', 'cycle'] as const)('C24 refuses a repeat owner response with foreign %s', async (fault) => {
  const { vi } = await import('vitest'); const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal('localStorage', browserStorage);
  const base = { cardId: 'card', outcome: 'correct', guided: false, completedAt: '2026-09-26T12:05:00Z', expectedReviewId: 0, expectedRevision: 1, studyId: 'study', exerciseId: 'exercise', answer: { type: 'choice', option_ids: ['a'] } } as const;
  const parent = { ...base, localEntryId: 1, attemptId: 'parent', serverReviewId: 41, serverAcknowledged: true, serverRepeatEntryId: 77 };
  const child = { ...base, localEntryId: 2, parentLocalEntryId: 1, attemptId: 'child', queueCycle: 1 };
  const other = { localEntryId: 3, cardId: 'opening', outcome: 'correct', guided: false, completedAt: base.completedAt, expectedReviewId: 0, expectedRevision: 1, attemptId: 'opening-original' };
  storage.stores.training.set('prepared-daily-queue', { localDate: '2026-09-26', preparedAt: base.completedAt, cards: [], nextTemporaryId: 100, attempts: [parent, child, other] });
  const owner: Record<string, unknown> = { parent_attempt_id: 'parent', review_id: 41, queue_entry_id: 77, card_id: 'card', cycle: 4, revision: 1 };
  const changes = { parent: ['parent_attempt_id', 'foreign'], review: ['review_id', 42], entry: ['queue_entry_id', 78], card: ['card_id', 'foreign'], revision: ['revision', 2], cycle: ['cycle', -1] } as const;
  const [key, value] = changes[fault]; owner[key] = value; const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => { const url = String(input); calls.push(url);
    if (url.includes('/requeue')) return Response.json(owner);
    if (url.includes('/cards/opening/')) return Response.json({ review_id: 43, requeue_entry_id: null });
    throw new Error(`unexpected ${url}`);
  }));
  try { const result = await (await import('../../app/lib/offline-training')).replayOfflineAttempts();
    expect(result?.attempts[1]).toEqual(child); expect(result?.attempts[2]).toMatchObject({ serverReviewId: 43 });
    expect(calls).toHaveLength(2); expect(calls.some(url => url.endsWith('/attempts'))).toBe(false);
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it('C24 holds the original repeat across an authority change during owner read', async () => {
  const { vi } = await import('vitest'); const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal('localStorage', browserStorage);
  const base = { cardId: 'card', outcome: 'correct', guided: false, completedAt: '2026-09-26T12:05:00Z', expectedReviewId: 0, expectedRevision: 1, studyId: 'study', exerciseId: 'exercise', answer: { type: 'choice', option_ids: ['a'] } } as const;
  const parent = { ...base, localEntryId: 1, attemptId: 'parent', serverReviewId: 41, serverAcknowledged: true, serverRepeatEntryId: 77 };
  const child = { ...base, localEntryId: 2, parentLocalEntryId: 1, attemptId: 'child', queueCycle: 1 };
  storage.stores.training.set('prepared-daily-queue', { localDate: '2026-09-26', preparedAt: base.completedAt, cards: [], nextTemporaryId: 100, attempts: [parent, child] });
  let resolve!: (value: Response) => void; let started!: () => void;
  const waiting = new Promise<void>(done => { started = done; });
  const fetcher = vi.fn(() => { started(); return new Promise<Response>(done => { resolve = done; }); }); vi.stubGlobal('fetch', fetcher);
  try {
    const offline = await import('../../app/lib/offline-training'); const connection = await import('../../app/lib/study-connection');
    const pending = offline.replayOfflineAttempts(); await waiting; connection.defaultStudyClient.disconnectStudy();
    resolve(Response.json({ parent_attempt_id: 'parent', review_id: 41, queue_entry_id: 77, card_id: 'card', cycle: 4, revision: 1 }));
    await expect(pending).rejects.toThrow('authority changed');
    expect((await offline.readPreparedTraining())?.attempts).toEqual([parent, child]); expect(fetcher).toHaveBeenCalledOnce();
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it.each([200, 202] as const)('C24 refuses delayed child %i after authority changes', async (status) => {
  const { vi } = await import('vitest'); const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal('localStorage', browserStorage);
  const connection = await import('../../app/lib/study-connection'); const authority = connection.defaultStudyClient.studyWorkspace();
  const base = { cardId: 'card', outcome: 'correct', guided: false, completedAt: '2026-09-26T12:05:00Z', expectedReviewId: 0, expectedRevision: 1, studyId: 'study', exerciseId: 'exercise', answer: { type: 'choice', option_ids: ['a'] } } as const;
  const parent = { ...base, localEntryId: 1, attemptId: 'parent', serverReviewId: 41, serverAcknowledged: true, serverRepeatEntryId: 77 };
  const child = { ...base, localEntryId: 2, parentLocalEntryId: 1, attemptId: 'child', queueCycle: 1,
    repeatBinding: { authority, parentAttemptId: 'parent', reviewId: 41, entryId: 77, cycle: 4, revision: 1, cardId: 'card' } };
  storage.stores.training.set('prepared-daily-queue', { localDate: '2026-09-26', preparedAt: base.completedAt, cards: [], nextTemporaryId: 100, attempts: [parent, child] });
  let resolve!: (value: Response) => void; let started!: () => void;
  const waiting = new Promise<void>(done => { started = done; }); const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => { calls.push(String(input)); started(); return new Promise<Response>(done => { resolve = done; }); }));
  try {
    const offline = await import('../../app/lib/offline-training'); const pending = offline.replayOfflineAttempts(); await waiting;
    connection.defaultStudyClient.disconnectStudy();
    resolve(status === 202 ? Response.json({ operation_id: 'phone-study:child', state: 'queued' }, { status: 202 }) :
      Response.json({ attempt_id: 'child', assessment: { outcome: 'correct', feedback: 'ok' }, rating: 'correct', requested_rating: 'correct', persisted: true, review: { review_id: 42, requeue_entry_id: null } }));
    await expect(pending).rejects.toThrow('authority changed');
    expect((await offline.readPreparedTraining())?.attempts).toEqual([parent, child]);
    expect(calls).toHaveLength(1); expect(calls.some(url => url.includes('/api/operations/'))).toBe(false);
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it('C24 does not send self-assessment after authority changes before journal completion', async () => {
  const { vi } = await import('vitest'); const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal('localStorage', browserStorage);
  const connection = await import('../../app/lib/study-connection'); const authority = connection.defaultStudyClient.studyWorkspace();
  const base = { cardId: 'card', outcome: 'correct', guided: false, completedAt: '2026-09-26T12:05:00Z', expectedReviewId: 0, expectedRevision: 1, studyId: 'study', exerciseId: 'exercise', answer: { type: 'explanation', text: 'answer', ready: true }, selfRating: 'correct' } as const;
  const parent = { ...base, localEntryId: 1, attemptId: 'parent', serverReviewId: 41, serverAcknowledged: true, serverRepeatEntryId: 77 };
  const child = { ...base, localEntryId: 2, parentLocalEntryId: 1, attemptId: 'child', queueCycle: 1,
    repeatBinding: { authority, parentAttemptId: 'parent', reviewId: 41, entryId: 77, cycle: 4, revision: 1, cardId: 'card' } };
  storage.stores.training.set('prepared-daily-queue', { localDate: '2026-09-26', preparedAt: base.completedAt, cards: [], nextTemporaryId: 100, attempts: [parent, child] });
  const calls: string[] = []; vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    calls.push(String(input)); return Response.json({ attempt_id: 'child', assessment: { outcome: 'needs_self_assessment', feedback: 'compare' }, pending_self_assessment: true });
  }));
  storage.controls.beforeCommit = () => { connection.defaultStudyClient.disconnectStudy(); return undefined; };
  try {
    const offline = await import('../../app/lib/offline-training'); await expect(offline.replayOfflineAttempts()).rejects.toThrow('authority changed');
    const retained = (await offline.readPreparedTraining())?.attempts;
    expect(retained).toEqual([parent, { ...child, serverAssessment: { outcome: 'needs_self_assessment', feedback: 'compare' } }]);
    expect(calls).toHaveLength(1); expect(calls.some(url => url.includes('/self-assess') || url.includes('/api/operations/'))).toBe(false);
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});

it.each([200, 409, 422] as const)('C24 stops later replay after authority switches during %i journal commit', async (status) => {
  const { vi } = await import('vitest'); const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal('localStorage', browserStorage);
  const connection = await import('../../app/lib/study-connection'); const authority = connection.defaultStudyClient.studyWorkspace();
  const base = { cardId: 'card', outcome: 'correct', guided: false, completedAt: '2026-09-26T12:05:00Z', expectedReviewId: 0, expectedRevision: 1, studyId: 'study', exerciseId: 'exercise', answer: { type: 'choice', option_ids: ['a'] } } as const;
  const parent = { ...base, localEntryId: 1, attemptId: 'parent', serverReviewId: 41, serverAcknowledged: true, serverRepeatEntryId: 77 };
  const child = { ...base, localEntryId: 2, parentLocalEntryId: 1, attemptId: 'child', queueCycle: 1,
    repeatBinding: { authority, parentAttemptId: 'parent', reviewId: 41, entryId: 77, cycle: 4, revision: 1, cardId: 'card' } };
  const other = { localEntryId: 3, cardId: 'opening', outcome: 'correct', guided: false, completedAt: base.completedAt, expectedReviewId: 0, expectedRevision: 1, attemptId: 'opening-original' };
  storage.stores.training.set('prepared-daily-queue', { localDate: '2026-09-26', preparedAt: base.completedAt, cards: [], nextTemporaryId: 100, attempts: [parent, child, other] });
  const calls: string[] = []; vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    calls.push(String(input)); return status === 200 ? Response.json({ attempt_id: 'child', assessment: { outcome: 'correct', feedback: 'ok' }, rating: 'correct', requested_rating: 'correct', persisted: true, review: { review_id: 42, requeue_entry_id: null } }) : Response.json({ detail: 'rejected' }, { status });
  }));
  storage.controls.beforeCommit = () => { connection.defaultStudyClient.disconnectStudy(); return undefined; };
  try {
    const offline = await import('../../app/lib/offline-training'); await expect(offline.replayOfflineAttempts()).rejects.toThrow('authority changed');
    const retained = (await offline.readPreparedTraining())!.attempts;
    expect(retained[0]).toEqual(parent); expect(retained[2]).toEqual(other);
    if (status === 200) expect(retained[1]).toMatchObject({ ...child, serverReviewId: 42, serverAcknowledged: true });
    else expect(retained[1]).toMatchObject({ ...child, conflict: expect.stringContaining('rejected') });
    expect(calls).toHaveLength(1); expect(calls.some(url => url.includes('/cards/opening/'))).toBe(false);
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});
