import { afterEach, beforeEach, expect, it, vi } from "vitest";
import transition from "../fixtures/prefix-comparison/transition.json";
import structural from "../fixtures/prefix-comparison/structural.json";
import { prefixSourceSchema } from "../../app/domain/prefix-comparison";
import { prefixTransitionApplySchema, prefixTransitionPlanSchema, transitionRequest, transitionSummary } from "../../app/domain/prefix-transition";
import { beginPrefixTransition, clearResolvedPrefixTransition, readPrefixTransition, recoverPrefixTransition, reservePrefixTransition,
  knownPrefixTransitions, prefixTransitionRecoveryIssue, readPrefixStructuralAdmission, admitPrefixStructuralMutation,
  withPrefixTransitionJournalLock, resetPrefixTransitionSessionForTests, type PrefixTransitionOperation } from "../../app/lib/prefix-transition-command";
import { installPrefixTransitionTestLocks } from "./support/prefix-transition-locks";

const request = prefixTransitionApplySchema.parse(transition.request);
const summary = transitionSummary(prefixTransitionPlanSchema.parse(transition.ready), prefixSourceSchema.parse(structural.source), "Combined Black");
const key = "tempo-pending-prefix-transition-v1:rep";
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, refuse) => { resolve = accept; reject = refuse; });
  return { promise, resolve, reject };
}
function completion(operationId: string) {
  return Response.json({ operation_id: operationId, state: "complete",
    transition: { state: "complete", graph_generation: 2, queue_date: transition.result.queue_date, recovery: null },
    response: { ...transition.result, operation_id: operationId } });
}
const begin = () => beginPrefixTransition("rep", request, summary);
const recovering = (signal = new AbortController().signal) => recoverPrefixTransition("rep", { signal });
function assertFrozen(operation: PrefixTransitionOperation | null, operationId: string) {
  expect(operation?.operationId).toBe(operationId); expect(operation?.repertoireId).toBe("rep");
  expect(operation?.request).toEqual(request); expect(operation?.summary).toEqual(summary);
}
beforeEach(() => { installPrefixTransitionTestLocks(); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each(["pending", "unknown", "blocked", "failed", "transport_error", "abort"])(
  "audit187_published_journal_cannot_regress_after_older_%s", async olderState => {
    const original = await begin(); const firstResponse = deferred<Response>(); const secondResponse = deferred<Response>();
    let reads = 0; const bothReading = deferred<void>();
    const fetcher = vi.fn((_url: unknown, options?: RequestInit) => {
      expect(options?.method).not.toBe("POST");
      ++reads; if (reads === 2) bothReading.resolve();
      return reads === 1 ? firstResponse.promise : secondResponse.promise;
    }); vi.stubGlobal("fetch", fetcher);
    const firstController = new AbortController(); const firstRecovery = recovering(firstController.signal).catch(failure => failure);
    const secondRecovery = recovering(); await bothReading.promise;
    secondResponse.resolve(completion(original.operationId)); expect((await secondRecovery)?.state).toBe("published");
    if (olderState === "transport_error") firstResponse.reject(new TypeError("Older transport failed"));
    else {
      if (olderState === "abort") firstController.abort();
      firstResponse.resolve(Response.json({ operation_id: original.operationId, state: olderState === "abort" ? "pending" : olderState,
        error: olderState === "failed" ? { code: "staging_failed" } : undefined }));
    }
    const resolved = await firstRecovery;
    if (olderState === "abort") expect(resolved).toBeInstanceOf(Error); else expect(resolved.state).toBe("published");
    const current = readPrefixTransition("rep"); assertFrozen(current, original.operationId);
    expect(current?.state).toBe("published"); expect(current?.result?.operation_id).toBe(original.operationId);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

it("audit187_web_lock_reservations_share_one_operation", async () => {
  const lockRequests = installPrefixTransitionTestLocks();
  const operations = await Promise.all([begin(), reservePrefixTransition("rep", request, summary, new AbortController().signal)]);
  expect(new Set(operations.map(operation => operation.operationId)).size).toBe(1);
  expect(new Set(lockRequests.mock.calls.map(([name]) => name))).toEqual(new Set(["tempo-pending-prefix-transition-v1:reservation"]));
});
it("audit187_no_locks_interleaved_reservations_must_not_create_two_identities", async () => {
  vi.stubGlobal("navigator", {}); const allocate = vi.spyOn(crypto, "randomUUID"); const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const outcomes = await Promise.allSettled([begin(), begin()]);
  for (const outcome of outcomes) { expect(outcome.status).toBe("rejected"); if (outcome.status === "rejected") expect(outcome.reason.message).toMatch(/Web Locks.*HTTPS or localhost/); }
  expect(allocate).not.toHaveBeenCalled(); expect(fetcher).not.toHaveBeenCalled(); expect(localStorage.getItem(key)).toBeNull();
});
it("audit187_no_locks_status_only_recovery_keeps_original_identity", async () => {
  const original = await begin(); vi.stubGlobal("navigator", {}); const fetcher = vi.fn().mockResolvedValue(completion(original.operationId)); vi.stubGlobal("fetch", fetcher);
  expect((await recovering())?.state).toBe("published");
  expect(JSON.parse(localStorage.getItem(key)!).state).toBe("pending");
  expect(prefixTransitionRecoveryIssue("rep")?.code).toBe("serialization_unavailable");
  expect(fetcher.mock.calls.every(([, options]) => options?.method !== "POST")).toBe(true);
  await expect(clearResolvedPrefixTransition("rep", original.operationId)).rejects.toThrow(/Web Locks/);
});
it("audit187_unusable_locks_reject_before_identity_or_delivery", async () => {
  vi.stubGlobal("navigator", { locks: { request: vi.fn().mockRejectedValue(new DOMException("Insecure context", "SecurityError")) } });
  const allocate = vi.spyOn(crypto, "randomUUID"); const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  await expect(begin()).rejects.toThrow(/Web Locks.*HTTPS or localhost/);
  expect(allocate).not.toHaveBeenCalled(); expect(fetcher).not.toHaveBeenCalled(); expect(localStorage.getItem(key)).toBeNull();
});
it("audit187_unusable_locks_allow_status_only_recovery_of_known_identity", async () => {
  const original = await begin();
  vi.stubGlobal("navigator", { locks: { request: vi.fn().mockRejectedValue(new DOMException("Insecure context", "SecurityError")) } });
  const fetcher = vi.fn().mockResolvedValue(completion(original.operationId)); vi.stubGlobal("fetch", fetcher);
  expect((await recovering())?.state).toBe("published");
  expect(JSON.parse(localStorage.getItem(key)!).state).toBe("pending");
  expect(prefixTransitionRecoveryIssue("rep")?.code).toBe("serialization_unavailable");
  expect(fetcher).toHaveBeenCalledOnce(); expect(fetcher.mock.calls[0][1]?.method).not.toBe("POST");
});
it("audit187_unreadable_storage_keeps_known_status_only_recovery_without_overwrite", async () => {
  const original = await begin(); localStorage.setItem(key, "broken");
  const fetcher = vi.fn().mockResolvedValue(completion(original.operationId)); vi.stubGlobal("fetch", fetcher);
  expect((await recovering())?.state).toBe("published"); expect(localStorage.getItem(key)).toBe("broken");
  expect(knownPrefixTransitions()[0].operationId).toBe(original.operationId);
  expect(prefixTransitionRecoveryIssue("rep")?.code).toBe("storage_unavailable"); expect(fetcher).toHaveBeenCalledOnce();
  expect(readPrefixStructuralAdmission({ repertoireIds: ["rep"], scopeComplete: true }).paused).toBe(true);
});
it("audit187_unresolved_clear_rejects_before_recovery_navigation", async () => {
  const original = await begin();
  await expect(clearResolvedPrefixTransition("rep", original.operationId)).rejects.toThrow(/could not be cleared/);
  expect(readPrefixTransition("rep")?.operationId).toBe(original.operationId);
});
it("audit187_verified_publication_recovery_starts_with_get_and_never_replays", async () => {
  const original = await begin(); vi.stubGlobal("fetch", vi.fn().mockResolvedValue(completion(original.operationId))); await recovering();
  const fetcher = vi.fn().mockResolvedValue(Response.json({ operation_id: original.operationId, state: "unknown" })); vi.stubGlobal("fetch", fetcher);
  expect((await recovering())?.state).toBe("published"); expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][1]?.method).not.toBe("POST");
});

it.each([false, true])("audit187_clear_between_storage_check_and_write_cannot_resurrect_old_operation_newer_%s", async createNewer => {
  const original = await begin(); const stale = deferred<Response>(); const reading = deferred<void>(); let reads = 0;
  vi.stubGlobal("fetch", vi.fn(() => { if (++reads === 1) { reading.resolve(); return stale.promise; } return Promise.resolve(completion(original.operationId)); }));
  const staleOutcome = recovering().catch(failure => failure); await reading.promise;
  expect((await recovering())?.state).toBe("published"); await clearResolvedPrefixTransition("rep", original.operationId);
  const newer = createNewer ? await beginPrefixTransition("rep", { ...request, plan_id: "fresh-approved-plan" }, summary) : null;
  stale.resolve(Response.json({ operation_id: original.operationId, state: "blocked" })); expect(await staleOutcome).toBeInstanceOf(Error);
  if (newer) { expect(readPrefixTransition("rep")?.operationId).toBe(newer.operationId); expect(readPrefixTransition("rep")?.request.plan_id).toBe("fresh-approved-plan"); }
  else expect(localStorage.getItem(key)).toBeNull();
});
it("audit187_clear_queues_behind_inflight_publication_write", async () => {
  const original = await begin(); let clearing: Promise<void> | undefined;
  const nativeSetItem = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, storageKey, storedValue) {
    clearing = clearResolvedPrefixTransition("rep", original.operationId);
    expect(JSON.parse(localStorage.getItem(key)!).state).toBe("pending");
    nativeSetItem.call(this, storageKey, storedValue);
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(completion(original.operationId)));
  expect((await recovering())?.state).toBe("published"); await clearing; expect(localStorage.getItem(key)).toBeNull();
});
it("audit187_two_unknown_recovery_loops_preserve_identity_with_repeated_delivery", async () => {
  const original = await begin(); const fetcher = vi.fn(async (_url: unknown, options?: RequestInit) =>
    options?.method === "POST" ? Response.json({}, { status: 202 }) : Response.json({ operation_id: original.operationId, state: "unknown" }));
  vi.stubGlobal("fetch", fetcher); await Promise.all([recovering(), recovering()]);
  const deliveries = fetcher.mock.calls.filter(([, options]) => options?.method === "POST"); expect(deliveries).toHaveLength(2);
  for (const [, delivery] of deliveries) { expect(new Headers(delivery?.headers).get("Idempotency-Key")).toBe(original.operationId); expect(JSON.parse(String(delivery?.body))).toEqual(request); }
  assertFrozen(readPrefixTransition("rep"), original.operationId);
});
it("audit187_immutable_scope_cannot_narrow_known_unresolved_ownership", async () => {
  const original = await beginPrefixTransition("rep", request, { ...summary, affectedRepertoireIds: ["rep", "shared"] });
  localStorage.setItem(key, JSON.stringify({ ...original, summary }));
  expect(() => readPrefixTransition("rep")).toThrow(/unreadable/);
  expect(readPrefixStructuralAdmission({ repertoireIds: ["shared"], scopeComplete: true }).paused).toBe(true);
  expect(knownPrefixTransitions()[0].summary.affectedRepertoireIds).toContain("shared");
});
it("audit187_reservation_freezes_caller_owned_request_and_scope", async () => {
  const editableRequest = structuredClone(request); const editableSummary = structuredClone(summary);
  const original = await beginPrefixTransition("rep", editableRequest, editableSummary);
  editableRequest.plan_id = "changed"; editableSummary.affectedRepertoireIds.length = 0; original.request.plan_id = "also-changed";
  expect(readPrefixTransition("rep")?.request).toEqual(request); expect(readPrefixTransition("rep")?.summary).toEqual(summary);
});
it("audit187_known_journal_disappearance_keeps_scope_and_status_only_recovery", async () => {
  const original = await begin(); localStorage.removeItem(key); const allocate = vi.spyOn(crypto, "randomUUID");
  expect(readPrefixTransition("rep")?.journalMissing).toBe(true);
  expect(readPrefixStructuralAdmission({ repertoireIds: ["rep"], scopeComplete: true }).paused).toBe(true);
  expect((await begin()).operationId).toBe(original.operationId); expect(allocate).not.toHaveBeenCalled();
  const fetcher = vi.fn().mockResolvedValue(Response.json({ operation_id: original.operationId, state: "unknown" })); vi.stubGlobal("fetch", fetcher);
  expect((await recovering())?.operationId).toBe(original.operationId); expect(fetcher).toHaveBeenCalledOnce(); expect(localStorage.getItem(key)).toBeNull();
});
it("audit187_quota_failure_during_completion_retains_verified_memory_and_reports_failure", async () => {
  const original = await begin(); vi.stubGlobal("fetch", vi.fn().mockResolvedValue(completion(original.operationId)));
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Quota", "QuotaExceededError"); });
  await expect(recovering()).rejects.toThrow(/could not be saved.*Quota/);
  expect(JSON.parse(localStorage.getItem(key)!).state).toBe("pending");
  expect(knownPrefixTransitions()[0].state).toBe("published"); expect(knownPrefixTransitions()[0].result?.operation_id).toBe(original.operationId);
});
it("audit187_reservation_quota_failure_retains_allocated_identity_without_delivery", async () => {
  const nativeSetItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Quota", "QuotaExceededError"); });
  await expect(begin()).rejects.toThrow(/Quota/); const allocated = knownPrefixTransitions()[0]; expect(allocated.operationId).toBeTruthy();
  nativeSetItem.mockRestore(); expect((await begin()).operationId).toBe(allocated.operationId); expect(localStorage.getItem(key)).toBeNull();
});
it("audit187_structural_dispatch_releases_lock_before_network_response", async () => {
  const network = deferred<void>(); const dispatchStarted = deferred<void>();
  const admission = admitPrefixStructuralMutation({ repertoireIds: ["rep"], scopeComplete: true }, () => { dispatchStarted.resolve(); return network.promise; });
  await dispatchStarted.promise; const original = await begin(); expect(original.state).toBe("pending");
  network.resolve(); await admission;
});
it("audit187_all_journal_writes_share_structural_admission_lock", async () => {
  const lockRequests = installPrefixTransitionTestLocks(); await withPrefixTransitionJournalLock(undefined, () => undefined);
  const original = await begin(); vi.stubGlobal("fetch", vi.fn().mockResolvedValue(completion(original.operationId))); await recovering();
  await clearResolvedPrefixTransition("rep", original.operationId);
  expect(lockRequests.mock.calls).toHaveLength(4);
  expect(new Set(lockRequests.mock.calls.map(([name]) => name))).toEqual(new Set(["tempo-pending-prefix-transition-v1:reservation"]));
});
it("audit187_legacy_scope_without_planner_evidence_blocks_independent_mutation", async () => {
  const original = await begin();
  expect(readPrefixStructuralAdmission({ repertoireIds: ["independent"], scopeComplete: true }).paused).toBe(true);
  await expect(beginPrefixTransition("independent", request, { ...summary, affectedRepertoireIds: ["independent"] })).rejects.toThrow(/Resolve/);
  expect(readPrefixTransition("rep")?.operationId).toBe(original.operationId);
});
it("audit187_reviewed_planner_evidence_allows_truly_independent_scope", async () => {
  const plan = prefixTransitionPlanSchema.parse(transition.ready); const source = prefixSourceSchema.parse(structural.source);
  await beginPrefixTransition("rep", request, summary, undefined, { plan, source });
  expect(readPrefixStructuralAdmission({ repertoireIds: ["independent"], scopeComplete: true }).paused).toBe(false);
  expect(readPrefixStructuralAdmission({ repertoireIds: ["rep"], scopeComplete: true }).paused).toBe(true);
});
it("audit187_stored_planner_evidence_rejects_valid_shape_omitted_shared_scope_after_reload", async () => {
  const plan = prefixTransitionPlanSchema.parse(transition.shared); const source = prefixSourceSchema.parse(structural.source);
  const approvedSummary = transitionSummary(plan, source, "Combined Black");
  const operation = await beginPrefixTransition("rep", transitionRequest(plan, request.candidate_depths), approvedSummary, undefined, { plan, source });
  expect(approvedSummary.affectedRepertoireIds).toContain("other");
  localStorage.setItem(key, JSON.stringify({ ...operation, summary: { ...approvedSummary, affectedRepertoireIds: ["rep"] } }));
  resetPrefixTransitionSessionForTests(); // A new page loses memory; the retained approval must prove its own scope.
  expect(() => readPrefixTransition("rep")).toThrow(/unreadable/);
  expect(readPrefixStructuralAdmission({ repertoireIds: ["independent"], scopeComplete: true }).paused).toBe(true);
});
it.each(["snapshot_id", "transition_snapshot_id", "graph_generation", "plan_id", "candidate_depths", "affected_scope"])(
  "audit187_reviewed_planner_evidence_rejects_mismatched_%s_before_identity", async changed => {
    const plan = prefixTransitionPlanSchema.parse(transition.ready); const source = prefixSourceSchema.parse(structural.source);
    const changedRequest = { ...request, ...(changed === "candidate_depths" ? { candidate_depths: { a: 1, b: 2 } }
      : changed === "affected_scope" ? {} : { [changed]: changed === "graph_generation" ? 3 : "different-approved-identity" }) };
    const changedSummary = changed === "affected_scope" ? { ...summary, affectedRepertoireIds: ["rep", "unreviewed"] } : summary;
    const allocate = vi.spyOn(crypto, "randomUUID");
    await expect(beginPrefixTransition("rep", changedRequest, changedSummary, undefined, { plan, source })).rejects.toThrow(/reviewed|evidence/);
    expect(allocate).not.toHaveBeenCalled(); expect(localStorage.getItem(key)).toBeNull();
  });

it("audit187_verified_publication_survives_unexpected_journal_deletion", async () => {
  const original = await begin(); vi.stubGlobal("fetch", vi.fn().mockResolvedValue(completion(original.operationId))); await recovering();
  localStorage.removeItem(key);
  const retained = readPrefixTransition("rep"); assertFrozen(retained, original.operationId);
  expect(retained?.state).toBe("published"); expect(retained?.result?.operation_id).toBe(original.operationId);
  const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 404 })); vi.stubGlobal("fetch", fetcher);
  expect((await recovering())?.state).toBe("published"); expect(localStorage.getItem(key)).toBeNull();
  expect(fetcher).toHaveBeenCalledOnce();expect(fetcher.mock.calls[0][1]?.method).not.toBe("POST");
  await clearResolvedPrefixTransition("rep", original.operationId); expect(readPrefixTransition("rep")).toBeNull();
});

it("audit187_deadline_feedback_preserves_latest_authoritative_blocked_state", async () => {
  vi.useFakeTimers(); const deadlines: AbortController[] = [];
  vi.spyOn(AbortSignal, "timeout").mockImplementation(milliseconds => {
    expect(milliseconds).toBe(30_000); const controller = new AbortController(); deadlines.push(controller);
    setTimeout(() => controller.abort(new DOMException("Checking deadline", "TimeoutError")), milliseconds); return controller.signal;
  });
  const original = await begin(); let statusReads = 0;
  vi.stubGlobal("fetch", vi.fn((_url, options?: RequestInit) => {
    ++statusReads;
    if (statusReads === 2) return new Promise<Response>((_resolve, reject) => options?.signal?.addEventListener("abort", () => reject(options.signal!.reason), { once: true }));
    return Promise.resolve(Response.json({ operation_id: original.operationId, state: statusReads === 1 ? "pending" : "blocked" }));
  }));
  const checking = recovering(); await vi.advanceTimersByTimeAsync(1000); expect(statusReads).toBe(2);
  expect((await recovering())?.state).toBe("blocked"); await vi.advanceTimersByTimeAsync(29_000);
  const timedOut = await checking; expect(timedOut?.state).toBe("blocked"); expect(timedOut?.message).toMatch(/timed out.*Backend work may still be running/);
  expect(readPrefixTransition("rep")?.state).toBe("blocked"); assertFrozen(timedOut, original.operationId);
  deadlines.forEach(controller => controller.abort());
});

it("audit187_lock_release_waits_for_storage_task_commit", async () => {
  let committedIdentity: string | null = null;
  const storageCommit = new MessageChannel();
  storageCommit.port1.onmessage = () => { committedIdentity = "original"; storageCommit.port1.close(); storageCommit.port2.close(); };
  const writing = withPrefixTransitionJournalLock(undefined, () => { storageCommit.port2.postMessage(null); });
  const reading = withPrefixTransitionJournalLock(undefined, () => committedIdentity);
  await writing; expect(await reading).toBe("original");
});
