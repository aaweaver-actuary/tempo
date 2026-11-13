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
      studyId: "study", exerciseId: "exercise", attemptId: "original-attempt", answer: { kind: "choice", choice_id: "a" }, selfRating: "correct" };
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
      return Response.json(assessment ? { review: { review_id: 41, requeue_entry_id: null } } :
        { pending_self_assessment: true, review_id: null, requeue_entry_id: null });
    }));
    const offline = await import("../../app/lib/offline-training");
    await expect(offline.replayOfflineAttempts()).rejects.toThrow("still pending");
    expect((storage.stores.training.get("prepared-daily-queue") as { attempts: unknown[] }).attempts).toEqual([attempt]);
    const result = await offline.replayOfflineAttempts();
    expect(result?.attempts[0]).toMatchObject({ attemptId: "original-attempt", answer: attempt.answer, serverAcknowledged: true, serverReviewId: 41 });
    expect(requests.filter(x => x.url.includes("/operations/"))).toHaveLength(0);
    expect(requests.filter(x => x.url.includes("self-assess")).every(x => x.key === "phone-assessment:original-attempt")).toBe(true);
    const submits = requests.filter(x => !x.url.includes("self-assess"));
    expect(submits.every(x => x.key === "phone-study:original-attempt" && x.body === submits[0].body)).toBe(true);
    vi.unstubAllGlobals(); vi.restoreAllMocks();
  });
