import type { IntentRecord, IntentStore } from "@tempo/browser-intents";
import type { StudyClient } from "./study-client";

export type PendingTeachingState = { cardId: string; revision: number; ply: number };
export function createTeachingDelivery(client: StudyClient, getStore: () => Promise<IntentStore>,
  operations: Pick<typeof import("./operation-status"), "readOperationResponse" | "confirmOriginalOperationResponse" | "PendingOperationError">,
  isStoreCurrent: (store: IntentStore) => boolean) {
  const { readOperationResponse, confirmOriginalOperationResponse, PendingOperationError } = operations;
  function bindAuthority() {
    const origin = client.origin, generation = client.generation;
    return (store?: IntentStore) => {
      if (client.origin !== origin || client.generation !== generation || (store && !isStoreCurrent(store)))
        throw new Error("Study authority changed. Recover the original teaching request with its original service.");
    };
  }
  function confirmedTeaching(record: IntentRecord, body: unknown) {
    const fact = body as Partial<PendingTeachingState & { taughtAt: string }>;
    const expected = record.payload as PendingTeachingState;
    if (!fact || fact.cardId !== expected.cardId || fact.revision !== expected.revision || fact.ply !== expected.ply || typeof fact.taughtAt !== "string" || !fact.taughtAt)
      throw new Error("Learning did not confirm the original teaching fact. Keep the pending request.");
    return { state: "committed" as const, receipt: body };
  }
  async function readReceipt(record: IntentRecord, signal: AbortSignal, requireCurrent: () => void) {
    requireCurrent();
    const response = await readOperationResponse(record.requestId, { allowMissing: true, signal, client });
    requireCurrent();
    if (response.status === 404) return undefined;
    const body = await response.json();
    requireCurrent();
    return confirmedTeaching(record, body);
  }
  async function checkReceipt(record: IntentRecord, signal: AbortSignal) {
    const requireAuthority = bindAuthority();
    const store = await getStore();
    requireAuthority(store);
    return readReceipt(record, signal, () => requireAuthority(store));
  }
  async function deliver(record: IntentRecord, signal: AbortSignal) {
    const requireAuthority = bindAuthority();
    const origin = client.origin;
    const store = await getStore();
    const requireCurrent = () => requireAuthority(store);
    requireCurrent();
    const state = record.payload as PendingTeachingState;
    const started = Boolean((record.data as { started?: boolean } | undefined)?.started);
    await store.update(record.key, latest => { requireCurrent(); return { ...latest, data: { started: true } }; });
    requireCurrent();
    if (started) { const receipt = await readReceipt(record, signal, requireCurrent); requireCurrent(); if (receipt) return receipt; }
    requireCurrent();
    const delivered = await client.learningFetch(`${origin}${record.endpoint}`, {
      method: "POST", signal, headers: { "Content-Type": "application/json", "Idempotency-Key": record.requestId },
      body: JSON.stringify({ revision: state.revision, ply: state.ply }),
    });
    requireCurrent();
    const response = await confirmOriginalOperationResponse(delivered, record.requestId, { signal, client });
    requireCurrent();
    if (response.status === 202) throw new PendingOperationError(record.requestId);
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { detail?: string };
      requireCurrent();
      throw new Error(body.detail ?? `Teaching save returned HTTP ${response.status}.`);
    }
    const body = await response.json();
    requireCurrent();
    return confirmedTeaching(record, body);
  }
  return { deliver, checkReceipt };
}
