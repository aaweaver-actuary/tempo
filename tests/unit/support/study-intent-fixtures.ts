import { IDBFactory, IDBObjectStore } from "fake-indexeddb";
import { vi } from "vitest";
import { closeStudyIntentStorage, initializeStudyIntentStorage } from "../../../app/lib/study-intent-storage";
import type { IntentRecord } from "@tempo/browser-intents";

/** POST-only transport fixtures explicitly model an absent receipt on recovery. */
export function withMissingReceipts<Arguments extends unknown[]>(request: (...parameters: Arguments) => Promise<Response>): typeof fetch {
  return (input, options) => String(input).includes("/api/operations/") && !options?.method
    ? Promise.resolve(new Response(null, { status: 404 })) : request(...([input, options] as unknown as Arguments));
}
export async function resetStudyStorage() {
  closeStudyIntentStorage();
  Object.defineProperty(globalThis, "indexedDB", { value: new IDBFactory(), configurable: true, writable: true });
  localStorage.clear();
  await initializeStudyIntentStorage();
}
/** Faults occur inside the new adapter's actual transaction, rather than legacy array writes. */
export function interceptIntentWrites(before: (record: IntentRecord) => void) {
  const originalAdd = IDBObjectStore.prototype.add;
  const originalPut = IDBObjectStore.prototype.put;
  const add = vi.spyOn(IDBObjectStore.prototype, "add").mockImplementation(function (this: IDBObjectStore, value, key) {
    if (this.name === "intents") before(value as IntentRecord);
    return originalAdd.call(this, value, key);
  });
  const put = vi.spyOn(IDBObjectStore.prototype, "put").mockImplementation(function (this: IDBObjectStore, value, key) {
    if (this.name === "intents") before(value as IntentRecord);
    return originalPut.call(this, value, key);
  });
  return { mockRestore() { add.mockRestore(); put.mockRestore(); } };
}

/** Drain browser-storage task turns without advancing the page's controlled clock. */
export async function settleStudyStorageTasks() {
  const { setImmediate } = await import("node:timers");
  for (let turn = 0; turn < 100; turn++) await new Promise<void>(resolve => setImmediate(resolve));
}

export async function advanceStudyTimers(milliseconds: number) {
  await settleStudyStorageTasks();
  await vi.advanceTimersByTimeAsync(milliseconds);
  await settleStudyStorageTasks();
}
