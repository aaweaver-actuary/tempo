import { afterEach, expect, it, vi } from "vitest";
import { evidenceCompletion, openingEvidenceStorage } from "../fixtures/opening-evidence-storage";
import { localDayKey } from "../../app/utils/local";
import type { PreparedTraining } from "../../app/lib/offline-training";
import type { BackendQueueCard } from "../../app/domain/transport";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const completion = evidenceCompletion();
const prepared: PreparedTraining = { localDate: localDayKey(), preparedAt: completion.started_at,
  nextTemporaryId: 1000000, attempts: [], cards: [{ id: completion.manifest.card_id, revision: 3,
    queue_entry_id: 101, latest_review_id: 7, cycle: 2, content_type: "opening", scheduling_mode: "normal",
    repertoire_name: "Phone opening", repertoire_source: "PGN", trained_color: "white",
    start_fen: completion.manifest.decisions[0].fen, moves: ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
    opening_decision_manifest: completion.manifest } as BackendQueueCard] };

async function setup() {
  const storage = await openingEvidenceStorage();
  storage.stores.training.set("prepared-daily-queue", structuredClone(prepared));
  storage.seed(completion);
  const notifications = await import("../../app/lib/notifications");
  const notify = vi.spyOn(notifications, "publishNotification").mockImplementation(() => "test-notification");
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  return { ...storage, notify, fetch, offline: await import("../../app/lib/offline-training"),
    journal: await import("../../app/lib/opening-evidence-journal") };
}

it.each(["QuotaExceededError", "NS_ERROR_DOM_QUOTA_REACHED"])("AS-16 offline review quota falls back to durable aggregate-only phone review (%s)", async quotaName => {
  const state = await setup();
  const original = structuredClone(completion);
  state.controls.beforePut = (store, value) => store === "training" && JSON.stringify(value).includes("openingEvidenceCompletion")
    ? new DOMException("Prepared evidence exceeds quota", quotaName) : undefined;
  const result = await state.offline.recordOfflineAttempt(101, "correct", false, undefined, { attemptId: completion.attempt_id, completion });
  expect(result.attempts).toHaveLength(1);
  expect(result.attempts[0]).toMatchObject({ attemptId: completion.attempt_id, completedAt: completion.terminal!.ended_at,
    localEntryId: 101, cardId: completion.manifest.card_id, expectedRevision: 3, expectedReviewId: 7, queueCycle: 2,
    outcome: "correct", guided: false, openingEvidenceFallbackReason: "local_storage_quota" });
  expect(result.attempts[0]).not.toHaveProperty("openingEvidenceCompletion");
  expect(result.cards).toHaveLength(1);
  expect(result.cards[0]).toMatchObject({ queue_entry_id: 1000000, parent_local_entry_id: 101, attempt_state: "reinforcement", cycle: 3 });
  expect(completion).toEqual(original);
  await vi.waitFor(() => expect(state.stores.opening_attempts.get(completion.attempt_id)).toMatchObject({ delivery_state: "retained", retention_reason: "local_storage_quota" }));
  expect(state.commits[0]).toEqual(["training"]);
  const acknowledgment = vi.spyOn(state.journal, "acknowledgeOpeningReview");
  state.fetch.mockRejectedValueOnce(new TypeError("Uncertain phone review"));
  await expect(state.offline.replayOfflineAttempts()).rejects.toThrow("Uncertain phone review");
  const [firstUrl, firstRequest] = state.fetch.mock.calls[0] as [string, RequestInit];
  expect(new Headers(firstRequest.headers).get("Idempotency-Key")).toBe(`phone-reconcile:${completion.attempt_id}:aggregate-only`);
  expect(JSON.parse(firstRequest.body as string)).not.toHaveProperty("opening_evidence_completion");
  // Reload preserves the persisted choice; the separately retained journal cannot restore the envelope.
  vi.resetModules(); await state.install();
  const reloaded = await import("../../app/lib/offline-training");
  state.fetch.mockResolvedValue(Response.json({ persisted: true, review_id: 8, requeue_entry_id: 102 }));
  const synced = await reloaded.replayOfflineAttempts();
  expect(state.fetch.mock.calls[1][0]).toBe(firstUrl);
  expect(state.fetch.mock.calls[1][1].body).toBe(firstRequest.body);
  expect(new Headers(state.fetch.mock.calls[1][1].headers).get("Idempotency-Key")).toBe(`phone-reconcile:${completion.attempt_id}:aggregate-only`);
  expect(synced?.attempts[0]).toMatchObject({ serverReviewId: 8, serverAcknowledged: true, openingEvidenceFallbackReason: "local_storage_quota" });
  await reloaded.replayOfflineAttempts(); expect(state.fetch).toHaveBeenCalledTimes(2);
  expect(acknowledgment).not.toHaveBeenCalled();
  expect(state.stores.opening_attempts.get(completion.attempt_id)).toMatchObject({ delivery_state: "retained" });
  expect(state.stores.opening_events.size).toBe(completion.events.length);
});

it("AS-16 aggregate phone storage quota without evidence remains blocking", async () => {
  const state = await setup();
  state.controls.beforePut = store => store === "training" ? new DOMException("No capacity", "QuotaExceededError") : undefined;
  await expect(state.offline.recordOfflineAttempt(101, "correct", false)).rejects.toMatchObject({ name: "QuotaExceededError" });
  expect(state.writes.filter(write => write.store === "training")).toHaveLength(1);
  expect(state.stores.training.get("prepared-daily-queue")).toEqual(prepared); expect(state.fetch).not.toHaveBeenCalled();
});

it("AS-16 offline aggregate-only storage failure remains blocking and retryable", async () => {
  const state = await setup();
  state.controls.beforePut = store => store === "training" ? new DOMException("No capacity", "QuotaExceededError") : undefined;
  await expect(state.offline.recordOfflineAttempt(101, "again", true, undefined, { attemptId: completion.attempt_id, completion })).rejects.toMatchObject({ name: "QuotaExceededError" });
  expect(state.stores.training.get("prepared-daily-queue")).toEqual(prepared);
  expect(state.commits).toEqual([]); expect(state.fetch).not.toHaveBeenCalled();
  state.controls.beforePut = undefined;
  const retry = await state.offline.recordOfflineAttempt(101, "again", true, undefined, { attemptId: completion.attempt_id, completion });
  expect(retry.attempts).toHaveLength(1); expect(retry.cards[0].queue_entry_id).toBe(1000000);
});

it.each(["SecurityError", "InvalidStateError"])("AS-16 offline evidence non-quota storage errors remain blocking (%s)", async name => {
  const state = await setup();
  state.controls.beforePut = store => store === "training" ? new DOMException("Storage unavailable", name) : undefined;
  await expect(state.offline.recordOfflineAttempt(101, "correct", false, undefined, { attemptId: completion.attempt_id, completion })).rejects.toMatchObject({ name });
  expect(state.writes.filter(write => write.store === "training")).toHaveLength(1);
  expect(state.stores.training.get("prepared-daily-queue")).toEqual(prepared); expect(state.fetch).not.toHaveBeenCalled();
});

it("AS-16 offline quota retention failure cannot undo a durable compact review", async () => {
  const state = await setup();
  state.controls.beforePut = (store, value) => store === "opening_attempts" || (store === "training" && JSON.stringify(value).includes("openingEvidenceCompletion"))
    ? new DOMException("No evidence capacity", "QuotaExceededError") : undefined;
  const result = await state.offline.recordOfflineAttempt(101, "correct", false, undefined, { attemptId: completion.attempt_id, completion });
  expect(result.attempts).toHaveLength(1);
  await vi.waitFor(() => expect(state.notify).toHaveBeenCalledWith(expect.objectContaining({ severity: "warning", message: expect.stringContaining("could not be retained") })));
  expect((state.stores.training.get("prepared-daily-queue") as PreparedTraining).attempts).toHaveLength(1);
  // A retry after optional diagnostic failure cannot record a second review or repeat.
  const duplicate = await state.offline.recordOfflineAttempt(101, "correct", false, undefined, { attemptId: completion.attempt_id, completion });
  expect(duplicate.attempts).toHaveLength(1); expect(duplicate.nextTemporaryId).toBe(result.nextTemporaryId);
});
