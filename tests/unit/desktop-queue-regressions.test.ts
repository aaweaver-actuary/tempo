import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";
import { clearDebugErrors, debugErrors } from "../../app/lib/debug-reporting";
import { localDayKey } from "../../app/utils/local";
import { OfflineReplayError } from "../../app/lib/offline-training";
import { asCardId, asFenString, asQueueEntryId, asSanMove, type PracticeCard } from "../../app/types";

const offlineTrainingMocks = vi.hoisted(() => ({
  readPreparedTraining: vi.fn(),
  replayOfflineAttempts: vi.fn(),
}));
vi.mock("../../app/lib/offline-training", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../app/lib/offline-training")>(),
  readPreparedTraining: offlineTrainingMocks.readPreparedTraining,
  replayOfflineAttempts: offlineTrainingMocks.replayOfflineAttempts,
}));

const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const activeCard: PracticeCard = {
  id: asCardId("desktop-active"), backendId: asCardId("desktop-active"),
  queueEntryId: asQueueEntryId(801), kind: "opening", title: "Desktop active", subtitle: "",
  orientation: "white", revision: 1, startingFen: asFenString(startingFen),
  moves: [asSanMove("e4"), asSanMove("e5"), asSanMove("Nf3")], userMoveTarget: 2,
};

beforeEach(() => {
  localStorage.clear();
  clearDebugErrors();
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  offlineTrainingMocks.readPreparedTraining.mockResolvedValue({
    localDate: localDayKey(), preparedAt: new Date().toISOString(), cards: [{
      id: "phone-card", queue_entry_id: 802, start_fen: startingFen, moves: ["d2d4"],
      content_type: "opening", repertoire_name: "Phone only", repertoire_source: "PGN",
    }], attempts: [], nextTemporaryId: -1,
  });
  offlineTrainingMocks.replayOfflineAttempts.mockResolvedValue(null);
  vi.stubGlobal("indexedDB", {});
});

describe("desktop live queue isolation", () => {
  it("desktop same-day prepared queue cannot replace a failed live request", async () => {
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([activeCard], true, 1);
    store.setStep(1);
    const activeFen = useTrainingStore.getState().currentFenString;
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Load failed")));

    await expect(fetchAndInitializeQueue()).rejects.toThrow("Load failed");

    expect(useTrainingStore.getState().isOfflineQueueActive).toBe(false);
    expect(useTrainingStore.getState().currentFenString).toBe(activeFen);
    expect(useTrainingStore.getState().getCard().id).toBe(activeCard.id);
    expect(useTrainingStore.getState().serviceError).toContain("Load failed");
    expect(debugErrors().at(-1)?.context).toMatchObject({
      source: "training-queue", operation: "load today queue", endpointPath: "/api/queue/window",
    });
  });

  it("offline review replay failure is attributed to replay while the live queue opens", async () => {
    offlineTrainingMocks.readPreparedTraining.mockResolvedValueOnce({
      localDate: localDayKey(), preparedAt: new Date().toISOString(), cards: [],
      attempts: [{ localEntryId: 801, cardId: "desktop-active", outcome: "correct",
        guided: false, completedAt: new Date().toISOString(), expectedReviewId: 0, expectedRevision: 1 }],
      nextTemporaryId: -1,
    });
    offlineTrainingMocks.replayOfflineAttempts.mockRejectedValueOnce(new OfflineReplayError(
      "Saved review replay failed", "/api/cards/desktop-active/review"));
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ count: 2, cards: [{
      id: "desktop-active", queue_entry_id: 801, start_fen: startingFen,
      moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening",
      repertoire_name: "Desktop active", repertoire_source: "PGN",
    }, { id: "other-live", queue_entry_id: 803, start_fen: startingFen,
      moves: ["d2d4"], content_type: "opening", repertoire_name: "Other live",
      repertoire_source: "PGN" }] })));

    await fetchAndInitializeQueue();

    expect(useTrainingStore.getState().serviceError).toBe("");
    expect(useTrainingStore.getState().isOfflineQueueActive).toBe(false);
    expect(useTrainingStore.getState().pendingReviewError).toContain("Saved review replay failed");
    expect(useTrainingStore.getState().practiceCards.map((card) => card.backendId)).toEqual(["other-live"]);
    expect(debugErrors().at(-1)?.context).toMatchObject({
      source: "training-offline-review-replay", operation: "replay saved offline reviews",
    });
    expect(debugErrors().at(-1)?.context.endpointPath).toBe("/api/cards/desktop-active/review");
  });
});
