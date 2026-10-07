import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { evidenceCompletion, openingEvidenceStorage } from "../fixtures/opening-evidence-storage";

const browserStorage = localStorage;
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); browserStorage.clear(); });

async function recoveryHarness(ids = ["A"], fallback = false) {
  const storage = await openingEvidenceStorage();
  const seedPending = (id: string) => {
    const checkpoint = evidenceCompletion(id);
    checkpoint.terminal!.state = "partial";
    storage.seed(checkpoint);
    storage.stores.opening_attempts.set(id, { ...(storage.stores.opening_attempts.get(id) as object),
      delivery_state: "pending", delivery: { checkpoint, operationKey: `opening-checkpoint:${id}` } });
  };
  ids.forEach(seedPending);
  const originals = structuredClone([...storage.stores.opening_attempts]);
  const events = structuredClone([...storage.stores.opening_events]);
  const requests: { url: string; init?: RequestInit; at: number }[] = [];
  const submittedAttempts = new Map<string, string>();
  let leaseReleased!: () => void;
  const response = { mode: "blocked" as "blocked" | "network" | "pending" | "complete" };
  const responseModes = new Map<string, typeof response.mode>();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init, at: Date.now() });
    const key = init?.method === "POST" ? new Headers(init.headers).get("Idempotency-Key")! : decodeURIComponent(url.split("/").at(-1)!);
    if (init?.method === "POST") submittedAttempts.set(key, JSON.parse(init.body as string).attempt_id);
    const id = submittedAttempts.get(key) ?? key.split(":").at(-1)!;
    const mode = responseModes.get(id) ?? response.mode;
    if (mode === "network") throw new TypeError("Fetch failed");
    if (init?.method === "POST" && mode !== "complete") return Response.json({ operation_id: key }, { status: 202 });
    const persisted = { persisted: true, attempt_id: id, received_sequences: [1, 2, 3], contiguous_sequence: 3 };
    return Response.json(init?.method === "POST" ? persisted : mode === "complete"
      ? { state: "complete", response: persisted } : { state: mode, last_error: { message: "Repair the local worker" } });
  }));
  const journal = await import("../../app/lib/opening-evidence-journal");
  const recovery = vi.spyOn(journal, "recoverOpeningEvidence");
  const subscribeLease = journal.subscribeOpeningEvidenceLeaseRelease;
  vi.spyOn(journal, "subscribeOpeningEvidenceLeaseRelease").mockImplementation(listener => { leaseReleased = listener; return subscribeLease(listener); });
  const { useTrainingStore } = await import("../../app/state/training-store");
  const { useOpeningEvidenceRecovery } = await import("../../app/hooks/use-opening-evidence-recovery");
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  useTrainingStore.setState({ queueReadiness: "ready" });
  const callbacks = new Map<number, IdleRequestCallback>(); let sequence = 0;
  vi.stubGlobal("requestIdleCallback", vi.fn(callback => { callbacks.set(++sequence, callback); return sequence; }));
  vi.stubGlobal("cancelIdleCallback", vi.fn(id => callbacks.delete(id)));
  const frames = new Map<number, FrameRequestCallback>();
  const messages = new Map<number, () => void>();
  const ports: { closed: boolean }[] = [];
  if (fallback) {
    vi.stubGlobal("requestIdleCallback", undefined);
    vi.stubGlobal("requestAnimationFrame", vi.fn(callback => { frames.set(++sequence, callback); return sequence; }));
    vi.stubGlobal("cancelAnimationFrame", vi.fn(id => frames.delete(id)));
    vi.stubGlobal("MessageChannel", class {
      port1 = { onmessage: null as (() => void) | null, closed: false, close: () => { this.port1.closed = true; messages.delete(this.id); } };
      port2 = { closed: false, postMessage: () => messages.set(this.id, () => this.port1.onmessage?.()), close: () => { this.port2.closed = true; } };
      id = ++sequence;
      constructor() { ports.push(this.port1, this.port2); }
    });
  }
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  function Harness({ enabled = true }: { enabled?: boolean }) {
    const ready = useTrainingStore(store => store.queueReadiness === "ready");
    const blocked = useTrainingStore(store => store.attempt.phase === "opponentReplyPending");
    useOpeningEvidenceRecovery(enabled, ready, blocked); return null;
  }
  const mounted = render(<Harness />);
  const paint = () => { expect(frames.size).toBe(1); const [id, callback] = [...frames][0]; frames.delete(id); act(() => callback(0)); };
  const idle = async (settle = true) => {
    if (fallback) {
      expect(messages.size).toBe(1); const [id, callback] = [...messages][0]; messages.delete(id);
      await act(async () => { callback(); if (settle) await recovery.mock.results.at(-1)?.value.catch(() => undefined); }); return;
    }
    expect(callbacks.size).toBe(1);
    const [id, callback] = [...callbacks][0]; callbacks.delete(id);
    await act(async () => { callback({ didTimeout: false, timeRemaining: () => 50 });
      if (settle) await recovery.mock.results.at(-1)?.value.catch(() => undefined); });
  };
  const advance = async (milliseconds: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); }); };
  const posts = () => requests.filter(request => request.init?.method === "POST");
  const unchanged = () => { expect([...storage.stores.opening_attempts]).toEqual(originals);
    expect([...storage.stores.opening_events]).toEqual(events); expect(storage.commits).toEqual([]); };
  return { ...storage, journal, recovery, response, responseModes, seedPending, requests, callbacks, mounted, Harness, idle, advance, posts, unchanged, useTrainingStore, leaseReleased: () => leaseReleased(), frames, messages, ports, paint };
}

it("pending journal backoff survives healthy journal successes and idle timer wakeups", async () => {
  const state = await recoveryHarness(["A", "B"]);
  state.response.mode = "complete"; state.responseModes.set("A", "pending");
  const startedAt = Date.now();
  const aRequests = () => state.requests.filter(request => decodeURIComponent(request.url).endsWith("opening-checkpoint:A"));
  await state.idle();
  await state.idle(); // B does not inherit A's first delay.
  expect(state.stores.opening_attempts.has("B")).toBe(false);
  expect(aRequests().map(request => request.at - startedAt)).toEqual([0]);
  let elapsed = 0;
  for (const [index, delay] of [1000, 2000, 4000, 8000, 16000, 30000, 30000].entries()) {
    if (index === 1) {
      state.seedPending("C");
      act(() => window.dispatchEvent(new Event("online")));
      await state.idle();
      expect(state.stores.opening_attempts.has("C")).toBe(false);
      expect(aRequests()).toHaveLength(index + 1);
    }
    const requestCount = state.requests.length;
    await state.advance(delay - 1);
    expect(state.callbacks.size).toBe(0); expect(state.requests).toHaveLength(requestCount);
    await state.advance(1);
    expect(state.callbacks.size).toBe(1); expect(state.requests).toHaveLength(requestCount);
    await state.idle(); elapsed += delay;
    expect(aRequests()).toHaveLength(index + 2);
    expect(aRequests().at(-1)!.at - startedAt).toBe(elapsed);
    expect(state.posts()).toEqual([]); expect(vi.getTimerCount()).toBe(1);
  }
  state.responseModes.set("A", "complete");
  await state.advance(30000); await state.idle();
  expect(state.stores.opening_attempts.size).toBe(0); expect(vi.getTimerCount()).toBe(0);
});

it("recovery wakes at the earliest independent journal retry deadline", async () => {
  const state = await recoveryHarness(["A"]); state.response.mode = "pending";
  const startedAt = Date.now();
  await state.idle(); await state.advance(250);
  state.seedPending("B"); act(() => window.dispatchEvent(new Event("online")));
  await state.idle();
  expect(state.requests.map(request => [decodeURIComponent(request.url).split(":").at(-1), request.at - startedAt])).toEqual([["A", 0], ["B", 250]]);
  await state.advance(749); expect(state.callbacks.size).toBe(0);
  await state.advance(1); expect(state.requests).toHaveLength(2); await state.idle();
  expect(state.requests.at(-1)!.at - startedAt).toBe(1000);
  expect(decodeURIComponent(state.requests.at(-1)!.url)).toContain("opening-checkpoint:A");
  await state.advance(249); expect(state.callbacks.size).toBe(0);
  await state.advance(1); expect(state.requests).toHaveLength(3); await state.idle();
  expect(state.requests.at(-1)!.at - startedAt).toBe(1250);
  expect(decodeURIComponent(state.requests.at(-1)!.url)).toContain("opening-checkpoint:B");
  await state.advance(1750); expect(state.requests).toHaveLength(4); await state.idle();
  expect(state.requests.at(-1)!.at - startedAt).toBe(3000);
  expect(decodeURIComponent(state.requests.at(-1)!.url)).toContain("opening-checkpoint:A");
  expect(vi.getTimerCount()).toBe(1);
});

it.each(["pending", "blocked"] as const)("externally completed opening journal prunes stale recovery guards (%s)", async mode => {
  for (const completion of ["deleted", "acknowledged"] as const) {
    const state = await recoveryHarness(); state.response.mode = mode;
    const { notifications, publishNotification } = await import("../../app/lib/notifications");
    publishNotification({ severity: "warning", source: "opening evidence", key: "opening-evidence-delivery", message: "Delivery remains saved" });
    await state.idle();
    expect(notifications().filter(record => ["opening-evidence-recovery", "opening-evidence-delivery"].includes(record.key ?? "")).every(record => record.resolvedAt === null)).toBe(true);
    const networkCount = state.requests.length;
    // Shared storage changes without this module's acknowledgment/cleanup path.
    if (completion === "deleted") state.stores.opening_attempts.delete("A");
    else state.stores.opening_attempts.set("A", { ...(state.stores.opening_attempts.get("A") as object), delivery_state: "idle", delivery: undefined });
    state.stores.opening_events.clear();
    await act(async () => { expect(await state.journal.recoverOpeningEvidence()).toEqual({ moreWork: false }); });
    expect(state.requests).toHaveLength(networkCount); expect(state.posts()).toEqual([]);
    expect(notifications().filter(record => ["opening-evidence-recovery", "opening-evidence-delivery"].includes(record.key ?? "")).every(record => Boolean(record.resolvedAt))).toBe(true);
    state.mounted.unmount();
  }
});

it("AS-15 blocked opening-evidence operations do not automatically resubmit during idle recovery", async () => {
  const state = await recoveryHarness();
  await state.idle();
  // Offer several normal idle opportunities: none may repeat the blocked POST.
  for (let index = 0; index < 3 && state.callbacks.size; index++) await state.idle();
  expect(state.posts()).toHaveLength(0); expect(state.requests).toHaveLength(1);
  state.unchanged(); expect(state.callbacks.size).toBe(0);
  expect(state.requests.some(request => request.url.endsWith("/retry"))).toBe(false);
  const { notifications } = await import("../../app/lib/notifications");
  expect(notifications().find(record => record.key === "opening-evidence-recovery")?.message).toContain("opening-checkpoint:A");
});

it("live opening appends request idle recovery without bypassing pending receipt backoff", async () => {
  const state = await recoveryHarness([]);
  state.response.mode = "pending";
  vi.mocked(navigator.locks.request).mockImplementation((...arguments_: unknown[]) => {
    if (typeof arguments_[1] === "function") return new Promise(() => undefined);
    return Promise.resolve((arguments_[2] as (lock: object) => unknown)({}));
  });
  const { mapQueueCardToPracticeCard } = await import("../../app/domain/adapters/practice-card-adapters");
  const manifest = evidenceCompletion("live").manifest;
  const card = mapQueueCardToPracticeCard({ id: manifest.card_id, revision: manifest.card_revision, queue_entry_id: 101,
    start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", moves: ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
    repertoire_name: "Live", repertoire_source: "PGN",
    content_type: "opening", opening_decision_manifest: manifest });
  const capture = state.journal.beginOpeningAttempt(card, "live")!;
  await act(async () => { capture.response(manifest.decisions[0].move_offset, manifest.decisions[0].expected_uci, "expected"); });
  await state.advance(0);
  expect(state.posts()).toHaveLength(0);
  await state.idle();
  expect(state.posts()).toHaveLength(1);
  const frozen = structuredClone(state.stores.opening_attempts.get("live"));
  const requestsBeforeAppend = state.requests.length;
  await act(async () => { capture.response(manifest.decisions[1].move_offset, manifest.decisions[1].expected_uci, "expected"); });
  await state.idle(); // An append may reconcile work, but cannot replay this identity early.
  expect(state.requests).toHaveLength(requestsBeforeAppend);
  await state.advance(999);
  expect(state.posts()).toHaveLength(1);
  expect(state.callbacks.size).toBe(0);
  await state.advance(1);
  state.response.mode = "complete";
  await state.idle();
  // The pending operation is read before any new checkpoint is submitted.
  expect(state.requests.some(request => request.url.includes("/api/operations/"))).toBe(true);
  const originalDelivery = (frozen as { delivery: { operationKey: string } }).delivery;
  expect(state.requests.filter(request => request.url.endsWith(encodeURIComponent(originalDelivery.operationKey))).length).toBeGreaterThan(1);
  // The append made during the pending receipt is a separate next slice.
  await state.idle();
  const { notifications } = await import("../../app/lib/notifications");
  expect(notifications().find(record => record.key === "opening-evidence-recovery")?.resolvedAt).toBeTruthy();
});

it("pending opening receipt yields to another journal and keeps its warning until confirmed", async () => {
  const state = await recoveryHarness(["A", "B"]);
  state.response.mode = "complete"; state.responseModes.set("A", "pending");
  await state.idle();
  await state.idle();
  expect(state.stores.opening_attempts.has("A")).toBe(true);
  expect(state.stores.opening_attempts.has("B")).toBe(false);
  const { notifications } = await import("../../app/lib/notifications");
  expect(notifications().find(record => record.key === "opening-evidence-recovery")?.resolvedAt).toBeNull();
  await state.advance(1000); state.responseModes.set("A", "complete");
  await state.idle();
  expect(state.stores.opening_attempts.size).toBe(0);
  expect(notifications().find(record => record.key === "opening-evidence-recovery")?.resolvedAt).toBeTruthy();
  expect(state.posts()).toEqual([]);
});

it("AS-15 transient opening-evidence retries use capped backoff before a new safe idle slice", async () => {
  const state = await recoveryHarness(); state.response.mode = "network";
  await state.idle();
  expect(state.callbacks.size).toBe(0); // Idle availability alone cannot retry.
  expect(vi.getTimerCount()).toBe(1); state.unchanged();
  for (const [index, delay] of [1000, 2000, 4000, 8000, 16000, 30000, 30000].entries()) {
    await state.advance(delay - 1);
    expect(state.recovery).toHaveBeenCalledTimes(index + 1); expect(state.callbacks.size).toBe(0);
    await state.advance(1);
    expect(state.recovery).toHaveBeenCalledTimes(index + 1); expect(state.callbacks.size).toBe(1);
    await state.idle(); expect(state.recovery).toHaveBeenCalledTimes(index + 2);
    expect(vi.getTimerCount()).toBe(1); state.unchanged();
  }
  expect(state.posts()).toEqual([]);
  expect(state.requests.every(request => decodeURIComponent(request.url).endsWith("opening-checkpoint:A"))).toBe(true);
});

it("AS-15 connectivity lease and render storms cannot bypass recovery backoff", async () => {
  const state = await recoveryHarness(); state.response.mode = "network";
  await state.idle();
  act(() => { for (let index = 0; index < 10; index++) { window.dispatchEvent(new Event("online")); state.leaseReleased(); } });
  state.mounted.rerender(<state.Harness />);
  expect(state.callbacks.size).toBe(1); expect(vi.getTimerCount()).toBe(1);
  await state.idle(); expect(state.requests).toHaveLength(1); expect(state.callbacks.size).toBe(0);
  await state.advance(999); expect(state.requests).toHaveLength(1);
  await state.advance(1); expect(state.callbacks.size).toBe(1); expect(state.requests).toHaveLength(1);
  await state.idle(); expect(state.requests).toHaveLength(2); state.unchanged();
});

it("AS-15 foreground activity pauses an eligible recovery retry", async () => {
  const state = await recoveryHarness(); state.response.mode = "network";
  await state.idle(); act(() => state.useTrainingStore.getState().setAttemptPhase("opponentReplyPending"));
  await state.advance(1000); expect(state.callbacks.size).toBe(0); expect(state.recovery).toHaveBeenCalledOnce();
  act(() => state.useTrainingStore.getState().setAttemptPhase("playerTurn"));
  expect(state.callbacks.size).toBe(1); state.response.mode = "complete";
  await state.idle(); expect(state.posts()).toHaveLength(0); expect(state.requests).toHaveLength(2); expect(state.stores.opening_attempts.size).toBe(0);
});

it("AS-15 successful recovery resets backoff and healthy backlog has no delay", async () => {
  const state = await recoveryHarness(["A"]); state.response.mode = "network";
  await state.idle(); await state.advance(1000); await state.idle(); await state.advance(2000);
  state.response.mode = "complete"; await state.idle();
  expect(state.stores.opening_attempts.has("A")).toBe(false); expect(state.callbacks.size).toBe(0); expect(vi.getTimerCount()).toBe(0);
  state.seedPending("B"); state.seedPending("C"); state.responseModes.set("B", "network");
  act(() => window.dispatchEvent(new Event("online")));
  await state.idle(); expect(state.callbacks.size).toBe(1);
  await state.idle(); expect(state.stores.opening_attempts.has("C")).toBe(false); expect(vi.getTimerCount()).toBe(1);
  await state.advance(999); expect(state.callbacks.size).toBe(0);
  await state.advance(1); state.responseModes.set("B", "complete"); await state.idle();
  expect(state.stores.opening_attempts.size).toBe(0); expect(state.callbacks.size).toBe(0); expect(vi.getTimerCount()).toBe(0);
});

it("AS-15 post-paint recovery retries obey the same eligibility delay", async () => {
  const state = await recoveryHarness(["A"], true); state.response.mode = "network";
  expect(state.recovery).not.toHaveBeenCalled(); state.paint(); expect(state.recovery).not.toHaveBeenCalled();
  await state.idle(); expect(state.frames.size).toBe(0); expect(state.messages.size).toBe(0);
  await state.advance(999); expect(state.frames.size).toBe(0);
  await state.advance(1); expect(state.frames.size).toBe(1); expect(state.recovery).toHaveBeenCalledOnce();
  state.paint(); state.response.mode = "complete"; await state.idle();
  expect(state.recovery).toHaveBeenCalledTimes(2); expect(state.ports.every(port => port.closed)).toBe(true);
});

it("AS-15 explicit operation status recovery resumes a deferred journal without starving backlog", async () => {
  const state = await recoveryHarness(["A", "B"]);
  await state.idle(); const frozenA = structuredClone(state.stores.opening_attempts.get("A"));
  state.response.mode = "complete"; await state.idle();
  expect(state.requests.map(request => decodeURIComponent(request.url).split(":").at(-1))).toEqual(["A", "B"]);
  expect(state.stores.opening_attempts.get("A")).toEqual(frozenA); expect(state.callbacks.size).toBe(0);
  act(() => { window.dispatchEvent(new Event("online")); state.leaseReleased(); });
  await state.idle(); expect(state.posts()).toHaveLength(0); expect(state.stores.opening_attempts.get("A")).toEqual(frozenA);
  act(() => state.useTrainingStore.getState().setAttemptPhase("opponentReplyPending"));
  const { retryBlockedOperation } = await import("../../app/lib/operation-status");
  await act(async () => { await retryBlockedOperation("opening-checkpoint:A"); });
  expect(state.callbacks.size).toBe(0); // Status signal requests work, never runs it.
  act(() => state.useTrainingStore.setState({ queueReadiness: "loading" }));
  act(() => state.useTrainingStore.getState().setAttemptPhase("playerTurn")); expect(state.callbacks.size).toBe(0);
  act(() => state.useTrainingStore.setState({ queueReadiness: "ready" })); expect(state.callbacks.size).toBe(1);
  await state.idle();
  expect(state.posts().filter(request => !request.url.endsWith("/retry"))).toEqual([]);
  expect(decodeURIComponent(state.requests.at(-1)!.url)).toContain("opening-checkpoint:A");
  expect(state.stores.opening_attempts.size).toBe(0); expect(state.stores.opening_events.size).toBe(0);
});

it.each(["pending", "executing", "retrying"])("AS-15 unresolved durable operation confirmation is paced (%s)", async receiptState => {
  const state = await recoveryHarness(); state.response.mode = "pending";
  const requestFetch = fetch;
  vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const result = await requestFetch(url, init);
    return init?.method === "POST" ? result : Response.json({ state: receiptState });
  }));
  await state.idle(); expect(state.callbacks.size).toBe(0); state.unchanged();
  await state.advance(1000); expect(state.requests).toHaveLength(1);
  await state.idle(); expect(state.requests).toHaveLength(2); expect(state.posts()).toHaveLength(0); state.unchanged();
});

it.each(["SecurityError", "InvalidStateError", "QuotaExceededError", "NS_ERROR_DOM_QUOTA_REACHED", "malformed", "TypeError", "unknown"])(
  "AS-15 persistent browser recovery errors suspend until repaired state is explicitly reloaded (%s)", async failure => {
    const state = await recoveryHarness();
    const getItem = vi.fn(() => {
      if (failure === "malformed") return "{bad-json";
      if (failure === "TypeError") throw new TypeError("Storage programming failure");
      if (failure === "unknown") throw new Error("Unknown storage failure");
      throw new DOMException("Storage access failed", failure);
    });
    vi.stubGlobal("localStorage", { getItem });
    await state.idle(); expect(state.posts()).toHaveLength(0); expect(state.callbacks.size).toBe(0); expect(vi.getTimerCount()).toBe(0);
    const reads = getItem.mock.calls.length;
    act(() => { for (let index = 0; index < 5; index++) { window.dispatchEvent(new Event("online")); state.leaseReleased(); } });
    state.mounted.rerender(<state.Harness />); await state.advance(120_000);
    expect(getItem).toHaveBeenCalledTimes(reads); state.unchanged();
    const { notifications } = await import("../../app/lib/notifications");
    expect(notifications().find(record => record.key === "opening-evidence-recovery")?.message).toContain("reload Tempo");
    // Repair + explicit page lifecycle restart, rather than an unrelated online event.
    state.mounted.unmount(); vi.stubGlobal("localStorage", { getItem: () => null }); state.response.mode = "complete";
    render(<state.Harness />); expect(state.callbacks.size).toBe(1); await state.idle();
    expect(state.stores.opening_attempts.size).toBe(0);
  });

it("AS-15 offline recovery cancels admission and waits for connectivity without clearing blocked operations", async () => {
  const state = await recoveryHarness();
  Object.assign(navigator, { onLine: false }); act(() => window.dispatchEvent(new Event("offline")));
  expect(state.callbacks.size).toBe(0); await state.advance(60_000); expect(state.requests).toEqual([]);
  Object.assign(navigator, { onLine: true }); act(() => window.dispatchEvent(new Event("online")));
  await state.idle(); expect(state.callbacks.size).toBe(0); expect(state.posts()).toHaveLength(0); expect(state.requests).toHaveLength(1);
  Object.assign(navigator, { onLine: false }); act(() => window.dispatchEvent(new Event("offline")));
  Object.assign(navigator, { onLine: true }); act(() => window.dispatchEvent(new Event("online")));
  await state.idle(); expect(state.posts()).toHaveLength(0); expect(state.requests).toHaveLength(1); state.unchanged();
});

it.each(["unmount", "disable"])("AS-15 retry cleanup cancels delayed and idle work (%s)", async action => {
  const state = await recoveryHarness(); state.response.mode = "network";
  await state.idle(); expect(vi.getTimerCount()).toBe(1);
  if (action === "unmount") state.mounted.unmount(); else state.mounted.rerender(<state.Harness enabled={false} />);
  expect(vi.getTimerCount()).toBe(0); await state.advance(60_000);
  act(() => window.dispatchEvent(new Event("online"))); expect(state.callbacks.size).toBe(0); expect(state.recovery).toHaveBeenCalledOnce();
});

it("AS-15 a warning subscriber cannot change blocked suspension or transient backoff", async () => {
  const state = await recoveryHarness();
  const { subscribeNotifications } = await import("../../app/lib/notifications");
  const unsubscribe = subscribeNotifications(() => { throw new Error("Subscriber failed"); });
  try {
    await state.idle(); expect(state.callbacks.size).toBe(0); expect(state.posts()).toHaveLength(0); expect(state.requests).toHaveLength(1); state.unchanged();
  } finally { unsubscribe(); }
});

it("AS-15 unknown HTTP and application failures are conservative while known service failures retry", async () => {
  const { openingEvidenceRecoveryPolicy, OpeningEvidenceHttpError, openingEvidenceFetch } = await import("../../app/lib/opening-evidence-recovery-policy");
  for (const status of [408, 429, 500, 503, 504]) expect(openingEvidenceRecoveryPolicy(new OpeningEvidenceHttpError(status))).toBe("retry");
  for (const status of [400, 401, 403, 422]) expect(openingEvidenceRecoveryPolicy(new OpeningEvidenceHttpError(status))).toBe("suspend");
  expect(openingEvidenceRecoveryPolicy(new TypeError("Application error"))).toBe("suspend");
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Network error"); }));
  const transport = await openingEvidenceFetch("/shadow").catch(error => error);
  expect(openingEvidenceRecoveryPolicy(transport)).toBe("retry");
});

it("AS-15 events during an active failing slice cannot create a concurrent or early retry", async () => {
  const state = await recoveryHarness();
  let rejectRequest!: (error: Error) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((_resolve, reject) => { rejectRequest = reject; })));
  await state.idle(false); const first = state.recovery.mock.results[0].value.catch(() => undefined);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  act(() => { for (let index = 0; index < 5; index++) { window.dispatchEvent(new Event("online")); state.leaseReleased(); } });
  state.mounted.rerender(<state.Harness />); expect(state.callbacks.size).toBe(0);
  expect(state.journal.recoverOpeningEvidence()).toBe(state.recovery.mock.results[0].value);
  await act(async () => { rejectRequest(new TypeError("Network unavailable")); }); await first;
  expect(state.callbacks.size).toBe(1); expect(vi.getTimerCount()).toBe(1); expect(fetch).toHaveBeenCalledOnce();
  await state.idle(); expect(state.callbacks.size).toBe(0); expect(fetch).toHaveBeenCalledOnce(); state.unchanged();
  await state.advance(1000); expect(state.callbacks.size).toBe(1); expect(fetch).toHaveBeenCalledOnce();
});

it("AS-15 disabling and re-enabling recovery preserves the pending retry deadline", async () => {
  const state = await recoveryHarness(); state.response.mode = "network";
  await state.idle(); await state.advance(250);
  state.mounted.rerender(<state.Harness enabled={false} />); expect(vi.getTimerCount()).toBe(0);
  await state.advance(250); state.mounted.rerender(<state.Harness />);
  expect(vi.getTimerCount()).toBe(1); expect(state.callbacks.size).toBe(0);
  await state.advance(499); expect(state.callbacks.size).toBe(0);
  await state.advance(1); expect(state.callbacks.size).toBe(1); expect(state.recovery).toHaveBeenCalledOnce();
});

it("AS-15 malformed saved journal validation suspends without discarding evidence", async () => {
  const state = await recoveryHarness();
  const malformed = { ...(state.stores.opening_attempts.get("A") as object), source: "invalid-source" };
  state.stores.opening_attempts.set("A", malformed);
  await state.idle(); expect(state.requests).toEqual([]); expect(state.callbacks.size).toBe(0); expect(vi.getTimerCount()).toBe(0);
  act(() => window.dispatchEvent(new Event("online"))); await state.advance(60_000);
  expect(state.requests).toEqual([]); expect(state.stores.opening_attempts.get("A")).toEqual(malformed);
  expect(state.stores.opening_events.size).toBe(3);
});

it("AS-15 a checkpoint deadline becomes paced transport recovery with unchanged delivery", async () => {
  const state = await recoveryHarness(); const completedFetch = fetch;
  vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(new DOMException("Request aborted", "AbortError")), { once: true });
  })));
  await state.idle(false); const first = state.recovery.mock.results[0].value.catch(() => undefined);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  await state.advance(15_000); await first;
  expect(state.callbacks.size).toBe(0); expect(vi.getTimerCount()).toBe(1); state.unchanged();
  state.response.mode = "complete"; vi.stubGlobal("fetch", completedFetch);
  await state.advance(1000); expect(state.recovery).toHaveBeenCalledOnce();
  await state.idle(); expect(state.stores.opening_attempts.size).toBe(0); expect(vi.getTimerCount()).toBe(0);
});
