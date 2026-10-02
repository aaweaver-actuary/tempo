import { afterEach, expect, it, vi } from "vitest";
import { DiscoveryPreviewScheduler, DiscoveryPreviewValidationCache, type DiscoveryPreviewWork } from "../../app/lib/discovery-preview-scheduler";

afterEach(() => vi.useRealTimers());
function work(index: number, priority: DiscoveryPreviewWork["priority"] = "background", generation = 1): DiscoveryPreviewWork {
  return { discoveryId: String(index), identity: `${index}:fingerprint:${generation}`, priority, feedOrder: index };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(finish => { resolve = finish; });
  return { resolve, promise };
}
async function settle() { for (let index = 0; index < 15; index++) await Promise.resolve(); }

it("discovery_explicit_demand_precedes_queued_speculation", async () => {
  const requests: Array<{ work: DiscoveryPreviewWork; response: ReturnType<typeof deferred<"complete" | "retry">> }> = [];
  const scheduler = new DiscoveryPreviewScheduler(async next => {
    const response = deferred<"complete" | "retry">(); requests.push({ work: next, response }); return response.promise;
  });
  const items = Array.from({ length: 100 }, (_, index) => work(index));
  scheduler.update(items.map(item => item.identity), items); await settle();
  scheduler.update(items.map(item => item.identity), items.map(item => item.discoveryId === "99" ? { ...item, priority: "explicit" } : item));
  requests[0].response.resolve("complete"); await settle();
  expect(requests.map(request => request.work.discoveryId)).toEqual(["0", "1", "99"]);
  expect(scheduler.snapshot()).toMatchObject({ maximumActive: 2, active: 2, queued: 97 });
  scheduler.dispose();
});

it("discovery_due_retries_do_not_duplicate_queue_entries", async () => {
  vi.useFakeTimers();
  const requests: Array<ReturnType<typeof deferred<"complete" | "retry">>> = [];
  const scheduler = new DiscoveryPreviewScheduler(async () => {
    const response = deferred<"complete" | "retry">(); requests.push(response); return response.promise;
  }, 2, () => Date.now());
  const items = Array.from({ length: 100 }, (_, index) => work(index));
  const identities = items.map(item => item.identity);
  scheduler.update(identities, items); await settle();
  // Complete the first attempt for every item as waiting, before retry time.
  for (let index = 0; index < 100; index += 2) {
    requests[index].resolve("retry"); requests[index + 1].resolve("retry"); await settle();
  }
  expect(scheduler.snapshot()).toMatchObject({ active: 0, queued: 100, retriesScheduled: 100, maximumActive: 2 });
  expect(vi.getTimerCount()).toBe(1);
  await vi.advanceTimersByTimeAsync(30_000);
  for (let wake = 0; wake < 20; wake++) scheduler.update(identities, items);
  await settle();
  expect(requests).toHaveLength(102);
  expect(scheduler.snapshot()).toMatchObject({ active: 2, queued: 98, maximumActive: 2 });
  scheduler.update(identities, []);
  requests[100].resolve("retry"); requests[101].resolve("retry"); await settle();
  expect(vi.getTimerCount()).toBe(0);
  scheduler.dispose();
});

it("discovery_fingerprint_replacement_fences_late_results", async () => {
  const requests: Array<{ work: DiscoveryPreviewWork; current: () => boolean; response: ReturnType<typeof deferred<"complete">> }> = [];
  const scheduler = new DiscoveryPreviewScheduler(async (next, context) => {
    const response = deferred<"complete">(); requests.push({ work: next, current: context.isCurrent, response }); return response.promise;
  });
  const old = [work(0), work(1), work(2)];
  scheduler.update(old.map(item => item.identity), old); await settle();
  const replacement = work(0, "explicit", 2);
  scheduler.update([replacement.identity], [replacement]);
  expect(requests[0].current()).toBe(false);
  expect(scheduler.snapshot()).toMatchObject({ staleQueuedDiscarded: 1, active: 2, queued: 1 });
  requests[0].response.resolve("complete"); await settle();
  expect(requests[2].work).toEqual(replacement);
  expect(scheduler.snapshot()).toMatchObject({ staleResultsIgnored: 1, maximumActive: 2 });
  scheduler.dispose();
});

it("discovery_removal_and_return_uses_new_generation", async () => {
  const responses: Array<ReturnType<typeof deferred<"complete">>> = [];
  const current: Array<() => boolean> = [];
  const scheduler = new DiscoveryPreviewScheduler(async (_next, context) => {
    current.push(context.isCurrent); const response = deferred<"complete">(); responses.push(response); return response.promise;
  });
  scheduler.update([work(0).identity], [work(0)]); await settle();
  scheduler.update([], []);
  const returned = work(0, "explicit", 3);
  scheduler.update([returned.identity], [returned]); await settle();
  expect(responses).toHaveLength(2);
  expect(current.map(isCurrent => isCurrent())).toEqual([false, true]);
  responses[0].resolve("complete"); await settle();
  expect(scheduler.snapshot().staleResultsIgnored).toBe(1);
  scheduler.dispose();
});

it("discovery_unmount_disposes_work_and_fences_remount", async () => {
  vi.useFakeTimers();
  const response = deferred<"retry">(); let signal!: AbortSignal, current!: () => boolean;
  const scheduler = new DiscoveryPreviewScheduler(async (_next, context) => {
    signal = context.signal; current = context.isCurrent; return response.promise;
  });
  scheduler.update([work(0).identity], [work(0)]); await settle();
  scheduler.dispose();
  expect(signal.aborted).toBe(true); expect(current()).toBe(false);
  response.resolve("retry"); await settle();
  expect(vi.getTimerCount()).toBe(0);
  expect(scheduler.snapshot()).toMatchObject({ active: 0, queued: 0, retriesScheduled: 0, staleResultsIgnored: 1 });
  const remounted = new DiscoveryPreviewScheduler(async () => "complete");
  remounted.update([work(0).identity], [work(0)]); await settle();
  expect(remounted.snapshot().started).toBe(1);
  remounted.dispose();
});

it("discovery_validation_cache_reuses_only_current_authoritative_identity", () => {
  const counters = vi.fn(); const validate = vi.fn(() => true);
  const cache = new DiscoveryPreviewValidationCache(counters, 2);
  const preview = {};
  cache.matches("a:fingerprint:1", "position-a", preview, validate);
  cache.matches("a:fingerprint:1", "position-a", preview, validate);
  expect(validate).toHaveBeenCalledTimes(1); expect(counters.mock.calls).toEqual([[false], [true]]);
  cache.matches("a:fingerprint:1", "position-b", preview, validate);
  cache.matches("a:fingerprint:1", "position-b", {}, validate);
  cache.matches("a:replacement:2", "position-b", preview, validate);
  expect(validate).toHaveBeenCalledTimes(4);
  cache.matches("b:fingerprint:1", "position-b", {}, validate);
  expect(cache.size).toBe(2);
  cache.retain(new Set(["b:fingerprint:1"])); expect(cache.size).toBe(1);
  cache.clear(); expect(cache.size).toBe(0);
});

it("discovery_visibility_gate_is_checked_at_capacity_release_before_react_reconciliation", async () => {
  let visible = true;
  const responses: Array<ReturnType<typeof deferred<"complete">>> = [];
  const scheduler = new DiscoveryPreviewScheduler(async () => {
    const response = deferred<"complete">(); responses.push(response); return response.promise;
  }, 2, () => performance.now(), 30_000, () => visible);
  const items = Array.from({ length: 100 }, (_, index) => work(index));
  scheduler.update(items.map(item => item.identity), items); await settle();
  visible = false;
  responses[0].resolve("complete"); responses[1].resolve("complete"); await settle();
  expect(responses).toHaveLength(2);
  expect(scheduler.snapshot()).toMatchObject({ active: 0, queued: 98 });
  visible = true; scheduler.update(items.map(item => item.identity), items); await settle();
  expect(responses).toHaveLength(4);
  expect(scheduler.snapshot().maximumActive).toBe(2);
  scheduler.dispose();
});
