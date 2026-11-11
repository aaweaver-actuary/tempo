import { afterEach, expect, it, vi } from "vitest";
import { OFFLINE_SHELL_VERSION, waitForOfflineShell } from "../../app/lib/offline-shell";

afterEach(() => vi.unstubAllGlobals());

it("phone preparation waits for the new service worker to control the page", async () => {
  const listeners = new Set<() => void>();
  const serviceWorkers = {
    controller: null as ServiceWorker | null,
    ready: Promise.resolve({}),
    addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
  };
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: serviceWorkers });
  class FakeMessageChannel {
    port1 = { onmessage: null as ((event: MessageEvent) => void) | null, close: vi.fn() };
    port2 = {};
    constructor() {
      fakePort = this.port1;
    }
  }
  let fakePort: FakeMessageChannel["port1"];
  vi.stubGlobal("MessageChannel", FakeMessageChannel);
  const waiting = waitForOfflineShell(500);
  await Promise.resolve();
  serviceWorkers.controller = {
    postMessage: () => fakePort.onmessage?.({ data: { version: OFFLINE_SHELL_VERSION, ready: true } } as MessageEvent),
  } as unknown as ServiceWorker;
  listeners.forEach((listener) => listener());
  await expect(waiting).resolves.toBeUndefined();
});


it.each([
  ["tempo-static-v7", true, "older Tempo app shell"],
  [OFFLINE_SHELL_VERSION, false, "offline app shell is incomplete"],
])("rejects stale or incomplete shell (%s, ready %s)", async (version, ready, message) => {
  let port: { onmessage: ((event: MessageEvent) => void) | null; close: () => void };
  vi.stubGlobal("MessageChannel", class {
    port1 = { onmessage: null, close: vi.fn() }; port2 = {};
    constructor() { port = this.port1; }
  });
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: {
    ready: Promise.resolve({}), controller: { postMessage: () => port.onmessage?.({ data: { version, ready } } as MessageEvent) },
  } });
  await expect(waitForOfflineShell(500)).rejects.toThrow(message);
});
