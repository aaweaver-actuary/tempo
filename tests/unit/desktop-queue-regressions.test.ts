import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";
import { clearDebugErrors, debugErrors } from "../../app/lib/debug-reporting";
import { localDayKey } from "../../app/utils/local";
import { OfflineReplayError } from "../../app/lib/offline-training";
import { clearNotificationHistory, notificationToastIds, notifications, publishNotification } from "../../app/lib/notifications";
import { enqueueTrainingFailure, pendingTrainingFailures } from "../../app/lib/training-failure-outbox";
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
  clearNotificationHistory();
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
  it("AS-15 only the current queue generation can settle recovery readiness", async () => {
    let firstRead!: (value: null) => void;
    let secondRead!: (value: null) => void;
    offlineTrainingMocks.readPreparedTraining.mockImplementationOnce(() => new Promise(resolve => { firstRead = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { secondRead = resolve; }));
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ count: 0, cards: [] })));
    const firstQueue = fetchAndInitializeQueue();
    expect(useTrainingStore.getState().queueReadiness).toBe("loading");
    const secondQueue = fetchAndInitializeQueue();
    firstRead(null);
    await firstQueue;
    expect(useTrainingStore.getState().queueReadiness).toBe("loading");
    secondRead(null);
    await secondQueue;
    expect(useTrainingStore.getState().queueReadiness).toBe("ready");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Queue unavailable")));
    await expect(fetchAndInitializeQueue()).rejects.toThrow("Queue unavailable");
    expect(useTrainingStore.getState().queueReadiness).toBe("unavailable");
  });
  it("guided attempt recovery clears its warning while phone conflicts remain", async () => {
    const pendingNotice = publishNotification({ severity: "warning", source: "training queue", key: "guided-attempt-save", message: "Guided attempt save pending. Tempo will retry." });
    offlineTrainingMocks.replayOfflineAttempts.mockResolvedValueOnce({
      localDate: localDayKey(), preparedAt: new Date().toISOString(), cards: [], nextTemporaryId: -1,
      attempts: [{ localEntryId: 801, cardId: "desktop-active", outcome: "correct", guided: false,
        completedAt: new Date().toISOString(), expectedReviewId: 0, expectedRevision: 1, conflict: "Computer review changed" }],
    });
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ count: 0, cards: [] })));
    await fetchAndInitializeQueue();
    expect(notifications().find((record) => record.id === pendingNotice)?.resolvedAt).not.toBeNull();
    expect(notifications().find((record) => record.key === "phone-review-conflicts")).toMatchObject({ severity: "warning", resolvedAt: null });
  });

  it("guided attempt warnings clear quietly only after pending saves are confirmed", async () => {
    const legacy = publishNotification({ severity: "warning", source: "training queue", message: "Guided attempt save pending. Tempo will retry." });
    enqueueTrainingFailure(801);
    let confirmAttempt: ((response: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/fail")) return new Promise<Response>((resolve) => { confirmAttempt = resolve; });
      return Response.json({ count: 1, cards: [{ id: "desktop-active", queue_entry_id: 801,
        start_fen: startingFen, moves: ["e2e4"], content_type: "opening", repertoire_name: "Desktop active", repertoire_source: "PGN" }] });
    }));
    await fetchAndInitializeQueue();
    expect(pendingTrainingFailures()).toEqual([801]);
    expect(notifications().find((record) => record.key === "guided-attempt-save")).toMatchObject({ severity: "warning", resolvedAt: null });
    expect(notifications().find((record) => record.id === legacy)?.resolvedAt).toBeNull();
    expect(confirmAttempt).toBeDefined();
    confirmAttempt!(Response.json({ attempt_failed: true }));
    await waitFor(() => expect(pendingTrainingFailures()).toEqual([]));
    await waitFor(() => expect(notifications().every((record) => record.resolvedAt)).toBe(true));
    expect(notificationToastIds()).toHaveLength(0);
    expect(useTrainingStore.getState().queueNotice).toBe("");
  });

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
    expect(notifications().find((record) => record.key === "phone-review-syncing")).toMatchObject({ severity: "warning", active: false, resolvedAt: null });
    await fetchAndInitializeQueue();
    expect(notifications().find((record) => record.key === "phone-review-syncing")).toMatchObject({ severity: "success", active: false });
    expect(notifications().find((record) => record.key === "phone-review-syncing")?.resolvedAt).not.toBeNull();
  });
});
