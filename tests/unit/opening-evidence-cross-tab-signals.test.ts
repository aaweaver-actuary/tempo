import { afterEach, expect, it, vi } from "vitest";
import { evidenceCompletion, openingEvidenceStorage } from "../fixtures/opening-evidence-storage";

const signalKey = "tempo-opening-evidence-completion-v1";
const browserStorage = localStorage;
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); browserStorage.clear(); });
async function signalingHarness() {
  const storage = await openingEvidenceStorage();
  const channels: TestChannel[] = [];
  class TestChannel {
    onmessage: ((event: { data: unknown }) => void) | null = null;
    close = vi.fn(); postMessage = vi.fn();
    constructor(readonly name: string) { channels.push(this); }
  }
  vi.stubGlobal("BroadcastChannel", TestChannel);
  const setItem = vi.fn(); vi.stubGlobal("localStorage", { getItem: () => null, setItem });
  const journal = await import("../../app/lib/opening-evidence-journal");
  storage.seed(evidenceCompletion("A"));
  return { ...storage, channels, setItem, journal };
}

it("opening acknowledgment publishes only after atomic evidence removal and never leaks journal content", async () => {
  const state = await signalingHarness();
  state.controls.beforeCommit = () => { expect(state.setItem).not.toHaveBeenCalled(); return undefined; };
  await state.journal.acknowledgeOpeningReview("A");
  expect(state.stores.opening_attempts.size).toBe(0); expect(state.stores.opening_events.size).toBe(0);
  expect(state.setItem).toHaveBeenCalledOnce();
  const [key, raw] = state.setItem.mock.calls[0]; expect(key).toBe(signalKey);
  expect(JSON.parse(raw)).toEqual({ attemptId: "A", signalId: expect.any(String), sourceSessionId: expect.any(String) });
  expect(state.channels).toHaveLength(0);
  state.controls.beforeCommit = undefined;
  await state.journal.acknowledgeOpeningReview("A"); expect(state.setItem).toHaveBeenCalledOnce();
});

it("aborted acknowledgment preserves saved evidence and emits no completion", async () => {
  const state = await signalingHarness(); const saved = structuredClone([...state.stores.opening_attempts]);
  state.controls.beforeCommit = () => new DOMException("Transaction aborted", "AbortError");
  await expect(state.journal.acknowledgeOpeningReview("A")).rejects.toThrow("Transaction aborted");
  expect([...state.stores.opening_attempts]).toEqual(saved); expect(state.stores.opening_events.size).toBe(3);
  expect(state.setItem).not.toHaveBeenCalled(); expect(state.channels).toHaveLength(0);
});

it.each(["retained", "rejected"])("acknowledgment of %s evidence never emits a misleading completion", async delivery_state => {
  const state = await signalingHarness();
  state.stores.opening_attempts.set("A", { ...(state.stores.opening_attempts.get("A") as object), delivery_state });
  await state.journal.acknowledgeOpeningReview("A");
  expect(state.stores.opening_attempts.has("A")).toBe(true); expect(state.stores.opening_events.size).toBe(3);
  expect(state.setItem).not.toHaveBeenCalled(); expect(state.channels).toHaveLength(0);
});

it("completion falls back to a closed BroadcastChannel without reversing a committed acknowledgment", async () => {
  const state = await signalingHarness(); state.setItem.mockImplementation(() => { throw new DOMException("Full", "QuotaExceededError"); });
  await state.journal.acknowledgeOpeningReview("A");
  expect(state.channels).toHaveLength(1); expect(state.channels[0].name).toBe(signalKey);
  expect(state.channels[0].postMessage).toHaveBeenCalledWith({ attemptId: "A", signalId: expect.any(String), sourceSessionId: expect.any(String) });
  expect(state.channels[0].close).toHaveBeenCalledOnce(); expect(state.stores.opening_attempts.size).toBe(0);
});

it("unavailable completion transports preserve durable success and report actionable diagnostics", async () => {
  const state = await signalingHarness(); state.setItem.mockImplementation(() => { throw new Error("Disabled storage"); });
  vi.stubGlobal("BroadcastChannel", undefined);
  await expect(state.journal.acknowledgeOpeningReview("A")).resolves.toBeUndefined();
  expect(state.stores.opening_attempts.size).toBe(0);
  const { notifications } = await import("../../app/lib/notifications");
  expect(notifications().find(record => record.key === "opening-evidence-completion-signal")).toMatchObject({ severity: "warning",
    message: expect.stringContaining("Reload other Tempo tabs") });
});

it("completion subscriptions ignore malformed own and unrelated storage events and clean up both transports", async () => {
  const state = await signalingHarness(); const first = vi.fn(), second = vi.fn();
  const stopFirst = state.journal.subscribeOpeningEvidenceAppend(first);
  const stopSecond = state.journal.subscribeOpeningEvidenceAppend(second);
  expect(state.channels).toHaveLength(1);
  const receive = state.channels[0].onmessage!;
  for (const data of [null, {}, { attemptId: "A", signalId: 4, sourceSessionId: "B" }]) receive({ data });
  window.dispatchEvent(new StorageEvent("storage", { key: signalKey, newValue: "{" }));
  window.dispatchEvent(new StorageEvent("storage", { key: "other-key", newValue: "{}" }));
  window.dispatchEvent(new StorageEvent("storage", { key: signalKey, newValue: null }));
  expect(first).not.toHaveBeenCalled();
  await state.journal.acknowledgeOpeningReview("A"); expect(first).toHaveBeenCalledOnce();
  const ownSignal = JSON.parse(state.setItem.mock.calls[0][1]); receive({ data: ownSignal });
  expect(first).toHaveBeenCalledOnce();
  first.mockClear(); second.mockClear();
  const externalSignal = { ...ownSignal, sourceSessionId: "other-tab" };
  receive({ data: externalSignal }); receive({ data: externalSignal });
  expect(first).toHaveBeenCalledTimes(2); expect(second).toHaveBeenCalledTimes(2);
  expect(state.setItem).toHaveBeenCalledOnce(); // Receiving never echoes.
  stopFirst(); receive({ data: externalSignal }); expect(first).toHaveBeenCalledTimes(2); expect(second).toHaveBeenCalledTimes(3);
  expect(state.channels[0].close).not.toHaveBeenCalled(); stopSecond();
  expect(state.channels[0].close).toHaveBeenCalledOnce(); expect(state.channels[0].onmessage).toBeNull();
  window.dispatchEvent(new StorageEvent("storage", { key: signalKey, newValue: JSON.stringify(externalSignal) }));
  expect(second).toHaveBeenCalledTimes(3);
  const stopAgain = state.journal.subscribeOpeningEvidenceAppend(first);
  expect(state.channels).toHaveLength(2); stopAgain(); expect(state.channels[1].close).toHaveBeenCalledOnce();
});

it.each([false, true])("checkpoint confirmation emits completion only after a successful transaction (abort=%s)", async abort => {
  const state = await signalingHarness();
  const checkpoint = evidenceCompletion("A"); checkpoint.terminal!.state = "partial";
  state.seed(checkpoint);
  state.stores.opening_attempts.set("A", { ...(state.stores.opening_attempts.get("A") as object),
    delivery_state: "pending", delivery: { checkpoint, operationKey: "opening-checkpoint:A" } });
  const saved = structuredClone([...state.stores.opening_attempts]);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state: "complete", response: {
    persisted: true, attempt_id: "A", received_sequences: [1, 2, 3], contiguous_sequence: 3 } })));
  state.controls.beforeCommit = () => {
    expect(state.setItem).not.toHaveBeenCalled();
    return abort ? new DOMException("Confirmation aborted", "AbortError") : undefined;
  };
  if (abort) {
    await expect(state.journal.recoverOpeningEvidence()).rejects.toThrow("Confirmation aborted");
    expect([...state.stores.opening_attempts]).toEqual(saved); expect(state.stores.opening_events.size).toBe(3);
    expect(state.setItem).not.toHaveBeenCalled();
  } else {
    await expect(state.journal.recoverOpeningEvidence()).resolves.toEqual({ moreWork: false });
    expect(state.stores.opening_attempts.size).toBe(0); expect(state.stores.opening_events.size).toBe(0);
    expect(state.setItem).toHaveBeenCalledOnce();
  }
});

it("storage completion wakeups remain subscribed when BroadcastChannel construction is unavailable", async () => {
  const state = await signalingHarness(); vi.stubGlobal("BroadcastChannel", undefined);
  const listener = vi.fn(); const stop = state.journal.subscribeOpeningEvidenceAppend(listener);
  const event = new StorageEvent("storage", { key: signalKey, newValue: JSON.stringify({
    attemptId: "A", signalId: "external", sourceSessionId: "other-tab" }) });
  window.dispatchEvent(event); expect(listener).toHaveBeenCalledOnce();
  stop(); window.dispatchEvent(event); expect(listener).toHaveBeenCalledOnce();
});
