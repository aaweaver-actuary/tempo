import type { IntentRecord, IntentStore } from "@tempo/browser-intents";
import type { StudyClient } from "./study-client";
export type PendingTeachingState = { cardId: string; revision: number; ply: number };
export function createTeachingDelivery(client: StudyClient, getStore: () => Promise<IntentStore>, operations: Pick<typeof import("./operation-status"), "readOperationResponse" | "confirmOriginalOperationResponse" | "PendingOperationError">) {
  const { readOperationResponse, confirmOriginalOperationResponse, PendingOperationError } = operations;
async function checkReceipt(record: IntentRecord, signal: AbortSignal) {
  const response = await readOperationResponse(record.requestId, { allowMissing: true, signal, client });
  if (response.status === 404) return undefined;
  return confirmedTeaching(record, await response.json());
}
function confirmedTeaching(record: IntentRecord, body: unknown) {
  const fact = body as Partial<PendingTeachingState & { taughtAt: string }>;
  const expected = record.payload as PendingTeachingState;
  if (!fact || fact.cardId !== expected.cardId || fact.revision !== expected.revision || fact.ply !== expected.ply || typeof fact.taughtAt !== "string" || !fact.taughtAt)
    throw new Error("Learning did not confirm the original teaching fact. Keep the pending request.");
  return { state: "committed" as const, receipt: body };
}
async function deliver(record: IntentRecord, signal: AbortSignal) {
  const store = await getStore();
  const state = record.payload as PendingTeachingState;
  const started = Boolean((record.data as { started?: boolean } | undefined)?.started);
  await store.update(record.key, latest => ({ ...latest, data: { started: true } }));
  if (started) { const receipt = await checkReceipt(record, signal); if (receipt) return receipt; }
  const delivered = await client.learningFetch(`${client.origin}${record.endpoint}`, {
    method: "POST", signal, headers: { "Content-Type": "application/json", "Idempotency-Key": record.requestId },
    body: JSON.stringify({ revision: state.revision, ply: state.ply }),
  });
  const response = await confirmOriginalOperationResponse(delivered, record.requestId, { signal, client });
  if (response.status === 202) throw new PendingOperationError(record.requestId);
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail ?? `Teaching save returned HTTP ${response.status}.`);
  }
  return confirmedTeaching(record, await response.json());
}
  return { deliver, checkReceipt };
}
