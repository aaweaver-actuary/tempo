import { afterEach, beforeEach, expect, it, vi } from "vitest";
import fixture from "../fixtures/prefix-comparison/transition.json";
import structural from "../fixtures/prefix-comparison/structural.json";
import { prefixSourceSchema } from "../../app/domain/prefix-comparison";
import { prefixTransitionPlanSchema, transitionSummary, prefixTransitionApplySchema } from "../../app/domain/prefix-transition";
import { beginPrefixTransition, clearResolvedPrefixTransition, readPrefixTransition, recoverPrefixTransition } from "../../app/lib/prefix-transition-command";

const plan = prefixTransitionPlanSchema.parse(fixture.ready);
const request = prefixTransitionApplySchema.parse(fixture.request);
const summary = transitionSummary(plan, prefixSourceSchema.parse(structural.source), "Combined Black");
const key = "tempo-pending-prefix-transition-v1:rep";
beforeEach(() => localStorage.clear());
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
const begin = () => beginPrefixTransition("rep", request, summary);
function complete(operationId: string, changes: Record<string, unknown> = {}) {
  return Response.json({ operation_id: operationId, state: "complete",
    transition: { state: "complete", graph_generation: 2, queue_date: fixture.result.queue_date, recovery: null },
    response: { ...fixture.result, operation_id: operationId, ...changes } });
}
const recover = (controller = new AbortController(), retryBlocked = false) => recoverPrefixTransition("rep", { signal: controller.signal, retryBlocked });

it.each(["missing", "identity", "plan", "repertoire", "generation", "queue"] as const)(
  "issue81_unverifiable_published_journal_recovers_authoritative_operation_%s", async mismatch => {
    const original = begin();
    const result = { ...fixture.result, operation_id: original.operationId };
    if (mismatch === "identity") result.operation_id = "different-operation";
    if (mismatch === "plan") result.plan_id = "different-plan";
    if (mismatch === "repertoire") result.repertoire_id = "different-repertoire";
    if (mismatch === "generation") result.graph_generation = request.graph_generation;
    const cached = { ...original, state: "published", transitionPhase: "complete",
      result: mismatch === "missing" ? undefined : mismatch === "queue" ? { ...result, queue_date: undefined } : result };
    const stored = JSON.stringify(cached); localStorage.setItem(key, stored);
    expect(readPrefixTransition("rep")?.state).toBe("transport_unknown");
    expect(localStorage.getItem(key)).toBe(stored);
    const fetcher = vi.fn(async (url: string | URL | Request) => {
      expect(String(url)).toContain(`/operations/${original.operationId}`);
      return complete(original.operationId);
    }); vi.stubGlobal("fetch", fetcher);
    expect((await recover())?.state).toBe("published");
    expect(readPrefixTransition("rep")?.operationId).toBe(original.operationId);
    expect(fetcher).toHaveBeenCalledOnce();
  });

it("issue81_double_click_creates_one_operation", () => {
  const original = begin();
  expect(begin().operationId).toBe(original.operationId);
  expect(() => beginPrefixTransition("rep", { ...request, plan_id: "different" }, summary)).toThrow(/Resolve the saved/);
  expect(readPrefixTransition("rep")?.request).toEqual(request);
});
it("issue81_overlapping_repertoire_transition_remains_locked", () => {
  beginPrefixTransition("rep", request, { ...summary, affectedRepertoireIds: ["rep", "shared"] });
  expect(() => beginPrefixTransition("shared", request, summary)).toThrow(/Resolve the prefix transition affecting/);
  expect(readPrefixTransition("shared")).toBeNull();
  expect(() => beginPrefixTransition("other", request, { ...summary, affectedRepertoireIds: ["other", "shared"] }))
    .toThrow(/Resolve the prefix transition affecting/);
  expect(readPrefixTransition("other")).toBeNull();
});

it("issue81_lost_response_reload_recovers_original_operation", async () => {
  const original = begin(); let admitted = false;
  const fetcher = vi.fn(async (_url, options?: RequestInit) => {
    if (options?.method === "POST") { admitted = true; throw new TypeError("Lost response"); }
    return admitted ? complete(original.operationId) : Response.json({ operation_id: original.operationId, state: "unknown" });
  });
  vi.stubGlobal("fetch", fetcher);
  expect((await recover())?.state).toBe("published");
  const [[, delivery]] = fetcher.mock.calls.filter(([, options]) => options?.method === "POST");
  expect(new Headers(delivery?.headers).get("Idempotency-Key")).toBe(original.operationId);
  expect(JSON.parse(String(delivery?.body))).toEqual(request);
  expect((await recover())?.operationId).toBe(original.operationId);
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
});

it("issue81_confirm_waits_for_published_completion", async () => {
  vi.useFakeTimers(); const original = begin(); let published = false; let admitted = false;
  vi.stubGlobal("fetch", vi.fn(async (_url, options?: RequestInit) => {
    if (options?.method === "POST") { admitted = true; return Response.json(fixture.result); }
    return published ? complete(original.operationId) : Response.json({ operation_id: original.operationId, state: admitted ? "pending" : "unknown" });
  }));
  const resolving = recover();
  await vi.waitFor(() => expect(readPrefixTransition("rep")?.state).toBe("pending"));
  expect(readPrefixTransition("rep")?.result).toBeUndefined();
  published = true; await vi.advanceTimersByTimeAsync(1000);
  expect((await resolving)?.state).toBe("published");
});

it("issue81_pending_survives_remount", async () => {
  const original = begin(); const controller = new AbortController();
  const fetcher = vi.fn(async () => Response.json({ operation_id: original.operationId, state: "pending" }));
  vi.stubGlobal("fetch", fetcher);
  const resolving = recover(controller);
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  controller.abort(); await expect(resolving).rejects.toThrow();
  expect(readPrefixTransition("rep")?.operationId).toBe(original.operationId);
  expect(readPrefixTransition("rep")?.summary).toEqual(summary);
});

it("issue81_offline_status_failure_retains_identity_without_blind_replay", async () => {
  const original = begin(); const fetcher = vi.fn().mockRejectedValue(new TypeError("Offline")); vi.stubGlobal("fetch", fetcher);
  expect((await recover())?.state).toBe("transport_unknown");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(readPrefixTransition("rep")?.operationId).toBe(original.operationId);
  clearResolvedPrefixTransition("rep", original.operationId);
  expect(readPrefixTransition("rep")).not.toBeNull();
});

it("issue81_unknown_admission_replays_once_with_original_payload", async () => {
  const original = begin(); const fetcher = vi.fn(async () => Response.json({ operation_id: original.operationId, state: "unknown" }));
  vi.stubGlobal("fetch", fetcher);
  expect((await recover())?.state).toBe("transport_unknown");
  expect(fetcher).toHaveBeenCalledTimes(3); // status, original delivery, status; no retry storm
  expect(readPrefixTransition("rep")?.request).toEqual(request);
});

it("issue81_operation_status_polling_yields_to_foreground", async () => {
  vi.useFakeTimers(); const original = begin(); let reads = 0;
  const fetcher = vi.fn(async (_url, options?: RequestInit) => {
    expect(new Headers(options?.headers).get("X-Tempo-Work-Class")).toBe("background");
    return ++reads === 1 ? Response.json({ detail: "Waiting for foreground activity" }, { status: 503, headers: { "Retry-After": "1" } }) : complete(original.operationId);
  });
  vi.stubGlobal("fetch", fetcher); const resolving = recover();
  await vi.advanceTimersByTimeAsync(1000);
  expect((await resolving)?.state).toBe("published"); expect(fetcher).toHaveBeenCalledTimes(2);
});

it.each(["plan_id", "operation_id", "repertoire_id", "graph_generation", "queue_date"])("issue81_mismatched_complete_%s_retains_recovery_identity", async field => {
  const original = begin(); vi.stubGlobal("fetch", vi.fn(async () => complete(original.operationId, { [field]: field === "graph_generation" ? 1 : "wrong" })));
  expect((await recover())?.state).toBe("transport_unknown");
  expect(readPrefixTransition("rep")?.result).toBeUndefined();
  expect(readPrefixTransition("rep")?.operationId).toBe(original.operationId);
});
it.each(["invalid JSON", "unsupported state", "invalid completion"])("issue81_malformed_%s_retains_recovery_identity", async kind => {
  const original = begin();
  vi.stubGlobal("fetch", vi.fn(async () => kind === "invalid JSON" ? new Response("Unreadable receipt")
    : Response.json({ operation_id: original.operationId, state: kind === "unsupported state" ? "new-state" : "complete", response: {} })));
  expect((await recover())?.state).toBe("transport_unknown");
  expect(readPrefixTransition("rep")?.operationId).toBe(original.operationId);
  expect(readPrefixTransition("rep")?.message).toMatch(/Check status again|check status again/);
  expect(readPrefixTransition("rep")?.result).toBeUndefined();
});

it("issue81_terminal_failure_offers_actionable_recovery", async () => {
  const original = begin(); vi.stubGlobal("fetch", vi.fn(async () => Response.json({ operation_id: original.operationId, state: "failed",
    error: { message: "Rejected", detail: { code: "stale_plan", message: "Review a fresh plan." } } })));
  expect((await recover())?.state).toBe("stale");
  expect(readPrefixTransition("rep")?.message).toBe("Review a fresh plan.");
  clearResolvedPrefixTransition("rep", original.operationId); expect(readPrefixTransition("rep")).toBeNull();
});

it("issue81_blocked_publication_retries_the_existing_operation", async () => {
  const original = begin(); localStorage.setItem(key, JSON.stringify({ ...original, state: "blocked", transitionPhase: "recovery_required" }));
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(complete(original.operationId)); vi.stubGlobal("fetch", fetcher);
  expect((await recover(new AbortController(), true))?.state).toBe("published");
  expect(fetcher.mock.calls[0][0]).toContain(`/operations/${original.operationId}`);
  expect(fetcher.mock.calls[0][1]?.method).not.toBe("POST");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/apply"))).toBe(false);
});
it("issue81_blocked_nested_error_code_survives_recovery", async () => {
  const original = begin();
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ operation_id: original.operationId, state: "blocked",
    last_error: { detail: { code: "publication_unavailable", message: "Restore the queue publisher and retry this operation." } } })));
  expect((await recover())?.failureCode).toBe("publication_unavailable");
  expect(readPrefixTransition("rep")?.message).toContain("Restore the queue publisher");
});

it("issue81_storage_quota_failure_prevents_delivery", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Quota", "QuotaExceededError"); });
  expect(begin).toThrow(/Quota/); expect(localStorage.getItem(key)).toBeNull();
});
it("issue81_unreadable_storage_prevents_new_identity", () => {
  localStorage.setItem(key, "broken"); expect(begin).toThrow(/unreadable/); expect(localStorage.getItem(key)).toBe("broken");
});
it("issue81_late_completion_cannot_erase_newer_operation", async () => {
  const original = begin(); let resolve!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(accept => { resolve = accept; })));
  const resolving = recover();
  localStorage.setItem(key, JSON.stringify({ ...original, operationId: "newer" }));
  resolve(complete(original.operationId)); await expect(resolving).rejects.toThrow();
  expect(() => readPrefixTransition("rep")).toThrow(/unreadable/);
  expect(JSON.parse(localStorage.getItem(key)!).operationId).toBe("newer");
});
it("issue81_changed_payload_cannot_be_overwritten_or_delivered", async () => {
  const original = begin(); let resolve!: (response: Response) => void;
  const fetcher = vi.fn(() => new Promise<Response>(accept => { resolve = accept; })); vi.stubGlobal("fetch", fetcher);
  const resolving = recover();
  localStorage.setItem(key, JSON.stringify({ ...original, request: { ...original.request, plan_id: "changed" } }));
  resolve(Response.json({ operation_id: original.operationId, state: "unknown" }));
  await expect(resolving).rejects.toThrow(/unreadable/);
  expect(fetcher).toHaveBeenCalledOnce(); expect(() => readPrefixTransition("rep")).toThrow(/unreadable/);
  expect(JSON.parse(localStorage.getItem(key)!).request.plan_id).toBe("changed");
});
