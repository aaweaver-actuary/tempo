import { expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";
import { closeStudyIntentStorage, initializeStudyIntentStorage, legacyStudyCutoverRequired, savedTeachingFacts, studyIntentRecords, studyIntentStore } from "../../database/browser/study-intent-storage";
import { executeStudyRequest, recoverStudyRequests } from "../../app/lib/study-command-client";
import { enqueueTeachingState, flushTeachingStates, pendingTeachingStates } from "../../app/lib/teaching-state-outbox";
import { enqueueTrainingFailure, flushTrainingFailures, pendingTrainingFailures } from "../../app/lib/training-failure-outbox";
import { interceptIntentWrites } from "./support/study-intent-fixtures";
import { completedRecoveryReceipt, deferred, enterRecovery, observe, recoveryCommand } from "./support/recovery-ownership-fixtures";
import * as storage from "../../database/browser/study-intent-storage";
import { notifications } from "../../app/lib/notifications";

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
it.each(["failure", "teaching"])("issue219 migrated %s recovers the original receipt before any replay", async kind => {
  closeStudyIntentStorage();
  if (kind === "failure") localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify([{ queueEntryId: 5, operationId: "legacy-original", backendId: "card", expectedRevision: 1 }]));
  else localStorage.setItem("tempo-pending-teaching-states-v1", JSON.stringify([{ cardId: "card", revision: 1, ply: 0 }]));
  await initializeStudyIntentStorage(true);
  const fetcher = vi.fn(async (_input: RequestInfo | URL, options?: RequestInit) => {
    expect(options?.method).not.toBe("POST");
    return Response.json({ state: "complete", response: kind === "failure" ? { attempt_failed: true } : { cardId: "card", revision: 1, ply: 0, taughtAt: "2026-10-15T16:00:00Z" } });
  });
  vi.stubGlobal("fetch", fetcher);
  if (kind === "failure") { await flushTrainingFailures(); expect(pendingTrainingFailures()).toEqual([]); }
  else { await flushTeachingStates(); expect(pendingTeachingStates()).toEqual([]); }
  expect(fetcher).toHaveBeenCalledOnce();
  expect(String(fetcher.mock.calls[0][0])).toContain(kind === "failure" ? "/api/operations/legacy-original" : "/api/operations/teaching%3Acard%3A1%3A0");
  vi.unstubAllGlobals();
});

// PR #285 repair characterizations exercise the installed adapter and IndexedDB transactions.
const repairScope = "study:repair:exercise:practice:1";
const repairCommand = (id: string, scope = repairScope) => ({ key: `${scope}:${id}`, kind: "study-command", attemptId: id,
  requestId: id, endpoint: "/api/studies/repair/exercises/exercise/attempts", revision: 1,
  createdAt: "2026-10-15T16:00:00Z", payload: { scope, headers: { "Content-Type": "application/json", "Idempotency-Key": id }, body: { attempt_id: id } } });
it("pr285_recovery_ownership_live_replacement_does_not_inherit_aborted_pass", async () => {
  const command = recoveryCommand(); const store = await studyIntentStore();
  await store.put(command, { started: true });
  const receiptStarted = deferred<void>(), response = deferred<Response>();
  const trace: string[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    expect(String(input)).toContain(`/api/operations/${command.requestId}`); expect(options?.method).not.toBe("POST");
    trace.push("receipt:held"); receiptStarted.resolve(); return response.promise;
  });
  vi.stubGlobal("fetch", fetcher);
  const firstController = new AbortController(), replacementController = new AbortController();
  const firstReason = new Error("Only the first recovery was cancelled");
  const first = await enterRecovery(() => recoverStudyRequests(firstController.signal), trace, "A");
  let replacement: Awaited<ReturnType<typeof enterRecovery<void>>> | undefined;
  try {
    await receiptStarted.promise;
    firstController.abort(firstReason); trace.push("A:cancelled");
    replacement = await enterRecovery(() => recoverStudyRequests(replacementController.signal), trace, "B");
    trace.push("receipt:released"); response.resolve(Response.json(completedRecoveryReceipt(command)));
    console.info("pr285 direct recovery ordering", trace);
    expect(await replacement.outcome).toEqual({ status: "fulfilled", value: undefined });
    expect(await first.outcome).toEqual({ status: "rejected", reason: firstReason });
    expect(replacementController.signal.aborted).toBe(false);
    expect(await store.get(command.key)).toMatchObject({ ...command, state: "committed" });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(trace.indexOf("A:cancelled")).toBeLessThan(trace.indexOf("B:storage-acquired"));
    expect(trace.indexOf("B:storage-acquired")).toBeLessThan(trace.indexOf("receipt:released"));
  } finally {
    response.resolve(Response.json(completedRecoveryReceipt(command)));
    await Promise.all([first.outcome, replacement?.outcome]); vi.unstubAllGlobals();
  }
});

async function recoveryOverlap(options: { receipt?: "pending" | "rejected"; first?: "none"; second?: "none" | "same";
  cancelFirst?: boolean; cancelSecond?: boolean; started?: boolean } = {}) {
  const command = recoveryCommand(["overlap", options.receipt ?? "complete", options.first ?? "signal", options.second ?? "independent",
    options.cancelFirst, options.cancelSecond, options.started].join("-")); const store = await studyIntentStore();
  await store.put(command, { started: options.started !== false });
  const requestStarted = deferred<void>(), response = deferred<Response>(); const trace: string[] = [];
  const requests: Array<{ url: string; options?: RequestInit }> = [];
  const checkpoint = vi.spyOn(store, "checkpoint");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, requestOptions?: RequestInit) => {
    requests.push({ url: String(input), options: requestOptions }); requestStarted.resolve(); return response.promise;
  }));
  const firstController = new AbortController(), secondController = new AbortController();
  const firstReason = new Error("cancel A only"), secondReason = new Error("cancel B only");
  const first = await enterRecovery(() => recoverStudyRequests(options.first === "none" ? undefined : firstController.signal), trace, "A");
  let second: Awaited<ReturnType<typeof enterRecovery<void>>> | undefined;
  const receipt = options.receipt === "pending" ? { operation_id: command.requestId, state: "pending" } : options.receipt === "rejected"
    ? { operation_id: command.requestId, state: "failed", error: { status_code: 422, message: "Original validation rejection", retryable: false } }
    : completedRecoveryReceipt(command);
  const release = () => response.resolve(Response.json(options.started === false ? completedRecoveryReceipt(command).response : receipt));
  try {
    await requestStarted.promise;
    if (options.cancelFirst) firstController.abort(firstReason);
    const signal = options.second === "none" ? undefined : options.second === "same" ? firstController.signal : secondController.signal;
    second = await enterRecovery(() => recoverStudyRequests(signal), trace, "B");
    if (options.cancelSecond) secondController.abort(secondReason);
    release();
    const outcomes = await Promise.all([first.outcome, second.outcome]);
    return { command, record: await store.get(command.key), requests, outcomes, firstReason, secondReason,
      passes: checkpoint.mock.calls.filter(([key]) => key === "study-command-recovery:cursor").length };
  } finally { release(); await Promise.all([first.outcome, second?.outcome]); checkpoint.mockRestore(); vi.unstubAllGlobals(); }
}

it.each(["pending", "rejected"] as const)("pr285_recovery_ownership_%s_original_does_not_poison_replacement", async receipt => {
  const result = await recoveryOverlap({ receipt, cancelFirst: true });
  expect(result.outcomes).toEqual([{ status: "rejected", reason: result.firstReason }, { status: "fulfilled", value: undefined }]);
  expect(result.record).toMatchObject({ ...result.command, state: receipt === "pending" ? "pending" : "discarded" });
  const notices = notifications().filter(notice => notice.key === `study-command:${result.command.key}`);
  expect(notices.some(notice => notice.severity === "success")).toBe(false);
  expect(notices.at(-1)?.message).toContain(receipt === "pending" ? "remains saved" : "Original validation rejection");
  expect(result.requests).toHaveLength(1);
});
it("pr285_recovery_ownership_each_caller_observes_its_own_cancellation", async () => {
  const result = await recoveryOverlap({ cancelFirst: true, cancelSecond: true });
  expect(result.outcomes).toEqual([{ status: "rejected", reason: result.firstReason }, { status: "rejected", reason: result.secondReason }]);
  expect(result.record?.state).toBe("committed");
});
it.each([
  { second: "same" as const, passes: 1 },
  { first: "none" as const, second: "none" as const, passes: 1 },
  { first: "none" as const, passes: 2 },
  { second: "none" as const, cancelFirst: true, passes: 2 },
  { passes: 2 },
])("pr285_recovery_ownership_signal_identity_and_signalless_sharing (%j)", async scenario => {
  const result = await recoveryOverlap(scenario);
  expect(result.outcomes[1]).toEqual({ status: "fulfilled", value: undefined });
  expect(result.passes).toBe(scenario.passes); expect(result.requests).toHaveLength(1);
  expect(result.record).toMatchObject({ ...result.command, state: "committed" });
});
it.each([false, true])("pr285_recovery_ownership_cancelled_storage_acquisition_starts_no_delivery (%s)", async duringAcquisition => {
  const command = recoveryCommand(); const store = await studyIntentStore(); const retained = await store.put(command);
  const controller = new AbortController(), reason = new Error("cancelled before recovery acquired storage");
  const acquired = deferred<void>(), release = deferred<void>(); const acquire = storage.studyIntentStore;
  const acquisition = vi.spyOn(storage, "studyIntentStore").mockImplementation(async () => {
    const current = await acquire(); acquired.resolve(); await release.promise; return current;
  });
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  if (!duringAcquisition) controller.abort(reason);
  const outcome = observe(recoverStudyRequests(controller.signal));
  try {
    if (duringAcquisition) { await acquired.promise; controller.abort(reason); }
    release.resolve();
    expect(await outcome).toEqual({ status: "rejected", reason });
    expect(fetcher).not.toHaveBeenCalled(); expect(await store.get(command.key)).toEqual(retained);
    expect(acquisition).toHaveBeenCalledTimes(duringAcquisition ? 1 : 0);
  } finally { release.resolve(); await outcome; acquisition.mockRestore(); vi.unstubAllGlobals(); }
});
it("pr285_recovery_ownership_real_session_coalesces_original_delivery", async () => {
  const result = await recoveryOverlap({ cancelFirst: true, started: false });
  expect(result.outcomes[1]).toEqual({ status: "fulfilled", value: undefined });
  expect(result.requests).toHaveLength(1);
  expect(result.requests[0]).toMatchObject({ url: result.command.endpoint, options: { method: "POST",
    body: JSON.stringify((result.command.payload as { body: unknown }).body), headers: (result.command.payload as { headers: unknown }).headers } });
  expect(result.record).toMatchObject({ ...result.command, state: "committed" });
});
it("pr285_recovery_ownership_old_finalizer_preserves_new_pass", async () => {
  const store = await studyIntentStore(), original = recoveryCommand(), later = recoveryCommand("later");
  await store.put(original, { started: true }); await store.put(later, { started: true });
  const firstStarted = deferred<void>(), firstResponse = deferred<Response>(), laterStarted = deferred<void>(), laterResponse = deferred<Response>();
  const trace: string[] = []; const checkpoint = vi.spyOn(store, "checkpoint");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/original")) { firstStarted.resolve(); return firstResponse.promise; }
    laterStarted.resolve(); return laterResponse.promise;
  }));
  const firstController = new AbortController(), replacementController = new AbortController();
  const first = await enterRecovery(() => recoverStudyRequests(firstController.signal), trace, "A");
  const outcomes = [first.outcome];
  try {
    await firstStarted.promise; firstController.abort(new Error("cancel A"));
    const replacement = await enterRecovery(() => recoverStudyRequests(replacementController.signal), trace, "B"); outcomes.push(replacement.outcome);
    firstResponse.resolve(Response.json(completedRecoveryReceipt(original)));
    expect((await first.outcome).status).toBe("rejected"); await laterStarted.promise;
    const joined = await enterRecovery(() => recoverStudyRequests(replacementController.signal), trace, "B-again"); outcomes.push(joined.outcome);
    expect(checkpoint.mock.calls.filter(([key]) => key === "study-command-recovery:cursor")).toHaveLength(2);
    laterResponse.resolve(Response.json(completedRecoveryReceipt(later)));
    expect((await replacement.outcome).status).toBe("fulfilled"); expect((await joined.outcome).status).toBe("fulfilled");
  } finally {
    firstResponse.resolve(Response.json(completedRecoveryReceipt(original))); laterResponse.resolve(Response.json(completedRecoveryReceipt(later)));
    await Promise.all(outcomes); checkpoint.mockRestore(); vi.unstubAllGlobals();
  }
});
it("pr285_recovery_ownership_overlapping_cursors_preserve_bounded_eventual_coverage", async () => {
  const store = await studyIntentStore();
  for (let index = 1; index <= 25; index++) await store.put(recoveryCommand(`fair-${index}`), { started: true });
  const started = deferred<void>(), release = deferred<void>(); const reads: string[] = [], trace: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const id = String(input).split("/").at(-1)!; reads.push(id);
    if (reads.length === 1) { started.resolve(); await release.promise; }
    return Response.json({ operation_id: id, state: "pending" });
  }));
  const controller = new AbortController(); const first = await enterRecovery(() => recoverStudyRequests(controller.signal), trace, "A");
  let replacement: Awaited<ReturnType<typeof enterRecovery<void>>> | undefined;
  const compare = vi.spyOn(store, "compareCheckpoint");
  try {
    await started.promise; controller.abort(new Error("cancel fair A"));
    replacement = await enterRecovery(() => recoverStudyRequests(new AbortController().signal), trace, "B");
    release.resolve(); await Promise.all([first.outcome, replacement.outcome]);
    expect(new Set(reads).size).toBe(20); expect(reads).toHaveLength(20);
    expect(await Promise.all(compare.mock.results.map(result => result.value))).toContain(false);
    const covered = new Set(reads); const resumed: string[] = [];
    // Either pass may win the first CAS. A losing pass must not overwrite it;
    // subsequent bounded passes must cover the tail and revisit older work.
    for (let pass = 0; pass < 3; pass++) {
      reads.length = 0; await recoverStudyRequests(); expect(reads).toHaveLength(20);
      expect(new Set(reads).size).toBe(20);
      for (const id of reads) covered.add(id); resumed.push(...reads);
    }
    expect(covered.size).toBe(25); expect(resumed).toContain("fair-1");
    expect(await store.list(undefined, true)).toHaveLength(25);
  } finally { release.resolve(); await Promise.all([first.outcome, replacement?.outcome]); compare.mockRestore(); vi.unstubAllGlobals(); }
});
it("pr285_recovery_ownership_old_store_cannot_publish_or_advance_replacement_cursor", async () => {
  const command = recoveryCommand("old-store-original"); const oldStore = await studyIntentStore(); await oldStore.put(command, { started: true });
  const started = deferred<void>(), response = deferred<Response>(); const trace: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async () => { started.resolve(); return response.promise; }));
  const pass = await enterRecovery(() => recoverStudyRequests(), trace, "old-store");
  try {
    await started.promise; closeStudyIntentStorage(); const replacement = await initializeStudyIntentStorage();
    response.resolve(Response.json(completedRecoveryReceipt(command)));
    expect(await pass.outcome).toEqual({ status: "fulfilled", value: undefined });
    expect(await replacement.get(command.key)).toMatchObject({ ...command, state: "pending" });
    expect(await replacement.checkpoint("study-command-recovery:cursor")).toBeUndefined();
    expect(notifications().filter(notice => notice.key === `study-command:${command.key}`)).toEqual([]);
  } finally { response.resolve(Response.json(completedRecoveryReceipt(command))); await pass.outcome; vi.unstubAllGlobals(); }
});
it("pr285_rejected_answer_retains_terminal_validation_evidence", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Submit one move" }, { status: 422 })));
  await expect(executeStudyRequest({ scope: repairScope, endpoint: "/api/studies/repair/exercises/exercise/attempts", requestId: "invalid",
    body: { attempt_id: "invalid", revision: 1, answer: { type: "move_line", moves: [] } } })).rejects.toThrow("Submit one move");
  expect(await (await studyIntentStore()).get(`${repairScope}:invalid`)).toMatchObject({ state: "discarded", receipt: { type: "rejected", status: 422, requestId: "invalid" } });
  vi.unstubAllGlobals();
});
it("pr285_storage_retry_reopens_after_successful_initialization_then_failed_refresh", async () => {
  const { waitFor } = await import("@testing-library/react");
  const { studyIntentStorageReady } = await import("../../database/browser/study-intent-storage");
  const store = await studyIntentStore();
  await store.put(repairCommand("retained"));
  const fault = vi.spyOn(store, "list").mockRejectedValue(new Error("refresh failed"));
  await store.put(repairCommand("trigger-refresh"));
  await waitFor(() => expect(studyIntentStorageReady()).toBe(false));
  fault.mockRestore();
  const recovered = await initializeStudyIntentStorage();
  expect(studyIntentStorageReady()).toBe(true);
  expect(await recovered.get(`${repairScope}:retained`)).toBeDefined();
});
it("pr285_legacy_conflict_reimport_preserves_newer_reconciliation", async () => {
  closeStudyIntentStorage();
  const legacy = { ...legacyReview("a"), state: "conflicted", reconciliationSequence: 1, conflict: { code: "stale", message: "Changed", retryable: false } };
  localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([legacy]));
  const store = await initializeStudyIntentStorage(true);
  await store.update("review:a", current => ({ ...current, state: "pending", data: { ...legacy, state: "reconciling", reconciliationSequence: 2 } }));
  closeStudyIntentStorage();
  expect(await (await initializeStudyIntentStorage()).get("review:a")).toMatchObject({ state: "pending", data: { state: "reconciling", reconciliationSequence: 2 } });
});
it("pr285_practice_generation_uses_one_atomic_reset_boundary", async () => {
  const { startAnotherStudyPractice, retainedStudyRequest } = await import("../../app/lib/study-command-client");
  const store = await studyIntentStore();
  for (const [id, scope] of [["answer", repairScope], ["assessment", `${repairScope}:self-assess`]]) {
    const record = await store.put(repairCommand(id, scope));
    await store.update(record.key, current => ({ ...current, state: "committed", receipt: { attempt_id: "answer" } }));
  }
  const { IDBObjectStore } = await import("fake-indexeddb");
  const original = IDBObjectStore.prototype.put; let resetWrites = 0, interrupted = false;
  const fault = vi.spyOn(IDBObjectStore.prototype, "put").mockImplementation(function (this: IDBObjectStore, value, key) {
    if (this.name === "metadata" && String(value.key).startsWith("practice-reset:") && ++resetWrites === 2) throw new DOMException("interrupted reset", "AbortError");
    return original.call(this, value, key);
  });
  try { await startAnotherStudyPractice(repairScope); } catch { interrupted = true; } finally { fault.mockRestore(); }
  closeStudyIntentStorage(); await initializeStudyIntentStorage();
  const answer = await retainedStudyRequest(repairScope), assessment = await retainedStudyRequest(`${repairScope}:self-assess`);
  expect(Boolean(answer)).toBe(Boolean(assessment));
  expect(Boolean(answer)).toBe(interrupted);
  expect(await (await studyIntentStore()).list("study-command")).toHaveLength(2);
});
it("pr285_recovery_cursor_visits_record_21_after_twenty_unresolved_commands", async () => {
  const store = await studyIntentStore();
  for (let index = 1; index <= 21; index++) await store.put(repairCommand(`recover-${index}`), { started: true });
  const reads: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const id = decodeURIComponent(String(input).split("/").at(-1)!); reads.push(id);
    return Response.json(id === "recover-21" ? { state: "complete", response: { attempt_id: id } } : { state: "pending" });
  }));
  await recoverStudyRequests(); expect(reads).toHaveLength(20);
  await recoverStudyRequests();
  expect(await store.get(`${repairScope}:recover-21`)).toMatchObject({ state: "committed" });
  vi.unstubAllGlobals();
});
it("pr285_teaching_cross_tab_updates_the_application_projection", async () => {
  const { waitFor } = await import("@testing-library/react");
  const { openIntentStore } = await import("@tempo/browser-intents");
  const { API_URL } = await import("../../app/const");
  const other = await openIntentStore({ workspace: `${new URL(API_URL || "/", window.location.href).origin}:study` });
  try {
    await other.addTeachingFact({ key: "other:1:0", cardId: "other", revision: 1, ply: 0, kind: "seen" });
    await waitFor(() => expect(savedTeachingFacts()).toEqual(expect.arrayContaining([expect.objectContaining({ key: "other:1:0" })])));
  } finally { other.close(); }
});

it.each(["failed-422", "retryable-422", "conflict", "pending", "wrong-operation"])("pr285_rejected_answer_protocol_classification (%s)", async scenario => {
  const id = `protocol-${scenario}`;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    if (options?.method === "POST") {
      if (scenario === "conflict") return Response.json({ detail: "Exercise changed" }, { status: 409 });
      return Response.json({ operation_id: scenario === "wrong-operation" ? "different" : id }, { status: 202 });
    }
    return Response.json(scenario === "pending" ? { state: "pending" } : { state: "failed", error: { status_code: 422, message: "Submit one move", retryable: scenario === "retryable-422" } });
  }));
  await expect(executeStudyRequest({ scope: repairScope, endpoint: "/attempts", requestId: id, body: { attempt_id: id } })).rejects.toThrow();
  expect(await (await studyIntentStore()).get(`${repairScope}:${id}`)).toMatchObject({ state: scenario === "failed-422" ? "discarded" : "pending" });
  vi.unstubAllGlobals();
});
it("pr285_recovery_cursor_restart_wraparound_and_bound_preserve_older_unresolved_work", async () => {
  const store = await studyIntentStore();
  for (let index = 1; index <= 25; index++) await store.put(repairCommand(`wrap-${index}`), { started: true });
  const reads: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => { reads.push(decodeURIComponent(String(input).split("/").at(-1)!)); return Response.json({ state: "pending" }); }));
  await recoverStudyRequests(); expect(reads).toEqual(Array.from({ length: 20 }, (_, index) => `wrap-${index + 1}`));
  closeStudyIntentStorage(); await initializeStudyIntentStorage(); reads.length = 0;
  await recoverStudyRequests(); expect(reads).toHaveLength(20); expect(reads.slice(0, 5)).toEqual(["wrap-21", "wrap-22", "wrap-23", "wrap-24", "wrap-25"]); expect(reads[5]).toBe("wrap-1");
  expect(await (await studyIntentStore()).list(undefined, true)).toHaveLength(25); vi.unstubAllGlobals();
});
it("pr285_recovery_cursor_cancellation_retains_unvisited_commands", async () => {
  const store = await studyIntentStore();
  for (let index = 1; index <= 21; index++) await store.put(repairCommand(`cancel-${index}`), { started: true });
  const controller = new AbortController(); const reads: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => { reads.push(String(input)); controller.abort(new Error("cancel pass")); return Response.json({ state: "pending" }); }));
  await expect(recoverStudyRequests(controller.signal)).rejects.toThrow("cancel pass");
  expect(reads).toHaveLength(1); expect(await store.list(undefined, true)).toHaveLength(21);
  const resumed: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => { const id = decodeURIComponent(String(input).split("/").at(-1)!); resumed.push(id); return Response.json(id === "cancel-21" ? { state: "complete", response: { attempt_id: id } } : { state: "pending" }); }));
  await recoverStudyRequests(); expect(resumed[0]).toBe("cancel-2"); expect(resumed).toHaveLength(20);
  expect(await store.get(`${repairScope}:cancel-21`)).toMatchObject({ state: "committed" }); vi.unstubAllGlobals();
});
it("pr285_recovery_cursor_defers_self_assessment_until_parent_receipt_is_settled", async () => {
  const store = await studyIntentStore();
  await store.put(repairCommand("parent"), { started: true });
  const child = repairCommand("parent:self-assess", `${repairScope}:self-assess`); child.attemptId = "parent";
  await store.put(child, { started: true }); await store.put(repairCommand("independent"), { started: true });
  let parentComplete = false; const reads: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const id = decodeURIComponent(String(input).split("/").at(-1)!); reads.push(id);
    return Response.json(id === "parent" && !parentComplete ? { state: "pending" } : { state: "complete", response: { attempt_id: id === "parent:self-assess" ? "parent" : id } });
  }));
  await recoverStudyRequests(); expect(reads).not.toContain("parent:self-assess");
  expect(await store.get(`${repairScope}:independent`)).toMatchObject({ state: "committed" });
  parentComplete = true; await recoverStudyRequests();
  expect(await store.get(child.key)).toMatchObject({ state: "committed" }); vi.unstubAllGlobals();
});
it("pr285_teaching_cross_tab_incremental_seen_and_first_clean_do_not_enumerate_history", async () => {
  const { waitFor } = await import("@testing-library/react");
  const { openIntentStore } = await import("@tempo/browser-intents");
  const { API_URL } = await import("../../app/const");
  const store = await studyIntentStore(); const allFacts = vi.spyOn(store, "teachingFacts");
  const other = await openIntentStore({ workspace: `${new URL(API_URL || "/", window.location.href).origin}:study` });
  try {
    const fact = { key: "first-clean:other", cardId: "other", revision: 1, kind: "first-clean" as const };
    await other.addTeachingFact(fact); await other.addTeachingFact(fact);
    await waitFor(() => expect(savedTeachingFacts().filter(saved => saved.key === fact.key)).toHaveLength(1));
    await other.checkpoint("unrelated", true); await store.put(repairCommand("unrelated"));
    expect(allFacts).not.toHaveBeenCalled();
  } finally { other.close(); }
});

it("pr285_practice_generation_awaiting_self_assessment_blocks_reset_until_matching_completion", async () => {
  const { startAnotherStudyPractice } = await import("../../app/lib/study-command-client");
  const store = await studyIntentStore(); const parent = repairCommand("awaiting-rating");
  await store.put(parent); await store.update(parent.key, current => ({ ...current, state: "committed", receipt: { attempt_id: parent.attemptId, pending_self_assessment: true } }));
  await expect(startAnotherStudyPractice(repairScope)).rejects.toThrow("Confirm the saved answer");
  const assessment = { ...repairCommand("rating", `${repairScope}:self-assess`), attemptId: parent.attemptId };
  await store.put(assessment); await expect(startAnotherStudyPractice(repairScope)).rejects.toThrow("Confirm the saved answer");
  await store.update(assessment.key, current => ({ ...current, state: "committed", receipt: { attempt_id: parent.attemptId, rating: "again", pending_self_assessment: false } }));
  expect(await startAnotherStudyPractice(repairScope)).toBe(2);
});
it("pr285_rejected_answer_storage_abort_does_not_report_durable_rejection", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Submit one move" }, { status: 422 })));
  const fault = interceptIntentWrites(record => { if (record.state === "discarded") throw new DOMException("rejection quota", "QuotaExceededError"); });
  const request = { scope: repairScope, endpoint: "/attempts", requestId: "rejection-abort", body: { attempt_id: "rejection-abort", answer: { moves: [] } } };
  await expect(executeStudyRequest(request)).rejects.toThrow("rejection quota"); fault.mockRestore();
  expect(await (await studyIntentStore()).get(`${repairScope}:rejection-abort`)).toMatchObject({ state: "pending", payload: { body: request.body } });
  vi.unstubAllGlobals();
});
it("pr285_rejected_answer_failed_receipt_can_settle_under_another_tabs_lease", async () => {
  const request = { scope: repairScope, endpoint: "/attempts", requestId: "leased", body: { attempt_id: "leased" } };
  vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("response lost"); })); await expect(executeStudyRequest(request)).rejects.toThrow("response lost");
  const store = await studyIntentStore(); await store.claim(`${repairScope}:leased`, "other-tab", Date.now(), 60_000);
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, options?: RequestInit) => {
    expect(options?.method).not.toBe("POST"); return Response.json({ state: "failed", error: { status_code: 422, message: "Submit one move" } });
  }));
  await recoverStudyRequests(); expect(await store.get(`${repairScope}:leased`)).toMatchObject({ state: "discarded", receipt: { type: "rejected", requestId: "leased" } });
  vi.unstubAllGlobals();
});

it("pr285_storage_retry_live_database_connection_survives_hidden_factory_but_closed_store_cannot", async () => {
  const store = await studyIntentStore(); const factory = indexedDB;
  vi.stubGlobal("indexedDB", undefined);
  try {
    const current = await studyIntentStore(); expect(current).toBe(store);
    await current.put(repairCommand("live-database"));
    closeStudyIntentStorage(); await expect(initializeStudyIntentStorage()).rejects.toThrow("unavailable");
  } finally { vi.stubGlobal("indexedDB", factory); }
  expect(await (await initializeStudyIntentStorage()).get(`${repairScope}:live-database`)).toBeDefined();
  vi.unstubAllGlobals();
});

it.each([
  { queueEntryId: -1 }, { queueEntryId: 0 }, { expectedRevision: 0 }, { openingEvidenceCompletion: { bad: "shape" } },
  { state: "conflicted", conflict: { bad: "shape" } }, { preparedNext: { bad: "shape" } }, { reconciliationSequence: 0 },
])("pr285_legacy_conflict_malformed_review_stays_blocked_and_retained (%j)", async invalid => {
  const store = await studyIntentStore(); await store.put(repairCommand("unrelated")); closeStudyIntentStorage();
  const source = JSON.stringify([{ ...legacyReview("a"), ...invalid }]); localStorage.setItem("tempo-pending-training-reviews-v1", source);
  await expect(initializeStudyIntentStorage(true)).rejects.toThrow("invalid");
  expect(localStorage.getItem("tempo-pending-training-reviews-v1")).toBe(source);
  localStorage.setItem("tempo-pending-training-reviews-v1", "[]");
  expect(await (await initializeStudyIntentStorage(true)).get(`${repairScope}:unrelated`)).toBeDefined();
});

it.each([0, -1, { queueEntryId: 1, operationId: "" }, { queueEntryId: 1, operationId: "x".repeat(129) },
  { queueEntryId: 1, operationId: "original", backendId: "" }, { queueEntryId: 1, operationId: "original", expectedRevision: 0 }])(
  "pr285_legacy_conflict_malformed_failure_stays_blocked_and_retained (%j)", async invalid => {
    closeStudyIntentStorage(); const source = JSON.stringify([invalid]); localStorage.setItem("tempo-pending-training-failures-v1", source);
    await expect(initializeStudyIntentStorage(true)).rejects.toThrow("invalid"); expect(localStorage.getItem("tempo-pending-training-failures-v1")).toBe(source);
  });

it("pr285_rejected_answer_failed_receipt_preserves_structured_validation_detail_after_reload", async () => {
  const detail = [{ loc: ["answer", "moves"], msg: "Submit one move", type: "invalid_submission" }];
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "POST"
    ? Response.json({ operation_id: "detailed-rejection" }, { status: 202 })
    : Response.json({ state: "failed", error: { status_code: 422, code: "invalid_answer", message: "Submit one move", detail, retryable: false } })));
  await expect(executeStudyRequest({ scope: repairScope, endpoint: "/attempts", requestId: "detailed-rejection", body: { attempt_id: "detailed-rejection" } })).rejects.toThrow("Submit one move");
  closeStudyIntentStorage(); await initializeStudyIntentStorage();
  expect(await (await studyIntentStore()).get(`${repairScope}:detailed-rejection`)).toMatchObject({ state: "discarded", receipt: { details: { detail, code: "invalid_answer", retryable: false } } });
  vi.unstubAllGlobals();
});
