import type { IntentEnvelope, IntentRecord, IntentStore } from "@tempo/browser-intents";

export type SessionPhase = "active" | "persisting" | "pending" | "conflicted" | "committed" | "next-attempt-loading" | "next-load-failed" | "storage-failed";
export type SessionSnapshot = { phase: SessionPhase; key?: string; generation: number; error?: string };
export type Settlement = { state: "committed" | "conflicted" | "pending"; receipt?: unknown; data?: unknown };
export type LearningClient = { deliver(record: IntentRecord, signal: AbortSignal): Promise<Settlement> };
export type StudySession = ReturnType<typeof createStudySession>;

/** Owns delivery and progression. The view may project this state, never infer settlement. */
export function createStudySession(options: {
  store: IntentStore; learning: LearningClient; owner?: string; now?: () => number;
  requestTimeoutMs?: number; claimDurationMs?: number;
}) {
  const owner = options.owner ?? crypto.randomUUID();
  const now = options.now ?? Date.now;
  const listeners = new Set<() => void>();
  let snapshot: SessionSnapshot = { phase: "active", generation: 0 };
  let controller: AbortController | undefined;
  let disposed = false;
  const inFlight = new Map<string, Promise<Settlement>>();
  function publish(phase: SessionPhase, generation: number, key: string | undefined, error?: unknown) {
    if (disposed || snapshot.generation !== generation || snapshot.key !== key) return;
    snapshot = { phase, generation, key, ...(error ? { error: String(error) } : {}) };
    for (const listener of listeners) listener();
  }
  async function deadline<T>(action: (signal: AbortSignal) => Promise<T>, parent?: AbortSignal): Promise<T> {
    const current = new AbortController();
    const abort = () => current.abort(parent?.reason);
    parent?.addEventListener("abort", abort, { once: true });
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const cancelled = new Promise<never>((_, reject) => {
      current.signal.addEventListener("abort", () => reject(current.signal.reason ?? new Error("Study request cancelled.")), { once: true });
      timeout = setTimeout(() => current.abort(new Error("Study request timed out. Retry to recover its result.")), options.requestTimeoutMs ?? 15_000);
      if (parent?.aborted) abort();
    });
    try { return await Promise.race([action(current.signal), cancelled]); }
    finally { clearTimeout(timeout); parent?.removeEventListener("abort", abort); }
  }
  async function deliver(key: string): Promise<Settlement> {
    const generation = snapshot.generation;
    const displayedKey = snapshot.key;
    const current = await options.store.get(key);
    if (!current) throw new Error("The original completed study request is unavailable.");
    if (current.state === "committed" || current.state === "conflicted") return { state: current.state, receipt: current.receipt, data: current.data };
    if (current.state === "discarded") throw new Error("This study request was explicitly discarded.");
    const token = await options.store.claim(key, owner, now(), options.claimDurationMs ?? 60_000);
    if (!token) { if (key === displayedKey) publish("pending", generation, displayedKey); return { state: "pending" }; }
    if (key === displayedKey) publish("pending", generation, displayedKey);
    try {
      // Network authority is required even after another tab's lease expires.
      const settlement = await deadline(signal => options.learning.deliver(current, signal));
      if (settlement.state !== "pending") {
        await options.store.update(key, latest => latest.state === "committed" ? latest : ({
          state: settlement.state === "committed" ? "committed" : "conflicted",
          data: settlement.data ?? latest.data, receipt: settlement.receipt,
        }));
      }
      if (key === displayedKey) publish(settlement.state, generation, displayedKey);
      return settlement;
    } catch (error) { if (key === displayedKey) publish("pending", generation, displayedKey, error); throw error; }
    finally { await options.store.release(key, token); }
  }
  function recover(key: string): Promise<Settlement> {
    let delivery = inFlight.get(key);
    if (!delivery) { delivery = deliver(key).finally(() => { inFlight.delete(key); }); inFlight.set(key, delivery); }
    return delivery;
  }
  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    start(key: string) {
      controller?.abort(); disposed = false;
      snapshot = { phase: "active", generation: snapshot.generation + 1, key };
      for (const listener of listeners) listener();
    },
    async submit(envelope: IntentEnvelope, data?: unknown): Promise<Settlement> {
      const generation = snapshot.generation, displayedKey = snapshot.key;
      if (envelope.key === displayedKey) publish("persisting", generation, displayedKey);
      try { await options.store.put(envelope, data); }
      catch (error) { if (envelope.key === displayedKey) publish("storage-failed", generation, displayedKey, error); throw error; }
      return recover(envelope.key);
    },
    checkSave: recover,
    retrySave: recover,
    async retryNext(key: string, acquire: (signal: AbortSignal, acquisition: number) => Promise<unknown>, stillCurrent: () => boolean, retryable: (error: unknown) => boolean = () => true): Promise<void> {
      const generation = snapshot.generation, displayedKey = snapshot.key;
      const record = await options.store.get(key);
      if (!record || !["committed", "conflicted"].includes(record.state)) throw new Error("Confirm the original review before loading its next attempt.");
      await options.store.checkpoint(`progression:${key}`, { key, pending: true });
      controller?.abort(); const current = new AbortController(); controller = current;
      publish("next-attempt-loading", generation, displayedKey);
      for (let acquisition = 0; acquisition < 2; acquisition++) {
        try {
          await deadline(signal => acquire(signal, acquisition), current.signal);
          if (disposed || current.signal.aborted || !stillCurrent() || snapshot.generation !== generation) return;
          await options.store.checkpoint(`progression:${key}`, { key, pending: false });
          publish("active", generation, displayedKey); return;
        } catch (error) {
          if (disposed || current.signal.aborted || !stillCurrent() || snapshot.generation !== generation) return;
          if (acquisition === 1 || !retryable(error)) { publish("next-load-failed", generation, displayedKey, error); throw error; }
        }
      }
    },
    dispose() { disposed = true; controller?.abort(); listeners.clear(); },
  };
}
