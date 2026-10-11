import { vi } from "vitest";

/** jsdom capability only: named exclusive callbacks queue like native Web Locks. */
export function installPrefixTransitionTestLocks() {
  const queues = new Map<string, Promise<unknown>>();
  const request = vi.fn((name: string, options: { signal?: AbortSignal; ifAvailable?: boolean; mode?: LockMode }, callback: (lock: Lock | null) => unknown) => {
    if (options.ifAvailable && queues.has(name)) return Promise.resolve().then(() => callback(null));
    const previous = queues.get(name) ?? Promise.resolve();
    const result = previous.catch(() => undefined).then(() => {
      options.signal?.throwIfAborted();
      return callback({ name, mode: options.mode ?? "exclusive" } as Lock);
    });
    const settled = result.catch(() => undefined);
    queues.set(name, settled);
    void settled.then(() => { if (queues.get(name) === settled) queues.delete(name); });
    if (!options.signal) return result;
    const signal = options.signal;
    return new Promise((resolve, reject) => {
      const cancel = () => reject(signal.reason);
      signal.addEventListener("abort", cancel, { once: true });
      result.then(resolve, reject).finally(() => signal.removeEventListener("abort", cancel));
      if (signal.aborted) cancel();
    });
  });
  const originalNavigator = navigator;
  vi.stubGlobal("navigator", new Proxy(originalNavigator, {
    get(target, property) { return property === "locks" ? { request } : Reflect.get(target, property, target); },
  }));
  return request;
}
