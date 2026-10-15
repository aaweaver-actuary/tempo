import { expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";
import { closeStudyIntentStorage, initializeStudyIntentStorage, legacyStudyCutoverRequired, savedTeachingFacts, studyIntentRecords, studyIntentStore } from "../../app/lib/study-intent-storage";
import { executeStudyRequest, recoverStudyRequests } from "../../app/lib/study-command-client";
import { enqueueTeachingState, flushTeachingStates, pendingTeachingStates } from "../../app/lib/teaching-state-outbox";
import { enqueueTrainingFailure, flushTrainingFailures, pendingTrainingFailures } from "../../app/lib/training-failure-outbox";
import { interceptIntentWrites } from "./support/study-intent-fixtures";

const legacyReview = (id: string) => ({ backendId: id, queueEntryId: id === "a" ? 1 : 2, attemptId: id,
  outcome: "again", guided: true, completedAt: "2026-10-15T16:00:00Z" });
function seedLegacy() {
  closeStudyIntentStorage();
  localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([legacyReview("a"), legacyReview("b")]));
}
it("issue221 interrupted migration reimports retained sources and committed records cannot resurrect", async () => {
  seedLegacy();
  const fault = interceptIntentWrites(record => { if (record.key === "review:b") throw new DOMException("interrupted", "AbortError"); });
  await expect(initializeStudyIntentStorage(true)).rejects.toThrow("could not commit");
  fault.mockRestore();
  const store = await initializeStudyIntentStorage(true);
  expect((await store.list("review")).map(record => record.key)).toEqual(["review:a", "review:b"]);
  await store.update("review:a", current => ({ ...current, state: "committed", receipt: { persisted: true } }));
  closeStudyIntentStorage();
  const reopened = await initializeStudyIntentStorage();
  expect((await reopened.get("review:a"))?.state).toBe("committed");
  expect((await reopened.list("review", true)).map(record => record.key)).toEqual(["review:b"]);
  expect(localStorage.getItem("tempo-pending-training-reviews-v1")).toBe(JSON.stringify([legacyReview("a"), legacyReview("b")]));
  expect(await reopened.checkpoint("legacy-import")).toEqual({ complete: true, sourcesRetained: true });
});
it("issue221 conversion requires acknowledgement and an observed old writer closes recovery", async () => {
  seedLegacy();
  await expect(initializeStudyIntentStorage()).rejects.toThrow("close older");
  await initializeStudyIntentStorage(true);
  window.dispatchEvent(new StorageEvent("storage", { key: "tempo-pending-training-reviews-v1", oldValue: "[]", newValue: "changed" }));
  expect(legacyStudyCutoverRequired()).toBe(true);
  await expect(studyIntentStore()).rejects.toThrow("older");
  // Closing the old writer and explicitly reopening acknowledges the chosen cutover policy.
  await initializeStudyIntentStorage(true);
  expect(studyIntentRecords("review")).toHaveLength(2);
});
it("issue221 a legacy writer during conversion pauses instead of declaring migration complete", async () => {
  seedLegacy();
  const fault = interceptIntentWrites(record => { if (record.key === "review:a") localStorage.setItem("tempo-pending-teaching-states-v1", "[]"); });
  await expect(initializeStudyIntentStorage(true)).rejects.toThrow("changed study data during recovery");
  fault.mockRestore();
  expect(legacyStudyCutoverRequired()).toBe(true);
  await initializeStudyIntentStorage(true);
  expect(studyIntentRecords("review")).toHaveLength(2);
});
it("issue221 migration and current teaching use one identity and preserve demo facts", async () => {
  closeStudyIntentStorage();
  localStorage.setItem("tempo-pending-teaching-states-v1", JSON.stringify([{ cardId: "card", revision: 3, ply: 2 }]));
  localStorage.setItem("tempo-seen-moves", JSON.stringify(["demo:2:0"]));
  localStorage.setItem("tempo-first-clean-passes", JSON.stringify(["offline-demo"]));
  await initializeStudyIntentStorage(true);
  await enqueueTeachingState({ cardId: "card", revision: 3, ply: 2 });
  expect(pendingTeachingStates()).toEqual([{ cardId: "card", revision: 3, ply: 2 }]);
  expect(savedTeachingFacts()).toEqual(expect.arrayContaining([
    expect.objectContaining({ cardId: "demo", revision: 2, ply: 0 }), expect.objectContaining({ cardId: "offline-demo", kind: "first-clean" }),
  ]));
});
it("issue221 atomic teaching failure cannot publish a fact or a pending delivery", async () => {
  const fault = interceptIntentWrites(record => { if (record.kind === "teaching") throw new DOMException("quota", "QuotaExceededError"); });
  await expect(enqueueTeachingState({ cardId: "card", revision: 1, ply: 0 })).rejects.toThrow("could not commit");
  fault.mockRestore();
  expect(await (await studyIntentStore()).teachingFacts("card")).toEqual([]);
  expect(pendingTeachingStates()).toEqual([]);
});
it.each(["teaching", "failure"])("issue219 unconfirmed accepted %s command stays pending", async kind => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state: "accepted" }, { status: 202 })));
  if (kind === "teaching") {
    await enqueueTeachingState({ cardId: "card", revision: 1, ply: 0 });
    await expect(flushTeachingStates()).rejects.toThrow("still pending");
    expect(pendingTeachingStates()).toHaveLength(1);
  } else {
    await enqueueTrainingFailure(2, "card", 1);
    await expect(flushTrainingFailures()).rejects.toThrow("still pending");
    expect(pendingTrainingFailures()).toEqual([2]);
  }
  vi.unstubAllGlobals();
});
it("issue219 specialized lost-response recovery confirms the original receipt without remount or repost", async () => {
  const request = { scope: "study:s:e:4:2", endpoint: "/api/studies/s/exercises/e/attempts", requestId: "original",
    body: { attempt_id: "original", revision: 2, answer: { type: "choice", option_ids: ["a"], displayed_order: ["a", "b"] } } };
  const fetcher = vi.fn(async (_input: RequestInfo | URL, options?: RequestInit) => {
    if (options?.method === "POST") throw new Error("response lost after commit");
    return Response.json({ state: "complete", response: { attempt_id: "original", assessment: { outcome: "correct" } } });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(executeStudyRequest(request)).rejects.toThrow("response lost");
  closeStudyIntentStorage(); await initializeStudyIntentStorage();
  await recoverStudyRequests();
  const record = await (await studyIntentStore()).get(`${request.scope}:original`);
  expect(record).toMatchObject({ state: "committed", payload: { body: request.body } });
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
  vi.unstubAllGlobals();
});
it("issue219 a different study attempt response never settles the original command", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ attempt_id: "another", assessment: { outcome: "correct" } })));
  await expect(executeStudyRequest({ scope: "scope", endpoint: "/attempts", requestId: "original", body: { attempt_id: "original" } })).rejects.toThrow("original study attempt");
  expect((await (await studyIntentStore()).get("scope:original"))?.state).toBe("pending");
  vi.unstubAllGlobals();
});
it("issue221 unavailable storage does not silently create an in-memory substitute", async () => {
  closeStudyIntentStorage();
  const previous = globalThis.indexedDB;
  Object.defineProperty(globalThis, "indexedDB", { value: undefined, configurable: true, writable: true });
  try { await expect(initializeStudyIntentStorage()).rejects.toThrow("unavailable"); }
  finally { Object.defineProperty(globalThis, "indexedDB", { value: previous ?? new IDBFactory(), configurable: true, writable: true }); }
});

it("issue221 simultaneous initialization shares one storage connection", async () => {
  closeStudyIntentStorage();
  const [first, second] = await Promise.all([initializeStudyIntentStorage(), initializeStudyIntentStorage()]);
  expect(first).toBe(second);
});
it("issue221 concurrent commits publish their pending projection before awaited enqueue returns", async () => {
  const { enqueuePendingReview, pendingReviews } = await import("../../app/lib/review-outbox");
  await Promise.all([
    enqueuePendingReview(legacyReview("a") as Parameters<typeof enqueuePendingReview>[0]),
    enqueuePendingReview(legacyReview("b") as Parameters<typeof enqueuePendingReview>[0]),
    enqueueTeachingState({ cardId: "card", revision: 1, ply: 0 }),
  ]);
  expect(pendingReviews().map(review => review.attemptId)).toEqual(expect.arrayContaining(["a", "b"]));
  expect(pendingTeachingStates()).toHaveLength(1);
});
