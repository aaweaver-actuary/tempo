import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { evidenceCompletion, openingEvidenceStorage } from "../fixtures/opening-evidence-storage";
import type { OpeningEvidenceCheckpoint } from "../../app/domain/opening-evidence";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function backlog() {
  const storage = await openingEvidenceStorage();
  for (const id of ["A", "B", "C"]) storage.seed(evidenceCompletion(id));
  const verified: string[] = [], delivered: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-Tempo-Work-Class")).toBe("background");
    if (!init?.method) { verified.push(decodeURIComponent(url.split("/").at(-1)!)); return Response.json({}, { status: 404 }); }
    const checkpoint = JSON.parse(init.body as string) as OpeningEvidenceCheckpoint;
    delivered.push(checkpoint.attempt_id);
    return Response.json({ persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1, 2, 3], contiguous_sequence: 3 });
  }));
  return { ...storage, verified, delivered, journal: await import("../../app/lib/opening-evidence-journal") };
}

it("AS-15 opening evidence recovery processes one journal per idle slice", async () => {
  const state = await backlog();
  const originalB = structuredClone(state.stores.opening_attempts.get("B"));
  const first = state.journal.recoverOpeningEvidence();
  expect(state.journal.recoverOpeningEvidence()).toBe(first);
  const result = await first;
  expect(state.verified).toEqual(["A"]); expect(state.delivered).toEqual(["A"]);
  expect(state.stores.opening_attempts.get("B")).toEqual(originalB);
  expect(state.stores.opening_attempts.has("C")).toBe(true);
  expect(result).toEqual({ moreWork: true });
  expect(await state.journal.recoverOpeningEvidence()).toEqual({ moreWork: true });
  expect(state.delivered).toEqual(["A", "B"]);
  expect(await state.journal.recoverOpeningEvidence()).toEqual({ moreWork: false });
  expect(state.delivered).toEqual(["A", "B", "C"]);
});

it.each(["immediate", "deferred"])("AS-15 a failed recovery journal consumes one slice and yields before the next journal (%s)", async timing => {
  const state = await backlog();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/attempts/")) return Response.json({}, { status: 404 });
    if (url.includes("/operations/")) return Response.json({ state: "failed", error: { message: "Durable checkpoint failure" } });
    state.delivered.push((JSON.parse(init!.body as string) as OpeningEvidenceCheckpoint).attempt_id);
    return Response.json({ operation_id: new Headers(init?.headers).get("Idempotency-Key") }, { status: timing === "immediate" ? 500 : 202 });
  }));
  const result = await state.journal.recoverOpeningEvidence();
  expect(state.delivered).toEqual(["A"]);
  expect(state.stores.opening_attempts.get("A")).toMatchObject({ delivery_state: "rejected", rejection: "Durable checkpoint failure" });
  expect(state.stores.opening_attempts.get("B")).toMatchObject({ delivery_state: "idle", terminal: { state: "complete" } });
  expect(result).toEqual({ moreWork: true });
});

it("AS-15 foreground activity pauses remaining opening evidence recovery backlog", async () => {
  const state = await backlog();
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
  const mounted = render(<Harness />);
  const runIdle = async () => { const [id, callback] = [...callbacks][0]; callbacks.delete(id);
    await act(async () => callback({ didTimeout: false, timeRemaining: () => 50 })); };
  expect(callbacks.size).toBe(1);
  await runIdle();
  await waitFor(() => expect(state.delivered).toEqual(["A"]));
  await waitFor(() => expect(callbacks.size).toBe(1));
  act(() => useTrainingStore.getState().setAttemptPhase("opponentReplyPending"));
  expect(callbacks.size).toBe(0);
  act(() => { for (let index = 0; index < 3; index++) window.dispatchEvent(new Event("online")); });
  expect(callbacks.size).toBe(0); expect(state.delivered).toEqual(["A"]);
  act(() => useTrainingStore.getState().setAttemptPhase("playerTurn"));
  expect(callbacks.size).toBe(1); await runIdle();
  await waitFor(() => expect(state.delivered).toEqual(["A", "B"]));
  await waitFor(() => expect(callbacks.size).toBe(1));
  expect(state.stores.opening_attempts.has("C")).toBe(true);
  await runIdle(); await waitFor(() => expect(state.delivered).toEqual(["A", "B", "C"]));
  expect(callbacks.size).toBe(0);
  act(() => window.dispatchEvent(new Event("online"))); expect(callbacks.size).toBe(1);
  mounted.unmount(); expect(callbacks.size).toBe(0);
});

it("AS-15 reconnect requests share an active recovery slice without concurrent journal work", async () => {
  const state = await backlog();
  const originalFetch = fetch;
  let release!: (response: Response) => void;
  const requests = vi.fn((url: string, init?: RequestInit) => url.endsWith("/attempts/A")
    ? new Promise<Response>(resolve => { release = resolve; }) : originalFetch(url, init));
  vi.stubGlobal("fetch", requests);
  const { useTrainingStore } = await import("../../app/state/training-store");
  const { useOpeningEvidenceRecovery } = await import("../../app/hooks/use-opening-evidence-recovery");
  useTrainingStore.setState({ queueReadiness: "ready" });
  const callbacks = new Map<number, IdleRequestCallback>(); let sequence = 0;
  vi.stubGlobal("requestIdleCallback", vi.fn(callback => { callbacks.set(++sequence, callback); return sequence; }));
  vi.stubGlobal("cancelIdleCallback", vi.fn(id => callbacks.delete(id)));
  function Harness() { useOpeningEvidenceRecovery(true, true, false); return null; }
  const mounted = render(<Harness />);
  await act(async () => { const [id, callback] = [...callbacks][0]; callbacks.delete(id); callback({ didTimeout: false, timeRemaining: () => 50 }); });
  await waitFor(() => expect(requests).toHaveBeenCalledOnce());
  act(() => { for (let index = 0; index < 3; index++) window.dispatchEvent(new Event("online")); });
  mounted.rerender(<Harness />);
  const activeRecovery = state.journal.recoverOpeningEvidence();
  expect(state.journal.recoverOpeningEvidence()).toBe(activeRecovery);
  expect(requests).toHaveBeenCalledOnce(); expect(callbacks.size).toBe(0);
  await act(async () => release(Response.json({}, { status: 404 })));
  await waitFor(() => expect(state.delivered).toEqual(["A"]));
  await waitFor(() => expect(callbacks.size).toBe(1));
  mounted.unmount(); expect(callbacks.size).toBe(0);
  expect(state.stores.opening_attempts.has("B")).toBe(true);
});

it("AS-15 a live browser lease yields to later recovery journals without closing its attempt", async () => {
  const state = await backlog();
  const originalA = structuredClone(state.stores.opening_attempts.get("A"));
  vi.mocked(navigator.locks.request).mockImplementation(async (name, _options, callback) =>
    callback!(name.endsWith(":A") ? null : {} as Lock));
  expect(await state.journal.recoverOpeningEvidence()).toEqual({ moreWork: true });
  expect(state.delivered).toEqual([]); expect(state.stores.opening_attempts.get("A")).toEqual(originalA);
  await state.journal.recoverOpeningEvidence(); await state.journal.recoverOpeningEvidence();
  expect(state.delivered).toEqual(["B", "C"]);
  expect(state.stores.opening_attempts.get("A")).toEqual(originalA);
});

it("AS-15 recovery leaves completions owned by pending aggregate reviews and retained evidence untouched", async () => {
  const state = await backlog();
  state.stores.training.set("prepared-daily-queue", { attempts: [{ attemptId: "A" }] });
  state.stores.opening_attempts.set("B", { ...(state.stores.opening_attempts.get("B") as object), delivery_state: "retained" });
  expect(await state.journal.recoverOpeningEvidence()).toEqual({ moreWork: false });
  expect(state.delivered).toEqual(["C"]); expect(state.verified).toEqual(["C"]);
  expect(state.stores.opening_attempts.get("A")).toMatchObject({ terminal: { state: "complete" } });
  expect(state.stores.opening_attempts.get("B")).toMatchObject({ delivery_state: "retained" });
});

it("AS-15 live flushing requested during a recovery slice keeps its normal delivery behavior", async () => {
  const state = await backlog();
  for (const id of ["B", "C"]) state.stores.opening_attempts.set(id, {
    ...(state.stores.opening_attempts.get(id) as object), delivery_state: "pending", terminal: { state: "partial", final_sequence: 3, ended_at: "2026-10-03T12:01:00Z" } });
  const originalFetch = fetch; let release!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    if (init?.method === "POST" && JSON.parse(init.body as string).attempt_id === "A") {
      state.delivered.push("A"); return new Promise<Response>(resolve => { release = resolve; });
    }
    return originalFetch(url, init);
  }));
  const recovery = state.journal.recoverOpeningEvidence();
  await waitFor(() => expect(state.delivered).toEqual(["A"]));
  const live = state.journal.flushOpeningEvidence();
  await Promise.resolve(); expect(state.delivered).toEqual(["A"]);
  release(Response.json({ persisted: true, attempt_id: "A", received_sequences: [1, 2, 3], contiguous_sequence: 3 }));
  await Promise.all([recovery, live]);
  expect(state.verified).toEqual(["A"]); expect(state.delivered).toEqual(["A", "B", "C"]);
  expect(state.stores.opening_attempts.size).toBe(0);
});
