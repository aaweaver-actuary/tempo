import { createStudySession, type LearningClient } from "@tempo/study-session";
import { studyIntentStore } from "./study-intent-storage";

// The composition registers protocol adapters; this is the only session/settlement owner.
const adapters = new Map<string, LearningClient>();
let currentStore: Awaited<ReturnType<typeof studyIntentStore>> | undefined;
let session: ReturnType<typeof createStudySession> | undefined;
export function registerStudyDeliveryAdapter(kind: string, deliver: LearningClient["deliver"], checkReceipt?: LearningClient["checkReceipt"]) { adapters.set(kind, { deliver, checkReceipt }); }
export async function studySession() {
  const store = await studyIntentStore();
  if (store !== currentStore) {
    session?.dispose(); currentStore = store;
    session = createStudySession({ store, learning: { deliver(record, signal) {
      const adapter = adapters.get(record.kind);
      if (!adapter) throw new Error(`The saved ${record.kind} request has no installed delivery adapter. Keep this browser's data.`);
      return adapter.deliver(record, signal);
    }, checkReceipt(record, signal) { return adapters.get(record.kind)?.checkReceipt?.(record, signal) ?? Promise.resolve(undefined); } } });
  }
  return session!;
}
