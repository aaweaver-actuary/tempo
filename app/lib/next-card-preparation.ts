import { createStudySession } from "@tempo/study-session";
import type { IntentEnvelope, IntentRecord, IntentStore } from "@tempo/browser-intents";
import { z } from "zod";
import { studyIntentStore, isStudyIntentStoreCurrent } from "../../database/browser/study-intent-storage";
import { LEARNING_URL, learningFetch, studyWorkspace } from "./study-connection";
import { confirmOperationResponse, PendingOperationError, readOperationResponse } from "./operation-status";

const kind = "next-card-preparation";
const currentKey = `${kind}:current`;
// Older servers have no retry metadata. Only an explicit retry may start another
// no-progress unit, at least one second after its terminal receipt was retained.
export const preparationRetryDelayMs = 1_000;
const resultSchema = z.object({ progressed: z.boolean(), pending: z.boolean(), reason: z.string().nullable() });
type PreparationResult = z.infer<typeof resultSchema>;
type Request = { authority: string; progression: string; baseUrl: string; headers: { "Idempotency-Key": string } };

export class NextCardPreparationDeferredError extends Error {
  constructor(readonly operationId: string, readonly reason: string | null) {
    super("Local Study preparation has not progressed. Your result is saved. Retry loading the queue after the preparation hold is resolved.");
    this.name = "NextCardPreparationDeferredError";
  }
}
function result(receipt: unknown): PreparationResult {
  const parsed = resultSchema.safeParse(receipt);
  if (!parsed.success) throw new Error("Local next-card preparation returned an invalid result. Recover its original operation before retrying.");
  return parsed.data;
}
function request(record: IntentEnvelope): Request {
  const value = record.payload as Request;
  if (record.kind !== kind || !record.requestId || record.key !== `${kind}:${record.requestId}` ||
      !value || typeof value.authority !== "string" || typeof value.progression !== "string" ||
      value.progression !== record.attemptId || typeof value.baseUrl !== "string" ||
      record.endpoint !== `${value.baseUrl}/api/queue/prepare-next` || value.headers?.["Idempotency-Key"] !== record.requestId)
    throw new Error("The retained preparation identity is invalid. Keep this browser's saved data.");
  return value;
}
function requireAuthority(store: IntentStore, authority: string, signal: AbortSignal) {
  if (signal.aborted) throw signal.reason;
  if (!isStudyIntentStoreCurrent(store) || studyWorkspace() !== authority)
    throw new Error("Study authority changed. Recover preparation with its original service.");
}

async function selectRequest(store: IntentStore, authority: string, progression: string, retryDeferred: boolean, signal: AbortSignal): Promise<IntentEnvelope> {
  for (;;) {
    requireAuthority(store, authority, signal);
    const pointer = await store.checkpoint(currentKey) as IntentEnvelope | undefined;
    if (pointer) {
      if (request(pointer).authority !== authority) throw new Error("Preparation belongs to another Study authority.");
      const retained = await store.get(pointer.key);
      // The checkpoint is a durable reservation. A crash/storage error before
      // insertion leaves the same immutable envelope for another tab to retain.
      if (!retained || retained.state === "pending") return pointer;
      if (retained.state !== "committed") throw new PendingOperationError(retained.requestId);
      const completed = result(retained.receipt);
      if (!completed.progressed && completed.pending) {
        const completedAt = (retained.data as { completedAt?: number } | undefined)?.completedAt;
        if (!retryDeferred || !Number.isFinite(completedAt) || Date.now() < Number(completedAt) + preparationRetryDelayMs)
          throw new NextCardPreparationDeferredError(retained.requestId, completed.reason);
      }
    }
    requireAuthority(store, authority, signal);
    const requestId = crypto.randomUUID();
    const envelope: IntentEnvelope = { key: `${kind}:${requestId}`, kind, requestId, attemptId: progression,
      endpoint: `${LEARNING_URL}/api/queue/prepare-next`, createdAt: new Date().toISOString(),
      payload: { authority, progression, baseUrl: LEARNING_URL, headers: { "Idempotency-Key": requestId } } satisfies Request };
    // IndexedDB comparison selects one identity across tabs before any POST.
    if (await store.compareCheckpoint(currentKey, pointer, envelope)) return envelope;
  }
}

/** One authority-wide unresolved unit, retaining the progression that created it. */
export async function prepareNextCard(input: { progression: string; signal: AbortSignal; retryDeferred?: boolean }): Promise<PreparationResult> {
  const store = await studyIntentStore();
  const authority = studyWorkspace();
  const envelope = await selectRequest(store, authority, input.progression, input.retryDeferred === true, input.signal);
  const frozen = request(envelope);
  const assess = (record: IntentRecord, receipt: unknown) => ({ state: "committed" as const, receipt: result(receipt),
    data: { ...record.data as object, completedAt: Date.now() } });
  const receipt = async (record: IntentRecord, signal: AbortSignal) => {
    requireAuthority(store, authority, signal);
    const response = await readOperationResponse(record.requestId, { baseUrl: frozen.baseUrl, allowMissing: true, signal });
    requireAuthority(store, authority, signal);
    return response;
  };
  const coordinator = createStudySession({ store, learning: {
    async deliver(record, deliverySignal) {
      const signal = AbortSignal.any([deliverySignal, input.signal]);
      requireAuthority(store, authority, signal);
      const started = (record.data as { started?: boolean } | undefined)?.started === true;
      await store.update(record.key, latest => ({ ...latest, data: { ...latest.data as object, started: true } }));
      requireAuthority(store, authority, signal);
      let response = started ? await receipt(record, signal) : new Response(null, { status: 404 });
      if (response.status === 404) {
        requireAuthority(store, authority, signal);
        response = await learningFetch(record.endpoint, { method: "POST", signal, headers: frozen.headers });
      }
      if (response.status === 202) {
        const operation = await response.clone().json() as { operation_id?: unknown };
        if (operation.operation_id !== record.requestId) throw new Error("The service did not confirm the original preparation operation.");
      }
      response = await confirmOperationResponse(response, { signal, baseUrl: frozen.baseUrl });
      requireAuthority(store, authority, signal);
      if (!response.ok) throw new Error(`Local next-card preparation returned HTTP ${response.status}. Recover its original operation before retrying.`);
      return assess(record, await response.json());
    },
    async checkReceipt(record, deliverySignal) {
      const signal = AbortSignal.any([deliverySignal, input.signal]);
      const response = await receipt(record, signal);
      return response.status === 404 ? undefined : assess(record, await response.json());
    },
  } });
  // This delivery session is separate from the visible review session: it must
  // not supersede that session's generation or its progression checkpoint.
  try {
    requireAuthority(store, authority, input.signal);
    const settled = await coordinator.submit(envelope);
    requireAuthority(store, authority, input.signal);
    if (settled.state !== "committed") throw new PendingOperationError(envelope.requestId);
    const completed = result(settled.receipt);
    if (!completed.progressed && completed.pending) throw new NextCardPreparationDeferredError(envelope.requestId, completed.reason);
    return completed;
  } finally { coordinator.dispose(); }
}
