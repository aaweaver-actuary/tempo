import { afterEach, expect, it, vi } from "vitest";
import { waitForOfflineShell } from "../../app/lib/offline-shell";

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
    postMessage: () => fakePort.onmessage?.({ data: { version: "tempo-static-v6", ready: true } } as MessageEvent),
  } as unknown as ServiceWorker;
  listeners.forEach((listener) => listener());
  await expect(waiting).resolves.toBeUndefined();
});
