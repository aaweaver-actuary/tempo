import { vi } from "vitest";

/** jsdom capability only: callbacks queue by name, as native cross-tab locks do. */
export function installPrefixTransitionTestLocks() {
  const queues = new Map<string, Promise<unknown>>();
  const request = vi.fn((name: string, options: { signal?: AbortSignal }, callback: () => unknown) => {
    const previous = queues.get(name) ?? Promise.resolve();
    const result = previous.catch(() => undefined).then(() => {
      options.signal?.throwIfAborted();
      return callback();
    });
    queues.set(name, result.catch(() => undefined));
    return result;
  });
  const originalNavigator = navigator;
  vi.stubGlobal("navigator", new Proxy(originalNavigator, {
    get(target, property) {
      return property === "locks" ? { request } : Reflect.get(target, property, target);
    },
  }));
  return request;
}
