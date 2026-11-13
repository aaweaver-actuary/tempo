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

it("C24 repeats only through the authoritative parent requeue identity", async () => {
  const { vi } = await import("vitest");
  const { openingEvidenceStorage } = await import("../fixtures/opening-evidence-storage");
  const browserStorage = localStorage; const storage = await openingEvidenceStorage(); vi.stubGlobal("localStorage", browserStorage);
  const base = { cardId: "exercise-card", outcome: "correct", guided: false, completedAt: "2026-09-26T12:05:00Z", expectedReviewId: 0, expectedRevision: 1, studyId: "study", exerciseId: "exercise", answer: { type: "choice", option_ids: ["a"] } } as const;
  const parent = { ...base, localEntryId: 1, attemptId: "parent" };
  const child = { ...base, localEntryId: 2, parentLocalEntryId: 1, attemptId: "child", queueCycle: 1 };
  storage.stores.training.set("prepared-daily-queue", { localDate: "2026-09-26", preparedAt: base.completedAt, cards: [], nextTemporaryId: 100, attempts: [parent, child] });
  const bodies: Array<Record<string, unknown>> = [];
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)); bodies.push(body);
    return Response.json({ attempt_id: body.attempt_id, assessment: { outcome: "correct", feedback: "ok" }, rating: "correct", requested_rating: "correct", persisted: true,
      review: { review_id: body.attempt_id === "parent" ? 41 : 42, requeue_entry_id: body.attempt_id === "parent" ? 77 : null } });
  }));
  try {
    const offline = await import("../../app/lib/offline-training"); const result = await offline.replayOfflineAttempts();
    expect(bodies.map(body => [body.attempt_id, body.queue_entry_id, body.expected_review_id])).toEqual([["parent", 1, 0], ["child", 77, 41]]);
    expect(result?.attempts.map(attempt => attempt.serverReviewId)).toEqual([41, 42]);
    await offline.replayOfflineAttempts(); expect(bodies).toHaveLength(2);
  } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
});
