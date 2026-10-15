import { IntentStorageError, type IntentEnvelope, type IntentRecord, type IntentStore } from "@tempo/browser-intents";

export type SessionPhase = "active" | "persisting" | "pending" | "conflicted" | "committed" | "next-attempt-loading" | "next-load-failed" | "storage-failed";
export type SessionSnapshot = { phase: SessionPhase; key?: string; generation: number; error?: string };
export type Settlement = { state: "committed" | "conflicted" | "pending"; receipt?: unknown; data?: unknown };
export type LearningClient = {
  deliver(record: IntentRecord, signal: AbortSignal): Promise<Settlement>;
  checkReceipt?(record: IntentRecord, signal: AbortSignal): Promise<Settlement | undefined>;
};
export type StudySession = ReturnType<typeof createStudySession>;

/** Owns delivery and progression. The view may project this state, never infer settlement. */
export function createStudySession(options: {
  store: IntentStore; learning: LearningClient; owner?: string; now?: () => number;
  requestTimeoutMs?: number; claimDurationMs?: number; identity?: () => string;
}) {
  const owner = options.owner ?? (options.identity ?? (() => crypto.randomUUID()))();
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
    if (current.state === "committed" || current.state === "conflicted") {
      if (key === displayedKey) publish(current.state, generation, displayedKey);
      return { state: current.state, receipt: current.receipt, data: current.data };
    }
    if (current.state === "discarded") throw new Error("This study request was explicitly discarded.");
    const token = await options.store.claim(key, owner, now(), options.claimDurationMs ?? 60_000);
    if (!token) {
      // A crashed tab's unexpired delivery lease must not block read-only receipt recovery.
      const receipt = options.learning.checkReceipt ? await deadline(signal => options.learning.checkReceipt!(current, signal)) : undefined;
      if (receipt && receipt.state !== "pending") {
        await options.store.update(key, latest => latest.state === "committed" ? latest : ({ ...latest, state: receipt.state, receipt: receipt.receipt, data: receipt.data ?? latest.data }));
        if (key === displayedKey) publish(receipt.state, generation, displayedKey);
        return receipt;
      }
      if (key === displayedKey) publish("pending", generation, displayedKey);
      return { state: "pending" };
    }
    if (key === displayedKey) publish("pending", generation, displayedKey);
    try {
      // Network authority is required even after another tab's lease expires.
      const settlement = await deadline(signal => options.store.get(key).then(latest => { if (!latest) throw new Error("The original study request is unavailable."); return options.learning.deliver(latest, signal); }));
      if (settlement.state !== "pending") {
        await options.store.update(key, latest => latest.state === "committed" ? latest : ({
          state: settlement.state === "committed" ? "committed" : "conflicted",
          data: settlement.data ?? latest.data, receipt: settlement.receipt,
        }));
      }
      if (key === displayedKey) publish(settlement.state, generation, displayedKey);
      return settlement;
    } catch (error) { if (key === displayedKey) publish(error instanceof IntentStorageError ? "storage-failed" : "pending", generation, displayedKey, error); throw error; }
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
      disposed = false;
      snapshot = { phase: "active", generation: snapshot.generation + 1, key };
      for (const listener of listeners) listener();
    },
    async submit(envelope: IntentEnvelope, data?: unknown, submission: { deliver?: boolean } = {}): Promise<Settlement> {
      const generation = snapshot.generation, displayedKey = snapshot.key;
      if (envelope.key === displayedKey) publish("persisting", generation, displayedKey);
      let retained: IntentRecord;
      try { retained = await options.store.put(envelope, data); }
      catch (error) { if (envelope.key === displayedKey) publish("storage-failed", generation, displayedKey, error); throw error; }
      if (submission.deliver === false) {
        if (retained.state === "discarded") throw new Error("This study request was explicitly discarded.");
        const state = retained.state === "committed" || retained.state === "conflicted" ? retained.state : "pending";
        if (envelope.key === displayedKey) publish(state, generation, displayedKey);
        return { state, receipt: retained.receipt, data: retained.data };
      }
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
          // Acquisition may install the next attempt and advance the presentation generation.
          // Its successful return still resolves the original progression checkpoint.
          if (disposed || !stillCurrent()) return;
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
