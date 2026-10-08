import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { evidenceCompletion, openingEvidenceStorage } from "../fixtures/opening-evidence-storage";

const browserStorage = localStorage;
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); browserStorage.clear(); });

async function transientRecoveryFailures(failedSlices: number) {
  const state = await openingEvidenceStorage();
  const completion = evidenceCompletion("transient-orphan"); state.seed(completion);
  const originalAttempt = structuredClone(state.stores.opening_attempts.get(completion.attempt_id));
  const originalEvents = structuredClone([...state.stores.opening_events]);
  let verificationCount = 0;
  const delivered: typeof completion[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-Tempo-Work-Class")).toBe("background");
    if (init?.method !== "POST") {
      if (++verificationCount <= failedSlices) throw new TypeError("Transient verification failure");
      return Response.json({}, { status: 404 });
    }
    const checkpoint = JSON.parse(init.body as string) as typeof completion;
    delivered.push(checkpoint);
    return Response.json({ persisted: true, attempt_id: checkpoint.attempt_id,
      received_sequences: [1, 2, 3], contiguous_sequence: 3 });
  }));
  const journal = await import("../../app/lib/opening-evidence-journal");
  const recovery = vi.spyOn(journal, "recoverOpeningEvidence");
  const { useTrainingStore } = await import("../../app/state/training-store");
  const { useOpeningEvidenceRecovery } = await import("../../app/hooks/use-opening-evidence-recovery");
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  useTrainingStore.setState({ queueReadiness: "ready" });
  const callbacks = new Map<number, IdleRequestCallback>(); let sequence = 0;
  vi.stubGlobal("requestIdleCallback", vi.fn(callback => { callbacks.set(++sequence, callback); return sequence; }));
  vi.stubGlobal("cancelIdleCallback", vi.fn(id => callbacks.delete(id)));
  function Harness() {
    const ready = useTrainingStore(store => store.queueReadiness === "ready");
    const blocked = useTrainingStore(store => store.attempt.phase === "opponentReplyPending");
    useOpeningEvidenceRecovery(true, ready, blocked); return null;
  }
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  const mounted = render(<Harness />);
  const advance = async (milliseconds = 1000) => { await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); }); };
  const idle = async (settle = true) => {
    expect(callbacks.size).toBe(1);
    const [id, callback] = [...callbacks][0]; callbacks.delete(id);
    await act(async () => { callback({ didTimeout: false, timeRemaining: () => 50 });
      if (settle) await recovery.mock.results.at(-1)?.value.catch(() => undefined); });
  };
  const expectUnchangedJournal = () => {
    expect(state.stores.opening_attempts.get(completion.attempt_id)).toEqual(originalAttempt);
    expect([...state.stores.opening_events]).toEqual(originalEvents);
    expect(state.commits).toEqual([]); expect(delivered).toEqual([]);
  };
  return { ...state, completion, originalAttempt, delivered, recovery, callbacks, idle,
    expectUnchangedJournal, mounted, useTrainingStore, advance };
}

it("AS-15 transient opening-evidence recovery failure schedules a paced bounded idle slice", async () => {
  const state = await transientRecoveryFailures(1);
  await state.idle();
  expect(state.recovery).toHaveBeenCalledOnce(); state.expectUnchangedJournal();
  expect(state.useTrainingStore.getState().queueReadiness).toBe("ready");
  expect(state.callbacks.size).toBe(0); await state.advance();
  expect(state.callbacks.size).toBe(1);
  expect(requestIdleCallback).toHaveBeenCalledTimes(2);
  // No connectivity/readiness/lease event or synchronous retry supplies this opportunity.
  expect(state.recovery).toHaveBeenCalledOnce();
  await state.idle();
  expect(state.delivered).toHaveLength(1);
  expect(state.recovery).toHaveBeenCalledTimes(2); expect(state.callbacks.size).toBe(0);
  expect(state.delivered[0]).toEqual({ ...state.completion, terminal: { ...state.completion.terminal!, state: "partial" } });
  expect(state.stores.opening_attempts.size).toBe(0); expect(state.stores.opening_events.size).toBe(0);
});

it("AS-15 foreground activity pauses a retry scheduled after recovery failure", async () => {
  const state = await transientRecoveryFailures(1);
  await state.idle(); await state.advance(); expect(state.callbacks.size).toBe(1);
  act(() => state.useTrainingStore.getState().setAttemptPhase("opponentReplyPending"));
  expect(state.callbacks.size).toBe(0); state.expectUnchangedJournal();
  act(() => { for (let index = 0; index < 3; index++) window.dispatchEvent(new Event("online")); });
  expect(state.callbacks.size).toBe(0); expect(state.recovery).toHaveBeenCalledOnce();
  act(() => state.useTrainingStore.getState().setAttemptPhase("playerTurn"));
  expect(state.callbacks.size).toBe(1); expect(state.recovery).toHaveBeenCalledOnce();
  await state.idle(); expect(state.delivered).toHaveLength(1);
  expect(state.recovery).toHaveBeenCalledTimes(2); expect(state.callbacks.size).toBe(0);
});

it("AS-15 repeated transient recovery failures each wait before a distinct idle opportunity", async () => {
  const state = await transientRecoveryFailures(2);
  for (let failedSlice = 1; failedSlice <= 2; failedSlice++) {
    await state.idle();
    expect(state.recovery).toHaveBeenCalledTimes(failedSlice); state.expectUnchangedJournal();
    expect(state.callbacks.size).toBe(0); await state.advance(1000 * failedSlice);
    expect(state.callbacks.size).toBe(1);
    expect(requestIdleCallback).toHaveBeenCalledTimes(failedSlice + 1);
    expect(state.recovery).toHaveBeenCalledTimes(failedSlice);
  }
  const { notifications } = await import("../../app/lib/notifications");
  expect(notifications().filter(record => record.key === "opening-evidence-recovery")).toMatchObject([
    { severity: "warning", occurrenceCount: 2, message: expect.stringContaining("Opening evidence recovery is pending. Normal training continues.") },
  ]);
  await state.idle(); expect(state.delivered).toHaveLength(1);
  expect(state.recovery).toHaveBeenCalledTimes(3); expect(state.callbacks.size).toBe(0);
});

it("AS-15 unmount cancels an idle retry after transient recovery failure", async () => {
  const state = await transientRecoveryFailures(1);
  await state.idle(); await state.advance(); expect(state.callbacks.size).toBe(1);
  const canceledCallback = [...state.callbacks.values()][0];
  state.mounted.unmount(); expect(state.callbacks.size).toBe(0);
  await act(async () => { canceledCallback({ didTimeout: false, timeRemaining: () => 50 }); });
  expect(state.recovery).toHaveBeenCalledOnce(); state.expectUnchangedJournal();
});

it("AS-15 a transient recovery failure settling after unmount cannot schedule retry", async () => {
  const state = await transientRecoveryFailures(1);
  let rejectVerification!: (error: Error) => void;
  vi.mocked(fetch).mockImplementation(() => new Promise<Response>((_resolve, reject) => { rejectVerification = reject; }));
  await state.idle(false); await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  state.mounted.unmount();
  await act(async () => { rejectVerification(new TypeError("Transient verification failure")); });
  expect(state.callbacks.size).toBe(0); expect(requestIdleCallback).toHaveBeenCalledOnce();
  expect(state.recovery).toHaveBeenCalledOnce(); state.expectUnchangedJournal();
});

it("AS-15 a throwing warning subscriber cannot strand the next recovery idle slice", async () => {
  const state = await transientRecoveryFailures(1);
  const { subscribeNotifications } = await import("../../app/lib/notifications");
  const subscriber = vi.fn(() => { throw new Error("Notification subscriber failure"); });
  const unsubscribe = subscribeNotifications(subscriber);
  try {
    await state.idle(); expect(state.callbacks.size).toBe(0); await state.advance(); expect(state.callbacks.size).toBe(1);
    expect(subscriber).toHaveBeenCalled(); expect(state.recovery).toHaveBeenCalledOnce(); state.expectUnchangedJournal();
  } finally { unsubscribe(); }
  await state.idle(); expect(state.delivered).toHaveLength(1);
  expect(state.recovery).toHaveBeenCalledTimes(2); expect(state.callbacks.size).toBe(0);
});

it("AS-15 orphan completion verification timeout yields and retries safely", async () => {
  const state = await openingEvidenceStorage(); const completion = evidenceCompletion("orphan"); state.seed(completion);
  const original = structuredClone(state.stores.opening_attempts.get("orphan"));
  const journal = await import("../../app/lib/opening-evidence-journal");
  vi.useFakeTimers(); let release!: (response: Response) => void; let verificationSignal: AbortSignal | null | undefined;
  let verificationTimedOutAt = 0;
  const posts: string[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-Tempo-Work-Class")).toBe("background");
    if (init?.method === "POST") { posts.push(JSON.parse(init.body as string).attempt_id);
      return Promise.resolve(Response.json({ persisted: true, attempt_id: "orphan", received_sequences: [1, 2, 3], contiguous_sequence: 3 })); }
    expect(url).toContain("/attempts/orphan"); verificationSignal = init?.signal;
    return new Promise<Response>((resolve, reject) => { release = resolve;
      init?.signal?.addEventListener("abort", () => { verificationTimedOutAt = Date.now(); reject(new DOMException("Verification aborted", "AbortError")); }, { once: true }); });
  }));
  let failed = false; let settled = false;
  const active = journal.recoverOpeningEvidence(); expect(journal.recoverOpeningEvidence()).toBe(active);
  const first = active.catch(() => { failed = true; }).finally(() => { settled = true; });
  try {
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await vi.advanceTimersByTimeAsync(15_000);
    expect(settled).toBe(true); expect(failed).toBe(true); expect(verificationSignal?.aborted).toBe(true);
    expect(state.stores.opening_attempts.get("orphan")).toEqual(original); expect(posts).toEqual([]); expect(state.commits).toEqual([]);
    vi.mocked(fetch).mockImplementation(async (_url, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("X-Tempo-Work-Class")).toBe("background");
      if (init?.method !== "POST") return Response.json({}, { status: 404 });
      const checkpoint = JSON.parse(init.body as string); expect(checkpoint.terminal).toEqual({ ...completion.terminal, state: "partial" }); posts.push(checkpoint.attempt_id);
      return Response.json({ persisted: true, attempt_id: "orphan", received_sequences: [1, 2, 3], contiguous_sequence: 3 });
    });
    const deadline = verificationTimedOutAt + 1000;
    expect(await journal.recoverOpeningEvidence()).toEqual({ moreWork: false, nextRetryAt: deadline });
    expect(posts).toEqual([]);
    await vi.advanceTimersByTimeAsync(deadline - Date.now() - 1);
    expect(await journal.recoverOpeningEvidence()).toEqual({ moreWork: false, nextRetryAt: deadline }); expect(posts).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(await journal.recoverOpeningEvidence()).toEqual({ moreWork: false }); expect(posts).toEqual(["orphan"]);
    expect(state.stores.opening_attempts.has("orphan")).toBe(false);
  } finally { release?.(Response.json({}, { status: 404 })); await first; }
});

async function leasedBacklog(heldIds: string[]) {
  const state = await openingEvidenceStorage(); for (const id of ["A", "B", "C"]) state.seed(evidenceCompletion(id));
  const held = new Set(heldIds);
  const waiters = new Map<string, { release: () => Promise<void>; signal?: AbortSignal }>();
  vi.mocked(navigator.locks.request).mockImplementation(((name: string, options: LockOptions, callback: LockGrantedCallback<unknown>) => {
    const id = name.split(":").at(-1)!;
    if (options.ifAvailable) return Promise.resolve(callback(held.has(id) ? null : {} as Lock));
    return new Promise<void>((resolve, reject) => {
      waiters.set(id, { signal: options.signal, release: async () => { held.delete(id); await callback({} as Lock); resolve(); } });
      options.signal?.addEventListener("abort", () => reject(new DOMException("Watcher canceled", "AbortError")), { once: true });
    });
  }) as typeof navigator.locks.request);
  const delivered: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-Tempo-Work-Class")).toBe("background");
    if (init?.method !== "POST") return Response.json({}, { status: 404 });
    const checkpoint = JSON.parse(init.body as string); delivered.push(checkpoint.attempt_id);
    return Response.json({ persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1, 2, 3], contiguous_sequence: 3 });
  }));
  const { useTrainingStore } = await import("../../app/state/training-store");
  const { useOpeningEvidenceRecovery } = await import("../../app/hooks/use-opening-evidence-recovery");
  useTrainingStore.setState(useTrainingStore.getInitialState(), true); useTrainingStore.setState({ queueReadiness: "ready" });
  const callbacks = new Map<number, IdleRequestCallback>(); let sequence = 0;
  vi.stubGlobal("requestIdleCallback", vi.fn(callback => { callbacks.set(++sequence, callback); return sequence; }));
  vi.stubGlobal("cancelIdleCallback", vi.fn(id => callbacks.delete(id)));
  function Harness() {
    const ready = useTrainingStore(store => store.queueReadiness === "ready");
    const blocked = useTrainingStore(store => store.attempt.phase === "opponentReplyPending");
    useOpeningEvidenceRecovery(true, ready, blocked); return null;
  }
  const mounted = render(<Harness />);
  const idle = async () => { const [id, callback] = [...callbacks][0]; callbacks.delete(id);
    await act(async () => { callback({ didTimeout: false, timeRemaining: () => 50 }); }); };
  return { ...state, delivered, waiters, callbacks, idle, mounted, useTrainingStore };
}

it("AS-15 lease-blocked opening evidence is retried after the owning tab releases it", async () => {
  const state = await leasedBacklog(["A"]); const originalA = structuredClone(state.stores.opening_attempts.get("A"));
  await state.idle(); await waitFor(() => expect(state.callbacks.size).toBe(1));
  await state.idle(); await waitFor(() => expect(state.delivered).toEqual(["B"]));
  await waitFor(() => expect(state.callbacks.size).toBe(1)); await state.idle();
  await waitFor(() => expect(state.delivered).toEqual(["B", "C"])); expect(state.callbacks.size).toBe(0);
  expect(state.stores.opening_attempts.get("A")).toEqual(originalA);
  act(() => state.useTrainingStore.getState().setAttemptPhase("opponentReplyPending"));
  await act(async () => { await state.waiters.get("A")?.release(); });
  expect(state.callbacks.size).toBe(0); expect(state.delivered).toEqual(["B", "C"]);
  act(() => state.useTrainingStore.getState().setAttemptPhase("playerTurn"));
  await waitFor(() => expect(state.callbacks.size).toBe(1));
  await state.idle(); await waitFor(() => expect(state.delivered).toEqual(["B", "C", "A"]));
  expect(state.callbacks.size).toBe(0);
});

it("AS-15 live lease retry does not create an idle recovery loop", async () => {
  const state = await leasedBacklog(["A", "B"]);
  await state.idle(); await waitFor(() => expect(state.callbacks.size).toBe(1));
  await state.idle(); await waitFor(() => expect(state.callbacks.size).toBe(1));
  await state.idle(); await waitFor(() => expect(state.delivered).toEqual(["C"]));
  expect(state.callbacks.size).toBe(0); const lockRequests = vi.mocked(navigator.locks.request).mock.calls.length;
  const writes = state.writes.length; const reads = vi.mocked(fetch).mock.calls.length;
  vi.useFakeTimers(); await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(state.callbacks.size).toBe(0); expect(navigator.locks.request).toHaveBeenCalledTimes(lockRequests);
  expect(state.writes).toHaveLength(writes); expect(fetch).toHaveBeenCalledTimes(reads);
  // Restore browser task scheduling before simulating the ownership-release event.
  vi.useRealTimers();
  await act(async () => { await Promise.all([state.waiters.get("A")?.release(), state.waiters.get("B")?.release()]); });
  expect(state.callbacks.size).toBe(1); // Both ownership releases coalesce into one future idle opportunity.
  await state.idle(); await waitFor(() => expect(state.delivered).toEqual(["C", "A"]));
  await waitFor(() => expect(state.callbacks.size).toBe(1)); await state.idle();
  await waitFor(() => expect(state.delivered).toEqual(["C", "A", "B"])); expect(state.callbacks.size).toBe(0);
});

it("AS-15 unmount cancels passive lease waiters without running recovery", async () => {
  const state = await leasedBacklog(["A"]); const originalA = structuredClone(state.stores.opening_attempts.get("A"));
  await state.idle(); await waitFor(() => expect(state.waiters.has("A")).toBe(true));
  state.mounted.unmount(); expect(state.waiters.get("A")?.signal?.aborted).toBe(true);
  expect(state.callbacks.size).toBe(0);
  await act(async () => { await state.waiters.get("A")!.release(); });
  expect(state.callbacks.size).toBe(0); expect(state.delivered).toEqual([]);
  expect(state.stores.opening_attempts.get("A")).toEqual(originalA);
});

it("AS-15 orphan verification response body shares its bounded deadline", async () => {
  const state = await openingEvidenceStorage(); state.seed(evidenceCompletion("orphan-body"));
  const original = structuredClone(state.stores.opening_attempts.get("orphan-body"));
  const journal = await import("../../app/lib/opening-evidence-journal"); vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => new Response(new ReadableStream({
    start(controller) { init?.signal?.addEventListener("abort", () => controller.error(new DOMException("Body aborted", "AbortError")), { once: true }); },
  }))));
  const result = journal.recoverOpeningEvidence(); const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce()); await vi.advanceTimersByTimeAsync(15_000); await rejected;
  expect(state.stores.opening_attempts.get("orphan-body")).toEqual(original); expect(state.commits).toEqual([]);
  expect(vi.getTimerCount()).toBe(0);
});
