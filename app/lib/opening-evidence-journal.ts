import type { PracticeCard } from "../domain/cards";
import type { AssistanceKind, OpeningDecisionEvent, OpeningEvidenceCheckpoint } from "../domain/opening-evidence";
import { openingEvidenceCheckpointSchema, openingDecisionEventSchema } from "../domain/opening-evidence";
import { API_URL } from "../const";
import { offlineTrainingDatabase } from "./offline-training-storage";
import { confirmOperationResponse, FailedOperationError } from "./operation-status";
import { publishNotification } from "./notifications";

type AttemptHeader = Omit<OpeningEvidenceCheckpoint, "events">;
type SavedAttempt = AttemptHeader & { owner_session_id: string; final_sequence: number;
  delivery_state: "idle" | "pending" | "complete" | "rejected" | "retained"; rejection?: string;
  retention_reason?: "local_storage_quota";
  local_capture_gap?: string;
  delivery?: { checkpoint: OpeningEvidenceCheckpoint; operationKey: string } };
type SavedEvent = OpeningDecisionEvent & { attempt_id: string };
const captures = new Map<string, OpeningAttemptJournal>();
const sessionId = crypto.randomUUID();
let activeFlush: Promise<void> | undefined;
let activeRecovery: Promise<void> | undefined;
let flushTimer: ReturnType<typeof setTimeout> | undefined;
let writeTail = Promise.resolve();
const releaseLeases = new Map<string, () => void>();

function holdAttemptLease(attemptId: string): void {
  if (typeof navigator === "undefined" || !navigator.locks) return;
  void navigator.locks.request(`tempo-opening-attempt:${attemptId}`, async () => {
    await new Promise<void>((resolve) => releaseLeases.set(attemptId, resolve));
    releaseLeases.delete(attemptId);
  });
}

/** A bounded in-memory attempt; storage is an explicit asynchronous boundary. */
export class OpeningAttemptJournal {
  readonly events: OpeningDecisionEvent[] = [];
  readonly uncommittedEvents = new Map<number, OpeningDecisionEvent>();
  terminal: OpeningEvidenceCheckpoint["terminal"] = null;
  storageError: string | undefined;
  constructor(readonly header: AttemptHeader,
    private readonly append: (journal: OpeningAttemptJournal, event?: OpeningDecisionEvent) => Promise<void>) {
    this.header = structuredClone(header);
  }

  private add(decisionIndex: number, kind: OpeningDecisionEvent["kind"],
    fields: Partial<OpeningDecisionEvent> = {}): void {
    if (this.terminal) return;
    const decision = this.header.manifest.decisions[decisionIndex];
    if (!decision || this.events.length >= 256) {
      this.reportStorageError("Opening evidence reached its bounded event limit.");
      return;
    }
    const event: OpeningDecisionEvent = { sequence: this.events.length + 1, decision_index: decisionIndex,
      decision_id: decision.decision_id, expected_uci: decision.expected_uci, kind,
      observed_at: new Date().toISOString(), response_uci: null, assistance: null, disposition: null, ...fields };
    this.events.push(event);
    this.uncommittedEvents.set(event.sequence, event);
    // Initiate the local append before the board advances; never await it in input handling.
    void this.append(this, event).catch((error) => this.reportStorageError(error));
  }

  assistance(moveOffset: number, kind: AssistanceKind): void {
    const index = this.header.manifest.decisions.findIndex((decision) => decision.move_offset === moveOffset);
    if (index < 0 || this.events.some((event) => event.decision_index === index && event.kind === "assistance" && event.assistance === kind)) return;
    const answered = this.events.some((event) => event.decision_index === index && ["first_response", "manual_failure"].includes(event.kind));
    if (kind === "revealed" && answered) {
      if (!this.events.some((event) => event.decision_index === index && event.kind === "reveal")) this.add(index, "reveal");
    } else this.add(index, "assistance", { assistance: kind });
  }

  response(moveOffset: number, responseUci: string, disposition: OpeningDecisionEvent["disposition"]): void {
    const index = this.header.manifest.decisions.findIndex((decision) => decision.move_offset === moveOffset);
    if (index < 0) return;
    const prior = this.events.find((event) => event.decision_index === index && ["first_response", "manual_failure"].includes(event.kind));
    if (!prior) this.add(index, "first_response", { response_uci: responseUci, disposition });
    else if (responseUci === this.header.manifest.decisions[index].expected_uci &&
      prior.response_uci !== responseUci && !this.events.some((event) => event.decision_index === index && event.kind === "correction"))
      this.add(index, "correction", { response_uci: responseUci });
  }

  manualFailure(moveOffset: number): void {
    const index = this.header.manifest.decisions.findIndex((decision) => decision.move_offset === moveOffset);
    if (index < 0 || this.events.some((event) => event.decision_index === index && ["first_response", "manual_failure"].includes(event.kind))) return;
    this.add(index, "manual_failure");
  }

  finish(state: "partial" | "complete", endedAt = new Date().toISOString()): OpeningEvidenceCheckpoint {
    this.terminal ??= { state, final_sequence: this.events.length, ended_at: endedAt };
    void this.append(this).catch((error) => this.reportStorageError(error));
    return this.snapshot();
  }

  snapshot(): OpeningEvidenceCheckpoint {
    return structuredClone({ ...this.header, events: this.events, terminal: this.terminal });
  }

  private reportStorageError(error: unknown): void {
    this.storageError = String(error);
    publishNotification({ severity: "warning", source: "opening evidence", key: `opening-evidence:${this.header.attempt_id}`,
      message: `Opening evidence could not be saved locally. Keep this page open and retry after restoring browser storage. Normal review remains available. ${String(error)}` });
  }
}

async function storeAppend(journal: OpeningAttemptJournal, event?: OpeningDecisionEvent): Promise<void> {
  const snapshot = journal.snapshot();
  const attempt: SavedAttempt = { ...snapshot, owner_session_id: sessionId, final_sequence: snapshot.events.length,
    local_capture_gap: journal.storageError,
    delivery_state: event || snapshot.terminal?.state === "partial" ? "pending" : "idle" };
  delete (attempt as Partial<OpeningEvidenceCheckpoint>).events;
  const pendingEvents = [...journal.uncommittedEvents.values()];
  const writing = writeTail.catch(() => undefined).then(async () => {
    const database = await offlineTrainingDatabase();
    await new Promise<void>((resolve, reject) => {
      const transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
      const attempts = transaction.objectStore("opening_attempts");
      const prior = attempts.get(attempt.attempt_id);
      prior.onsuccess = () => attempts.put({ ...attempt,
        ...(prior.result?.delivery ? { delivery: prior.result.delivery } : {}),
        ...(["rejected", "retained"].includes(prior.result?.delivery_state)
          ? { delivery_state: prior.result.delivery_state, rejection: prior.result.rejection,
            retention_reason: prior.result.retention_reason } : {}),
      });
      // Normally one row; retries also repair this attempt's failed local appends.
      for (const pendingEvent of pendingEvents) transaction.objectStore("opening_events").put({ ...pendingEvent, attempt_id: snapshot.attempt_id });
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
    for (const pendingEvent of pendingEvents) journal.uncommittedEvents.delete(pendingEvent.sequence);
    if (!journal.uncommittedEvents.size) journal.storageError = undefined;
    if (snapshot.terminal) releaseLeases.get(snapshot.attempt_id)?.();
    if (snapshot.terminal?.state === "partial" && captures.get(snapshot.attempt_id) === journal)
      captures.delete(snapshot.attempt_id);
    if (attempt.delivery_state === "pending") scheduleOpeningEvidenceFlush();
  });
  writeTail = writing;
  return writing;
}

export function beginOpeningAttempt(card: PracticeCard, attemptId: string | undefined,
  options: { offline?: boolean; studyTimezone?: string } = {}): OpeningAttemptJournal | undefined {
  if (attemptId && card.kind === "opening" && card.openingEvidenceDiagnostic) publishNotification({
    severity: "warning", source: "opening evidence", key: `opening-evidence-unavailable:${card.id}`,
    message: card.openingEvidenceDiagnostic,
  });
  if (!attemptId || card.kind !== "opening" || !card.openingDecisionManifest || !card.queueEntryId || typeof indexedDB === "undefined") return;
  const existing = captures.get(attemptId);
  if (existing) return existing;
  let timezone = options.studyTimezone;
  try { if (timezone && timezone !== "local") new Intl.DateTimeFormat("en", { timeZone: timezone }); else timezone = undefined; }
  catch { timezone = undefined; }
  const header: AttemptHeader = { attempt_id: attemptId, manifest: card.openingDecisionManifest,
    origin_queue_entry_id: card.openingEvidenceOriginEntryId ?? card.queueEntryId,
    queue_entry_id: card.openingEvidenceParentAttemptId ? null : card.queueEntryId,
    parent_attempt_id: card.openingEvidenceParentAttemptId ?? null, started_at: new Date().toISOString(),
    study_timezone: timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
    source: options.offline ? "offline" : card.queueAttemptState === "reinforcement" ? "reinforcement" : "live", terminal: null };
  const journal = new OpeningAttemptJournal(header, storeAppend);
  captures.set(attemptId, journal);
  holdAttemptLease(attemptId);
  return journal;
}

export function partialOpeningAttempt(attemptId: string | undefined): void {
  if (!attemptId) return;
  const journal = captures.get(attemptId);
  if (journal && !journal.terminal && journal.events.length) journal.finish("partial");
  // Keep failed partial appends reachable until their local transaction commits.
  if (!journal?.events.length || journal.terminal?.state === "complete") captures.delete(attemptId);
  if (!journal?.events.length) releaseLeases.get(attemptId)?.();
}

export function completeOpeningAttempt(attemptId: string | undefined, endedAt?: string): OpeningEvidenceCheckpoint | undefined {
  return attemptId ? captures.get(attemptId)?.finish("complete", endedAt) : undefined;
}

function scheduleOpeningEvidenceFlush(): void {
  flushTimer ??= setTimeout(() => {
    flushTimer = undefined;
    void flushOpeningEvidence().catch((error) => publishNotification({ severity: "warning", source: "opening evidence",
      key: "opening-evidence-delivery", message: `Opening evidence remains saved for retry. Normal training continues. ${String(error)}` }));
  }, 0);
}

async function savedJournal(): Promise<{ attempt: SavedAttempt; events: SavedEvent[] } | undefined> {
  const database = await offlineTrainingDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(["opening_attempts", "opening_events"]);
    const request = transaction.objectStore("opening_attempts").index("delivery_state").get("pending");
    request.onsuccess = () => {
      const attempt = request.result as SavedAttempt | undefined;
      if (!attempt) { resolve(undefined); return; }
      const events = transaction.objectStore("opening_events").index("attempt_id").getAll(attempt.attempt_id, 256);
      events.onsuccess = () => resolve({ attempt, events: events.result as SavedEvent[] });
      events.onerror = () => reject(events.error);
    };
    request.onerror = () => reject(request.error);
  });
}

export async function rejectOpeningEvidence(attemptId: string, message: string): Promise<void> {
  if (typeof indexedDB !== "undefined") {
    const database = await offlineTrainingDatabase();
    await new Promise<void>((resolve, reject) => {
      const transaction = database.transaction("opening_attempts", "readwrite");
      const store = transaction.objectStore("opening_attempts");
      const read = store.get(attemptId);
      read.onsuccess = () => { if (read.result && read.result.delivery_state !== "retained") store.put({ ...read.result, delivery_state: "rejected", rejection: message }); };
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  }
  publishNotification({ severity: "warning", source: "opening evidence", key: `opening-evidence:${attemptId}`,
    message: `Opening evidence was rejected and retained for diagnosis. The aggregate review can still save. ${message}` });
}

export async function acknowledgeOpeningReview(attemptId: string): Promise<void> {
  if (typeof indexedDB === "undefined") return;
  await writeTail.catch(() => undefined);
  const database = await offlineTrainingDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
    const attempts = transaction.objectStore("opening_attempts");
    const read = attempts.get(attemptId);
    read.onsuccess = () => {
      if (!read.result || ["rejected", "retained"].includes(read.result.delivery_state)) return;
      attempts.delete(attemptId);
      const cursor = transaction.objectStore("opening_events").index("attempt_id").openCursor(attemptId);
      cursor.onsuccess = () => { if (cursor.result) { cursor.result.delete(); cursor.result.continue(); } };
    };
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
  captures.delete(attemptId);
}

/** Local capacity fallback is diagnostic retention, not a server rejection. */
export function retainOpeningEvidenceForStorageFallback(completion: OpeningEvidenceCheckpoint): Promise<void> {
  const snapshot = structuredClone(completion);
  const retaining = writeTail.catch(() => undefined).then(async () => {
    if (typeof indexedDB === "undefined") throw new Error("IndexedDB is unavailable");
    const database = await offlineTrainingDatabase();
    const { events, ...header } = snapshot;
    await new Promise<void>((resolve, reject) => {
      const transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
      const attempts = transaction.objectStore("opening_attempts");
      const prior = attempts.get(snapshot.attempt_id);
      prior.onsuccess = () => attempts.put({ ...prior.result, ...header, owner_session_id: prior.result?.owner_session_id ?? sessionId,
        final_sequence: events.length, delivery_state: "retained", retention_reason: "local_storage_quota" });
      // Restore the full bounded envelope even if earlier checkpoints were acknowledged.
      for (const event of events) transaction.objectStore("opening_events").put({ ...event, attempt_id: snapshot.attempt_id });
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
    publishNotification({ severity: "warning", source: "opening evidence", key: `opening-evidence:${snapshot.attempt_id}`,
      message: "Opening evidence could not fit in review storage and is retained separately for diagnosis. The aggregate review can still save." });
  });
  writeTail = retaining;
  return retaining;
}

async function deliverOpeningEvidence(): Promise<void> {
  if (typeof indexedDB === "undefined" || (typeof navigator !== "undefined" && !navigator.onLine)) return;
  await writeTail.catch(() => undefined);
  for (;;) {
    const saved = await savedJournal();
    if (!saved) return;
    const frozenDelivery = saved.attempt.delivery;
    const header = openingEvidenceCheckpointSchema.omit({ events: true }).parse(Object.fromEntries(
      Object.entries(saved.attempt).filter(([key]) => !["owner_session_id", "final_sequence", "delivery_state", "rejection", "retention_reason", "delivery", "local_capture_gap"].includes(key)),
    ));
    const events = saved.events.map(event => openingDecisionEventSchema.parse(
      Object.fromEntries(Object.entries(event).filter(([key]) => key !== "attempt_id")),
    )).sort((left, right) => left.sequence - right.sequence);
    const checkpoint = frozenDelivery?.checkpoint ?? openingEvidenceCheckpointSchema.parse({ ...header, events,
      terminal: header.terminal?.state === "complete" ? null : header.terminal });
    const identity = `${checkpoint.attempt_id}:${events.map((event) => event.sequence).join(",")}:${checkpoint.terminal ? "partial" : "active"}`;
    // Fixed-size delivery key, with a digest of the exact immutable envelope.
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(checkpoint)));
    const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    const operationKey = frozenDelivery?.operationKey ?? `opening-checkpoint:${hash}`;
    if (!frozenDelivery) {
      // Commit the exact delivery before sending. Later appends cannot alter an ambiguous retry.
      const database = await offlineTrainingDatabase();
      await new Promise<void>((resolve, reject) => {
        const transaction = database.transaction("opening_attempts", "readwrite");
        const attempts = transaction.objectStore("opening_attempts");
        const read = attempts.get(checkpoint.attempt_id);
        read.onsuccess = () => { if (read.result) attempts.put({ ...read.result, delivery: { checkpoint, operationKey } }); };
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      });
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15_000);
    let response: Response;
    try {
      response = await confirmOperationResponse(await fetch(`${API_URL}/api/opening-evidence/checkpoints`, {
        method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": operationKey,
          "X-Tempo-Work-Class": "background" },
        body: JSON.stringify(checkpoint), signal: controller.signal,
      }));
    } catch (error) {
      if (error instanceof FailedOperationError) { await rejectOpeningEvidence(checkpoint.attempt_id, error.message); continue; }
      throw error;
    } finally { clearTimeout(timeout); }
    if (!response.ok) {
      if (response.status === 409 || response.status === 422) {
        await rejectOpeningEvidence(checkpoint.attempt_id, await response.text());
        continue;
      }
      throw new Error(`Opening checkpoint ${identity} returned HTTP ${response.status}.`);
    }
    const receipt = await response.json() as { persisted?: boolean; attempt_id?: string; received_sequences?: number[]; contiguous_sequence?: number };
    if (!receipt.persisted || receipt.attempt_id !== checkpoint.attempt_id || !Array.isArray(receipt.received_sequences))
      throw new Error("Opening evidence persistence was not confirmed.");
    const database = await offlineTrainingDatabase();
    await new Promise<void>((resolve, reject) => {
      const transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
      const attemptStore = transaction.objectStore("opening_attempts");
      const eventStore = transaction.objectStore("opening_events");
      const current = attemptStore.get(checkpoint.attempt_id);
      current.onsuccess = () => {
        if (!current.result || ["rejected", "retained"].includes(current.result.delivery_state)) return;
        for (const event of checkpoint.events) if (receipt.received_sequences!.includes(event.sequence)) eventStore.delete([checkpoint.attempt_id, event.sequence]);
        const remaining = eventStore.index("attempt_id").count(checkpoint.attempt_id);
        remaining.onsuccess = () => {
          const terminalPending = current.result.terminal?.state === "partial" &&
            JSON.stringify(current.result.terminal) !== JSON.stringify(checkpoint.terminal);
          if (checkpoint.terminal?.state === "partial" && receipt.contiguous_sequence === checkpoint.terminal.final_sequence && !remaining.result && !terminalPending) attemptStore.delete(checkpoint.attempt_id);
          else attemptStore.put({ ...current.result, delivery: undefined,
            local_capture_gap: receipt.contiguous_sequence === current.result.final_sequence ? undefined : current.result.local_capture_gap,
            delivery_state: remaining.result || terminalPending ? "pending" : "idle" });
        };
      };
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  }
}

export function flushOpeningEvidence(): Promise<void> {
  activeFlush ??= deliverOpeningEvidence().finally(() => { activeFlush = undefined; });
  return activeFlush;
}

/** Claim only sessions whose browser lease has ended; another tab's board stays active. */
async function recoverSavedOpeningEvidence(): Promise<void> {
  if (typeof indexedDB === "undefined" || typeof navigator === "undefined" || !navigator.locks) return;
  await writeTail.catch(() => undefined);
  for (const journal of captures.values()) {
    if (journal.storageError) await storeAppend(journal);
  }
  const database = await offlineTrainingDatabase();
  const attempts = await new Promise<SavedAttempt[]>((resolve, reject) => {
    const request = database.transaction("opening_attempts").objectStore("opening_attempts").getAll();
    request.onsuccess = () => resolve(request.result as SavedAttempt[]);
    request.onerror = () => reject(request.error);
  });
  const pendingReviews = JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]") as { attemptId?: string }[];
  const prepared = await new Promise<{ attempts?: { attemptId?: string; serverReviewId?: number; serverAcknowledged?: boolean }[] } | undefined>((resolve, reject) => {
    const request = database.transaction("training").objectStore("training").get("prepared-daily-queue");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  const pendingAggregateIds = new Set([
    ...pendingReviews.map(review => review.attemptId),
    ...(prepared?.attempts ?? []).filter(attempt => !attempt.serverReviewId && !attempt.serverAcknowledged)
      .map(attempt => attempt.attemptId),
  ]);
  for (const attempt of attempts) {
    if (attempt.local_capture_gap) publishNotification({ severity: "warning", source: "opening evidence",
      key: `opening-evidence:${attempt.attempt_id}`, message: `A previous local capture reported a gap. Saved evidence remains recoverable; normal review is available. ${attempt.local_capture_gap}` });
    if (attempt.owner_session_id === sessionId || attempt.terminal?.state === "partial" || !attempt.final_sequence || ["rejected", "retained"].includes(attempt.delivery_state) ||
      (attempt.terminal?.state === "complete" && pendingAggregateIds.has(attempt.attempt_id))) continue;
    await navigator.locks.request(`tempo-opening-attempt:${attempt.attempt_id}`, { ifAvailable: true }, async (lease) => {
      if (!lease) return;
      if (attempt.terminal?.state === "complete") {
        // A crash between sealing and saving the aggregate outbox leaves observed
        // work, not an invented review. Check persisted completion before reclaiming it.
        const response = await fetch(`${API_URL}/api/opening-evidence/attempts/${encodeURIComponent(attempt.attempt_id)}`);
        if (response.ok) {
          const persisted = await response.json() as { state?: string };
          if (persisted.state === "complete") { await acknowledgeOpeningReview(attempt.attempt_id); return; }
        } else if (response.status !== 404) throw new Error("Could not verify an orphaned opening completion. Its journal remains saved for recovery.");
      }
      await new Promise<void>((resolve, reject) => {
        const transaction = database.transaction("opening_attempts", "readwrite");
        const store = transaction.objectStore("opening_attempts");
        const read = store.get(attempt.attempt_id);
        read.onsuccess = () => {
          if (read.result && read.result.terminal?.state !== "partial") store.put({ ...read.result, delivery_state: "pending",
            terminal: { state: "partial", final_sequence: read.result.final_sequence,
              ended_at: read.result.terminal?.ended_at ?? new Date().toISOString() } });
        };
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      });
    });
  }
  await flushOpeningEvidence();
}

export function recoverOpeningEvidence(): Promise<void> {
  activeRecovery ??= recoverSavedOpeningEvidence().finally(() => { activeRecovery = undefined; });
  return activeRecovery;
}
