import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { fetchAndInitializeQueue, loadEligibleOfflineQueue } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";
import { clearDebugErrors, debugErrors } from "../../app/lib/debug-reporting";
import { enqueuePendingReview, pendingReviews } from "../../app/lib/review-outbox";
import { localDayKey } from "../../app/utils/local";
import { OfflineReplayError, type PreparedTraining } from "../../app/lib/offline-training";
import { clearNotificationHistory, notificationToastIds, notifications, publishNotification } from "../../app/lib/notifications";
import { enqueueTrainingFailure, pendingTrainingFailures, flushTrainingFailures } from "../../app/lib/training-failure-outbox";
import { asCardId, asFenString, asQueueEntryId, asSanMove, type PracticeCard } from "../../app/types";

const offlineTrainingMocks = vi.hoisted(() => ({
  readPreparedTraining: vi.fn(),
  replayOfflineAttempts: vi.fn(),
}));
vi.mock("../../app/lib/offline-shell", () => ({ waitForOfflineShell: vi.fn().mockResolvedValue(undefined) }));
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
  it("queue preparation polls yield admission without spending the service failure budget", async () => {
    vi.useFakeTimers();
    try {
      const request = vi.fn().mockResolvedValueOnce(Response.json({ count: 0, cards: [], projection: { state: "refreshing" } }));
      for (let refusal = 0; refusal < 4; refusal += 1)
        request.mockResolvedValueOnce(Response.json({ detail: "Waiting for foreground activity" }, { status: 503 }));
      request.mockResolvedValueOnce(Response.json({ count: 1, cards: [{
        id: "prepared-live", queue_entry_id: 807, start_fen: startingFen, moves: ["e2e4"],
        content_type: "opening", repertoire_name: "Prepared", repertoire_source: "PGN",
      }], projection: { state: "ready", generation: 1, updated_at: new Date().toISOString(),
        refresh_pending: 0, last_error: null } }));
      vi.stubGlobal("fetch", request);
      const loading = fetchAndInitializeQueue();
      const settled = loading.then(() => null, error => error);
      await vi.advanceTimersByTimeAsync(5_000);
      expect(await settled).toBeNull();
      expect(request).toHaveBeenCalledTimes(6);
      expect(request.mock.calls[0][1]?.headers).toBeUndefined();
      for (const call of request.mock.calls.slice(1))
        expect(call[1]?.headers).toEqual({ "X-Tempo-Work-Class": "background" });
      expect(useTrainingStore.getState().queueReadiness).toBe("ready");
      expect(useTrainingStore.getState().serviceError).toBe("");
    } finally { vi.useRealTimers(); }
  });
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
  it.each([0, 1])("canonical route provenance %s keeps the live Black training card playable", async (canonicalRouteSource) => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ count: 1, cards: [{
      id: "black-live", queue_entry_id: 804, start_fen: startingFen,
      moves: ["e2e4", "e7e5"], content_type: "opening", repertoire_name: "Black repertoire",
      repertoire_source: "PGN", trained_color: "black", revision: 3,
      canonical_route_source: canonicalRouteSource,
    }] })));

    await fetchAndInitializeQueue();

    const state = useTrainingStore.getState();
    expect(state.practiceCards).toHaveLength(1);
    expect(state.getCard()).toMatchObject({ backendId: "black-live", orientation: "black", revision: 3 });
    expect(state.currentFenString.split(" ")[1]).toBe("b");
    expect(state.attempt.phase).toBe("playerTurn");
    expect(state.serviceError).toBe("");
    expect(debugErrors()).toHaveLength(0);
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
    enqueueTrainingFailure(801, "desktop-active", 1);
    let confirmAttempt: ((response: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/fail")) return new Promise<Response>((resolve) => { confirmAttempt = resolve; });
      return Response.json({ count: 1, cards: [{ id: "desktop-active", revision: 1, queue_entry_id: 801,
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


it("pending old-card review cannot hide replacement content on the same queue ID", async () => {
  enqueuePendingReview({ backendId: "old-card", queueEntryId: 801, outcome: "correct", guided: false });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => String(url).endsWith("/review")
    ? Response.json({ detail: "busy" }, { status: 503 })
    : Response.json({ count: 1, cards: [{ id: "replacement-card", queue_entry_id: 801,
      start_fen: startingFen, moves: ["d2d4"], content_type: "opening", repertoire_name: "Replacement", repertoire_source: "PGN" }] })));
  await fetchAndInitializeQueue();
  expect(useTrainingStore.getState().getCard().backendId).toBe("replacement-card");
  expect(useTrainingStore.getState().cardsLeft).toBe(1);
});

it("old guided marker cannot label replacement content sharing its queue ID", async () => {
  enqueueTrainingFailure(801, "old-card", 1);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => String(url).endsWith("/fail")
    ? Response.json({ detail: "busy" }, { status: 503 })
    : Response.json({ count: 1, cards: [{ id: "replacement-card", revision: 1, queue_entry_id: 801,
      start_fen: startingFen, moves: ["d2d4"], content_type: "opening", repertoire_name: "Replacement", repertoire_source: "PGN" }] })));
  await fetchAndInitializeQueue();
  expect(useTrainingStore.getState().getCard().backendId).toBe("replacement-card");
  expect(useTrainingStore.getState().isAttemptFailed).toBe(false);
});


afterEach(() => vi.unstubAllGlobals());

it("iphone fallback applies conflict card identity pending pair identity and unacknowledged phone exclusions", async () => {
  vi.stubGlobal("navigator", { userAgent: "iPhone", standalone: true });
  const rawCard = (id: string, queueEntryId: number) => ({ id, queue_entry_id: queueEntryId,
    revision: 2, start_fen: startingFen, moves: ["d2d4"], content_type: "opening",
    repertoire_name: id, repertoire_source: "PGN" });
  const conflict = { backendId: "conflicted-A", queueEntryId: 801, outcome: "correct", guided: false,
    attemptId: "original-A", completedAt: "2026-10-03T12:00:00Z", expectedRevision: 1,
    state: "conflicted", reconciliationSequence: 1,
    conflict: { code: "card_revision_changed", message: "Content changed", retryable: false } };
  localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([conflict]));
  enqueuePendingReview({ backendId: "pending-E", queueEntryId: 805, outcome: "correct", guided: false });
  const prepared = { localDate: localDayKey(), preparedAt: new Date().toISOString(), nextTemporaryId: -1,
    cards: [rawCard("conflicted-A", 901), rawCard("independent-B", 802), rawCard("unsynced-C", 803),
      { ...rawCard("connected-D", 804), content_type: "defense" }, rawCard("pending-E", 805),
      rawCard("replacement-F", 805), rawCard("pending-E", 905), rawCard("acknowledged-G", 806)],
    attempts: [{ localEntryId: 703, cardId: "unsynced-C", outcome: "correct", guided: false,
      completedAt: "2026-10-03T12:00:00Z", expectedReviewId: 0, expectedRevision: 1, conflict: "Review changed" },
      { localEntryId: 706, cardId: "acknowledged-G", outcome: "correct", guided: false,
        completedAt: "2026-10-03T12:00:00Z", expectedReviewId: 0, expectedRevision: 1, serverAcknowledged: true }],
  };
  offlineTrainingMocks.readPreparedTraining.mockResolvedValue(prepared);
  offlineTrainingMocks.replayOfflineAttempts.mockRejectedValue(new TypeError("Offline"));
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Offline")));
  await fetchAndInitializeQueue();
  expect(useTrainingStore.getState().isOfflineQueueActive).toBe(true);
  expect(useTrainingStore.getState().practiceCards.map((card) => card.backendId)).toEqual([
    "independent-B", "replacement-F", "pending-E", "acknowledged-G",
  ]);
  expect(useTrainingStore.getState().cardsLeft).toBe(4);
  expect(JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1")!).find(
    (review: { backendId: string }) => review.backendId === "conflicted-A")).toEqual(conflict);
  expect(prepared.attempts).toHaveLength(2);
});


it("offline eligibility preserves only linked unchanged reinforcement while retaining conflicts", async () => {
  const rawCard = { id: "local-repeat", queue_entry_id: 1000, parent_local_entry_id: 800, cycle: 1,
    revision: 1, start_fen: startingFen, moves: ["e2e4"], content_type: "opening" as const,
    repertoire_name: "Local repeat", repertoire_source: "PGN" };
  const parent = { localEntryId: 800, cardId: "local-repeat", expectedRevision: 1, queueCycle: 0,
    expectedReviewId: 0, completedAt: "2026-10-03T12:00:00Z", outcome: "correct" as const, guided: false };
  const prepared = { localDate: localDayKey(), preparedAt: new Date().toISOString(), nextTemporaryId: 1001,
    cards: [rawCard], attempts: [parent] };
  expect((await loadEligibleOfflineQueue(prepared)).map((card) => card.queueEntryId)).toEqual([1000]);
  expect(await loadEligibleOfflineQueue({ ...prepared, cards: [{ ...rawCard, revision: 2 }] })).toEqual([]);
  expect(await loadEligibleOfflineQueue({ ...prepared, cards: [{ ...rawCard, parent_local_entry_id: 999 }] })).toEqual([]);
  expect(await loadEligibleOfflineQueue({ ...prepared, attempts: [{ ...parent, conflict: "Content changed" }] })).toEqual([]);
  localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([{ backendId: "local-repeat",
    queueEntryId: 799, attemptId: "retained-conflict", completedAt: parent.completedAt, expectedRevision: 1,
    outcome: "correct", guided: false, state: "conflicted", reconciliationSequence: 1,
    conflict: { code: "card_revision_changed", message: "Content changed", retryable: false } }]));
  expect(await loadEligibleOfflineQueue(prepared)).toEqual([]);
});

const legacyFailureFormats = [
  { name: "numeric", saved: [801] },
  { name: "object", saved: [{ queueEntryId: 801, operationId: "legacy-guided-operation" }] },
];

function legacyMarkerQueuePayload(cardId = "desktop-active", revision = 1, attemptFailed = false) {
  return { count: 1, cards: [{ id: cardId, revision, queue_entry_id: 801,
    start_fen: startingFen, moves: ["e2e4"], content_type: "opening", repertoire_name: cardId,
    repertoire_source: "PGN", attempt_failed: attemptFailed }] };
}

function expectNoCleanLegacyAttempt() {
  const state = useTrainingStore.getState();
  // These are the store inputs used by TrainingView and rateCard to disable grading.
  expect(Boolean(state.serviceError && !state.isOfflineQueueActive) || state.isAttemptFailed).toBe(true);
}

it.each(legacyFailureFormats)("legacy $name marker blocks clean hydration during delayed replay and reload", async ({ saved }) => {
  localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify(saved));
  let resolveMarker!: (response: Response) => void;
  let serverFailed = false;
  const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/fail")
    ? new Promise<Response>(resolve => { resolveMarker = resolve; })
    : Response.json(legacyMarkerQueuePayload("desktop-active", 1, serverFailed)));
  vi.stubGlobal("fetch", fetcher);
  try {
    await fetchAndInitializeQueue().catch(() => undefined);
    expect(resolveMarker).toBeDefined();
    expectNoCleanLegacyAttempt();
    const normalized = JSON.parse(localStorage.getItem("tempo-pending-training-failures-v1")!);
    expect(normalized).toEqual([{ queueEntryId: 801, operationId: expect.any(String) }]);
    useTrainingStore.setState(useTrainingStore.getInitialState(), true);
    await fetchAndInitializeQueue().catch(() => undefined);
    expectNoCleanLegacyAttempt();
    expect(JSON.parse(localStorage.getItem("tempo-pending-training-failures-v1")!)).toEqual(normalized);
    serverFailed = true;
    resolveMarker(Response.json({ attempt_failed: true }));
    await waitFor(() => expect(pendingTrainingFailures()).toEqual([]));
    await fetchAndInitializeQueue();
    expect(useTrainingStore.getState().getCard().backendId).toBe("desktop-active");
    expect(useTrainingStore.getState().isAttemptFailed).toBe(true);
    expect(useTrainingStore.getState().attempt.phase).toBe("guided");
  } finally {
    resolveMarker?.(Response.json({ attempt_failed: true }));
    await flushTrainingFailures();
  }
});

it.each(legacyFailureFormats)("legacy $name marker blocks clean hydration when replay is deferred behind another review", async ({ saved }) => {
  localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify(saved));
  enqueuePendingReview({ backendId: "earlier-card", queueEntryId: 41, outcome: "correct", guided: false });
  const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/review")
    ? Response.json({ detail: "Review transport unavailable" }, { status: 503 })
    : Response.json(legacyMarkerQueuePayload()));
  vi.stubGlobal("fetch", fetcher);
  await fetchAndInitializeQueue().catch(() => undefined);
  expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/fail"))).toBe(false);
  expectNoCleanLegacyAttempt();
  const normalized = localStorage.getItem("tempo-pending-training-failures-v1");
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  await fetchAndInitializeQueue().catch(() => undefined);
  expectNoCleanLegacyAttempt();
  expect(localStorage.getItem("tempo-pending-training-failures-v1")).toBe(normalized);
});

it("authoritative legacy marker resolution guides a retained active attempt without replacing its identity", async () => {
  useTrainingStore.getState().hydrateLocalQueue([activeCard], true, 1);
  const logicalAttempt = useTrainingStore.getState().attempt;
  localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify([801]));
  let resolveMarker!: (response: Response) => void;
  let serverFailed = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/fail")
    ? new Promise<Response>(resolve => { resolveMarker = resolve; })
    : Response.json({ ...legacyMarkerQueuePayload("desktop-active", 1, serverFailed), cards: [{
      ...legacyMarkerQueuePayload("desktop-active", 1, serverFailed).cards[0], moves: ["e2e4", "e7e5", "g1f3"],
    }] })));
  try {
    await fetchAndInitializeQueue().catch(() => undefined);
    expectNoCleanLegacyAttempt();
    serverFailed = true;
    resolveMarker(Response.json({ attempt_failed: true }));
    await flushTrainingFailures();
    await fetchAndInitializeQueue();
    expect(useTrainingStore.getState().attempt).toMatchObject({ ...logicalAttempt, phase: "guided" });
    expect(useTrainingStore.getState().isAttemptFailed).toBe(true);
    expect(useTrainingStore.getState().serviceError).toBe("");
  } finally {
    resolveMarker?.(Response.json({ attempt_failed: true }));
    await flushTrainingFailures();
  }
});

it.each(legacyFailureFormats)("legacy $name marker cannot guide revised or replacement content after ambiguous validation", async ({ saved }) => {
  localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify(saved));
  for (const [cardId, revision] of [["desktop-active", 2], ["replacement-card", 1]] as const) {
    const fetcher = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      if (String(input).endsWith("/fail")) {
        expect(options?.body).toBeUndefined();
        return Response.json({ code: "queue_attempt_unprovable", detail: "The original attempt is ambiguous" }, { status: 409 });
      }
      return Response.json(legacyMarkerQueuePayload(cardId, revision));
    });
    vi.stubGlobal("fetch", fetcher);
    await fetchAndInitializeQueue().catch(() => undefined);
    await flushTrainingFailures().catch(() => undefined);
    expectNoCleanLegacyAttempt();
    expect(useTrainingStore.getState().isAttemptFailed).toBe(false);
    expect(pendingTrainingFailures()).toEqual([801]);
    const normalized = JSON.parse(localStorage.getItem("tempo-pending-training-failures-v1")!);
    expect(normalized).toEqual([{ queueEntryId: 801, operationId: expect.any(String) }]);
    useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  }
});

it("modern guided markers hydrate only their exact card and revision", async () => {
  enqueueTrainingFailure(801, "desktop-active", 1);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/fail")
    ? Response.json({ detail: "Temporarily unavailable" }, { status: 503 })
    : Response.json(legacyMarkerQueuePayload())));
  await fetchAndInitializeQueue();
  expect(useTrainingStore.getState().isAttemptFailed).toBe(true);
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/fail")
    ? Response.json({ detail: "Temporarily unavailable" }, { status: 503 })
    : Response.json(legacyMarkerQueuePayload("desktop-active", 2))));
  await fetchAndInitializeQueue();
  expect(useTrainingStore.getState().isAttemptFailed).toBe(false);
});

it.each(legacyFailureFormats)("unresolved legacy $name marker also blocks a prepared offline attempt", async ({ saved }) => {
  localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify(saved));
  useTrainingStore.getState().hydrateLocalQueue([activeCard], true, 1);
  useTrainingStore.getState().setOfflineQueue(true);
  await expect(loadEligibleOfflineQueue({ localDate: localDayKey(), preparedAt: new Date().toISOString(),
    cards: legacyMarkerQueuePayload().cards, attempts: [], nextTemporaryId: -1 } as PreparedTraining)).rejects.toThrow("identity is pending");
  expectNoCleanLegacyAttempt();
});

it.each(legacyFailureFormats)("confirmed legacy $name replay accepts a fresh authoritative guided queue in the same hydration", async ({ saved }) => {
  localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify(saved));
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/fail")) return Response.json({ attempt_failed: true });
    await flushTrainingFailures();
    return Response.json(legacyMarkerQueuePayload("desktop-active", 1, true));
  }));
  await fetchAndInitializeQueue();
  expect(pendingTrainingFailures()).toEqual([]);
  expect(useTrainingStore.getState().isAttemptFailed).toBe(true);
  expect(useTrainingStore.getState().serviceError).toBe("");
});


it.each(["complete", "missing"])("PR105 initial queue recovery confirms retained reviews before replay receipt=%s", async receiptState => {
  enqueuePendingReview({ backendId: "retained-card", queueEntryId: 811, attemptId: "reload-original", outcome: "correct", guided: false });
  const original = pendingReviews()[0];
  const fetcher = vi.fn<typeof fetch>(async (input, options) => {
    if (String(input).includes("/api/operations/")) return receiptState === "complete"
      ? Response.json({ state: "complete", response: { persisted: true } }) : new Response(null, { status: 404 });
    if (options?.method === "POST" && String(input).endsWith("/review")) return Response.json({ persisted: true });
    return Response.json({ cards: [], count: 0 });
  });
  vi.stubGlobal("fetch", fetcher);
  await fetchAndInitializeQueue(false, { preparePhoneQueue: false });
  const reviewTraffic = fetcher.mock.calls.filter(([input]) => String(input).includes("/api/operations/") || String(input).endsWith("/review"));
  expect(reviewTraffic[0][0]).toContain("/api/operations/review-attempt%3Areload-original");
  const posts = reviewTraffic.filter(([, options]) => options?.method === "POST");
  expect(posts).toHaveLength(receiptState === "complete" ? 0 : 1);
  if (receiptState === "missing") {
    expect(new Headers(posts[0][1]?.headers).get("Idempotency-Key")).toBe("review-attempt:reload-original");
    expect(JSON.parse(String(posts[0][1]?.body))).toMatchObject({ attempt_id: original.attemptId, recorded_at: original.completedAt });
  }
  expect(pendingReviews()).toEqual([]);
});

it("real-game promotion preserves the displayed attempt then starts the authoritative obligation", async () => {
  useTrainingStore.getState().hydrateLocalQueue([activeCard], true, 1);
  useTrainingStore.getState().setStep(1);
  const before = useTrainingStore.getState();
  const attemptId = before.attempt.attemptId;
  const fen = before.currentFenString;
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ count: 2, cards: [{
    id: "game-obligation", queue_entry_id: 803, revision: 1, start_fen: startingFen,
    moves: ["d2d4"], content_type: "opening", repertoire_name: "Missed card", repertoire_source: "PGN",
    trained_color: "white", gameplay_priority_reason: "Priority review · missed in a recent game",
  }, {
    id: "desktop-active", queue_entry_id: 801, revision: 1, start_fen: startingFen,
    moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening", repertoire_name: "Active", repertoire_source: "PGN",
  }] })));
  await fetchAndInitializeQueue(false);
  expect(useTrainingStore.getState().attempt.attemptId).toBe(attemptId);
  expect(useTrainingStore.getState().currentFenString).toBe(fen);
  expect(useTrainingStore.getState().getCard().id).toBe(activeCard.id);
  await fetchAndInitializeQueue(true);
  expect(useTrainingStore.getState().getCard().backendId).toBe("game-obligation");
  expect(useTrainingStore.getState().attempt.attemptId).not.toBe(attemptId);
});
