import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { evidenceCompletion, openingEvidenceStorage } from "../fixtures/opening-evidence-storage";

const browserStorage = localStorage;
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); browserStorage.clear(); });

async function recoveryHarness(ids = ["A"], fallback = false) {
  const storage = await openingEvidenceStorage();
  for (const id of ids) {
    const checkpoint = evidenceCompletion(id);
    checkpoint.terminal!.state = "partial";
    storage.seed(checkpoint);
    storage.stores.opening_attempts.set(id, { ...(storage.stores.opening_attempts.get(id) as object),
      delivery_state: "pending", delivery: { checkpoint, operationKey: `opening-checkpoint:${id}` } });
  }
  const originals = structuredClone([...storage.stores.opening_attempts]);
  const events = structuredClone([...storage.stores.opening_events]);
  const requests: { url: string; init?: RequestInit }[] = [];
  let leaseReleased!: () => void;
  const response = { mode: "blocked" as "blocked" | "network" | "pending" | "complete" };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    if (response.mode === "network") throw new TypeError("Fetch failed");
    const key = init?.method === "POST" ? new Headers(init.headers).get("Idempotency-Key")! : decodeURIComponent(url.split("/").at(-1)!);
    if (init?.method === "POST" && response.mode !== "complete") return Response.json({ operation_id: key }, { status: 202 });
    const id = key.split(":").at(-1)!;
    const persisted = { persisted: true, attempt_id: id, received_sequences: [1, 2, 3], contiguous_sequence: 3 };
    return Response.json(init?.method === "POST" ? persisted : response.mode === "complete"
      ? { state: "complete", response: persisted } : { state: response.mode, last_error: { message: "Repair the local worker" } });
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
  return { ...storage, journal, recovery, response, requests, callbacks, mounted, Harness, idle, advance, posts, unchanged, useTrainingStore, leaseReleased: () => leaseReleased(), frames, messages, ports, paint };
}

it("AS-15 blocked opening-evidence operations do not automatically resubmit during idle recovery", async () => {
  const state = await recoveryHarness();
  await state.idle();
  // Offer several normal idle opportunities: none may repeat the blocked POST.
  for (let index = 0; index < 3 && state.callbacks.size; index++) await state.idle();
  expect(state.posts()).toHaveLength(1);
  state.unchanged(); expect(state.callbacks.size).toBe(0);
  expect(state.requests.some(request => request.url.endsWith("/retry"))).toBe(false);
  const { notifications } = await import("../../app/lib/notifications");
  expect(notifications().find(record => record.key === "opening-evidence-recovery")?.message).toContain("opening-checkpoint:A");
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
  expect(state.posts().every(request => request.init?.body === state.posts()[0].init?.body &&
    new Headers(request.init?.headers).get("Idempotency-Key") === "opening-checkpoint:A")).toBe(true);
});

it("AS-15 connectivity lease and render storms cannot bypass recovery backoff", async () => {
  const state = await recoveryHarness(); state.response.mode = "network";
  await state.idle();
  act(() => { for (let index = 0; index < 10; index++) { window.dispatchEvent(new Event("online")); state.leaseReleased(); } });
  state.mounted.rerender(<state.Harness />);
  expect(state.callbacks.size).toBe(0); expect(vi.getTimerCount()).toBe(1);
  await state.advance(999); expect(state.recovery).toHaveBeenCalledOnce();
  await state.advance(1); expect(state.callbacks.size).toBe(1); expect(state.recovery).toHaveBeenCalledOnce();
  await state.idle(); expect(state.recovery).toHaveBeenCalledTimes(2); state.unchanged();
});

it("AS-15 foreground activity pauses an eligible recovery retry", async () => {
  const state = await recoveryHarness(); state.response.mode = "network";
  await state.idle(); act(() => state.useTrainingStore.getState().setAttemptPhase("opponentReplyPending"));
  await state.advance(1000); expect(state.callbacks.size).toBe(0); expect(state.recovery).toHaveBeenCalledOnce();
  act(() => state.useTrainingStore.getState().setAttemptPhase("playerTurn"));
  expect(state.callbacks.size).toBe(1); state.response.mode = "complete";
  await state.idle(); expect(state.posts()).toHaveLength(2); expect(state.stores.opening_attempts.size).toBe(0);
});

it("AS-15 successful recovery resets backoff and healthy backlog has no delay", async () => {
  const state = await recoveryHarness(["A", "B", "C"]); state.response.mode = "network";
  await state.idle(); await state.advance(1000); await state.idle(); await state.advance(2000);
  state.response.mode = "complete"; await state.idle();
  expect(state.stores.opening_attempts.has("A")).toBe(false); expect(state.callbacks.size).toBe(1); expect(vi.getTimerCount()).toBe(0);
  state.response.mode = "network"; await state.idle(); await state.advance(999); expect(state.callbacks.size).toBe(0);
  await state.advance(1); state.response.mode = "complete"; await state.idle();
  expect(state.callbacks.size).toBe(1); expect(vi.getTimerCount()).toBe(0);
  await state.idle(); expect(state.stores.opening_attempts.size).toBe(0); expect(state.callbacks.size).toBe(0);
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
  expect(state.posts().map(request => JSON.parse(request.init!.body as string).attempt_id)).toEqual(["A", "B"]);
  expect(state.stores.opening_attempts.get("A")).toEqual(frozenA); expect(state.callbacks.size).toBe(0);
  act(() => { window.dispatchEvent(new Event("online")); state.leaseReleased(); });
  await state.idle(); expect(state.posts()).toHaveLength(2); expect(state.stores.opening_attempts.get("A")).toEqual(frozenA);
  act(() => state.useTrainingStore.getState().setAttemptPhase("opponentReplyPending"));
  const { retryBlockedOperation } = await import("../../app/lib/operation-status");
  await act(async () => { await retryBlockedOperation("opening-checkpoint:A"); });
  expect(state.callbacks.size).toBe(0); // Status signal requests work, never runs it.
  act(() => state.useTrainingStore.setState({ queueReadiness: "loading" }));
  act(() => state.useTrainingStore.getState().setAttemptPhase("playerTurn")); expect(state.callbacks.size).toBe(0);
  act(() => state.useTrainingStore.setState({ queueReadiness: "ready" })); expect(state.callbacks.size).toBe(1);
  await state.idle();
  const checkpointPosts = state.posts().filter(request => !request.url.endsWith("/retry"));
  expect(checkpointPosts[2].init?.body).toBe(checkpointPosts[0].init?.body);
  expect(new Headers(checkpointPosts[2].init?.headers).get("Idempotency-Key")).toBe("opening-checkpoint:A");
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
  await state.advance(1000); expect(state.posts()).toHaveLength(1);
  await state.idle(); expect(state.posts()).toHaveLength(2); state.unchanged();
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
  await state.idle(); await state.idle(); expect(state.posts()).toHaveLength(1);
  Object.assign(navigator, { onLine: false }); act(() => window.dispatchEvent(new Event("offline")));
  Object.assign(navigator, { onLine: true }); act(() => window.dispatchEvent(new Event("online")));
  await state.idle(); expect(state.posts()).toHaveLength(1); state.unchanged();
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
    await state.idle(); await state.idle(); expect(state.posts()).toHaveLength(1); state.unchanged();
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
  expect(state.callbacks.size).toBe(0); expect(vi.getTimerCount()).toBe(1); expect(fetch).toHaveBeenCalledOnce(); state.unchanged();
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
