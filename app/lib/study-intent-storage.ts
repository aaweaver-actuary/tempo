import { openIntentStore, type IntentEnvelope, type IntentRecord, type IntentStore, type TeachingFact } from "@tempo/browser-intents";
import { API_URL } from "../const";

export const legacyStudyKeys = ["tempo-pending-training-reviews-v1", "tempo-pending-training-failures-v1", "tempo-pending-teaching-states-v1", "tempo-seen-moves", "tempo-first-clean-passes"] as const;
const acknowledgementKey = "tempo-study-intents-cutover-v1";
let opening: Promise<IntentStore> | undefined;
let connection: IntentStore | undefined;
let records: IntentRecord[] = [];
let facts: TeachingFact[] = [];
let failure: unknown;
let legacyWriterObserved = false;
let factory: IDBFactory | undefined;
const listeners = new Set<() => void>();
const changed = () => { for (const listener of listeners) listener(); window.dispatchEvent(new Event("tempo:review-outbox")); };
function workspace() { return `${new URL(API_URL || "/", window.location.href).origin}:study`; }
function legacyPresent() { return legacyStudyKeys.some(key => { const value = localStorage.getItem(key); return value && value !== "[]"; }); }
function array(key: string): unknown[] {
  const value: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
  if (!Array.isArray(value)) throw new Error(`Saved study data is invalid (${key}). Keep this browser's data.`);
  return value;
}
function requireReady() {
  if (failure) throw failure;
  if (legacyWriterObserved) throw new Error("An older Tempo tab changed study data. Finish or close older tabs and reopen before continuing.");
  if (!connection) throw new Error("Durable study storage is still loading.");
}
export function immutableReviewPayload(review: Record<string, unknown>): Record<string, unknown> {
  const payload = { ...review };
  for (const field of ["state", "conflict", "reconciliationSequence", "automaticRecoverySuppressed", "evidenceRejected", "deliveryStarted"]) delete payload[field];
  return payload;
}
export function studyIntentRecords(kind?: string): IntentRecord[] { requireReady(); return records.filter(record => !kind || record.kind === kind); }
export function savedTeachingFacts(): TeachingFact[] { requireReady(); return facts; }
export function legacyStudyCutoverRequired(): boolean { return legacyWriterObserved || (legacyPresent() && localStorage.getItem(acknowledgementKey) !== "acknowledged"); }
export function studyIntentStorageReady() { return Boolean(connection) && !failure && !legacyWriterObserved; }
export function subscribeStudyIntents(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }

let refreshGeneration = 0;
export async function refreshStudyIntents() {
  const generation = ++refreshGeneration;
  if (!connection) return;
  const next = await connection.list(undefined, true);
  if (generation !== refreshGeneration) return;
  records = next;
  changed();
}
function legacyEnvelope(key: string, kind: string, payload: Record<string, unknown>, endpoint: string, requestId: string): IntentEnvelope {
  return { key, kind, attemptId: String(payload.attemptId ?? payload.operationId ?? key), requestId, endpoint,
    revision: typeof payload.expectedRevision === "number" ? payload.expectedRevision : undefined,
    createdAt: typeof payload.completedAt === "string" ? payload.completedAt : "legacy-time-unavailable", payload };
}
async function migrate(store: IntentStore) {
  // Sources remain intact. Settlement records make repeat/interrupted imports idempotent.
  for (const entry of array(legacyStudyKeys[0])) {
    if (!entry || typeof entry !== "object") throw new Error("Saved training review is invalid. Keep this browser's data.");
    const review = entry as Record<string, unknown>;
    if (typeof review.backendId !== "string" || !Number.isSafeInteger(review.queueEntryId) || !["correct", "again"].includes(String(review.outcome)) || typeof review.guided !== "boolean")
      throw new Error("Saved training review is invalid. Keep this browser's data.");
    const id = String(review.attemptId ?? `legacy-online:${review.queueEntryId}`);
    const key = `review:${id}`;
    try { await store.put(legacyEnvelope(key, "review", immutableReviewPayload({ ...review, attemptId: id }), `/api/cards/${encodeURIComponent(review.backendId)}/review`, `review-attempt:${id}`), { ...review, attemptId: id, deliveryStarted: true }); }
    catch (error) { if (error && typeof error === "object") Object.assign(error, { backendId: review.backendId, queueEntryId: review.queueEntryId, attemptId: id }); throw error; }
    if (review.state === "conflicted") await store.update(key, current => current.state === "pending" ? { state: "conflicted", data: review } : current);
  }
  for (const entry of array(legacyStudyKeys[1])) {
    const marker = typeof entry === "number" ? { queueEntryId: entry, operationId: `legacy-failure:${entry}` } : entry;
    if (!marker || typeof marker !== "object" || !("queueEntryId" in marker) || !Number.isSafeInteger(marker.queueEntryId) || !("operationId" in marker) || typeof marker.operationId !== "string")
      throw new Error("Saved training failure is invalid. Keep this browser's data.");
    const payload = marker as Record<string, unknown>;
    const key = `failure:${payload.operationId}`;
    await store.put(legacyEnvelope(key, "failure", payload, `/api/queue/entries/${payload.queueEntryId}/fail`, String(payload.operationId)), payload);
  }
  for (const entry of array(legacyStudyKeys[2])) {
    if (!entry || typeof entry !== "object" || !("cardId" in entry) || typeof entry.cardId !== "string" || !("revision" in entry) || !Number.isSafeInteger(entry.revision) || !("ply" in entry) || !Number.isSafeInteger(entry.ply))
      throw new Error("Saved teaching state is invalid. Keep this browser's data.");
    const payload = entry as Record<string, unknown>;
    const id = `${payload.cardId}:${payload.revision}:${payload.ply}`;
    await store.put(legacyEnvelope(`teaching:${id}`, "teaching", payload, `/api/cards/${encodeURIComponent(entry.cardId)}/teaching`, `teaching:${id}`), payload);
  }
  for (const entry of array(legacyStudyKeys[3])) {
    if (typeof entry !== "string") throw new Error("Saved teaching facts are invalid. Keep this browser's data.");
    const match = /^(.*):(\d+):(\d+)$/.exec(entry);
    if (match) await store.addTeachingFact({ key: entry, cardId: match[1], revision: Number(match[2]), ply: Number(match[3]), kind: "seen" });
    else await store.checkpoint(`legacy-teaching:${entry}`, { original: entry });
  }
  for (const entry of array(legacyStudyKeys[4])) {
    if (typeof entry !== "string") throw new Error("Saved first clean passes are invalid. Keep this browser's data.");
    await store.addTeachingFact({ key: `first-clean:${entry}`, cardId: entry, revision: 1, kind: "first-clean" });
  }
  await store.checkpoint("legacy-import", { complete: true, sourcesRetained: true });
}

export async function initializeStudyIntentStorage(acknowledgeOlderTabsClosed = false): Promise<IntentStore> {
  if (!connection || (globalThis.indexedDB && factory !== globalThis.indexedDB)) { closeStudyIntentStorage(); factory = globalThis.indexedDB; }
  if (acknowledgeOlderTabsClosed) { localStorage.setItem(acknowledgementKey, "acknowledged"); legacyWriterObserved = false; failure = undefined; }
  if (acknowledgeOlderTabsClosed && connection) { await migrate(connection); await refreshStudyIntents(); }
  if (legacyStudyCutoverRequired()) throw new Error("Finish or close older Tempo tabs, then confirm to recover saved study work.");
  opening ??= (async () => {
    const store = await openIntentStore({ workspace: workspace() });
    try { await migrate(store); connection = store; failure = undefined; facts = await store.teachingFacts(); await refreshStudyIntents(); }
    catch (error) { store.close(); throw error; }
    store.subscribe(() => { void refreshStudyIntents().catch(error => { failure = error; changed(); }); });
    return store;
  })().catch(error => { failure = error; opening = undefined; throw error; });
  return opening;
}
export async function studyIntentStore(): Promise<IntentStore> { const store = await initializeStudyIntentStorage(); requireReady(); return store; }
export async function putStudyIntent(envelope: IntentEnvelope, data?: unknown) {
  const store = await studyIntentStore(); const record = await store.put(envelope, data); await refreshStudyIntents(); return record;
}
export async function updateStudyIntent(key: string, change: Parameters<IntentStore["update"]>[1]) {
  const store = await studyIntentStore(); const record = await store.update(key, change); await refreshStudyIntents(); return record;
}
export async function rememberTeachingFact(fact: TeachingFact, intent?: IntentEnvelope) {
  const store = await studyIntentStore(); await store.addTeachingFact(fact, intent);
  // Incremental local projection; no historical serialization or enumeration per move.
  if (!facts.some(previous => previous.key === fact.key)) facts = [...facts, fact];
  if (intent) await refreshStudyIntents();
}
export function closeStudyIntentStorage() { connection?.close(); connection = undefined; opening = undefined; records = []; facts = []; failure = undefined; }
if (typeof window !== "undefined") window.addEventListener("storage", event => {
  if (event.key !== null && !legacyStudyKeys.includes(event.key as typeof legacyStudyKeys[number])) return;
  if (event.oldValue === event.newValue) return;
  legacyWriterObserved = true;
  try { localStorage.removeItem(acknowledgementKey); } catch { /* The gate remains closed in memory too. */ }
  changed();
});
