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
