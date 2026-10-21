/** Complete fixed browser-journal operations; caller owns connection, policy and notifications. */
import type { OpeningDecisionEvent, OpeningEvidenceCheckpoint } from "../../app/domain/opening-evidence";
import type { SavedAttempt, SavedEvent, SavedOpeningJournal, OpeningEvidenceReceipt, CompletionSignal, PreparedOpeningAggregate, PendingOpeningReviewAttempt } from "../../app/domain/opening-evidence-journal";

export const completionSignalKey = "tempo-opening-evidence-completion-v1";

export function appendOpeningAttempt(database: IDBDatabase, attempt: SavedAttempt, pendingEvents: OpeningDecisionEvent[], snapshot: OpeningEvidenceCheckpoint): Promise<void> {
  return new Promise<void>((resolve, reject) => {
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
}

export function readSavedOpeningJournal(database: IDBDatabase, attemptId: string | undefined, isBlocked: (attemptId: string) => boolean): Promise<SavedOpeningJournal | undefined> {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(["opening_attempts", "opening_events"]);
    const attempts = transaction.objectStore("opening_attempts");
    const request = attemptId ? attempts.get(attemptId) : attempts.openCursor();
    request.onsuccess = () => {
      const cursor = attemptId ? undefined : request.result as IDBCursorWithValue | null;
      const attempt = (attemptId ? request.result : cursor?.value) as SavedAttempt | undefined;
      if (cursor && (attempt?.delivery_state !== "pending" || isBlocked(attempt.attempt_id))) { cursor.continue(); return; }
      if (!attempt || isBlocked(attempt.attempt_id) || attempt.delivery_state !== "pending") { resolve(undefined); return; }
      const events = transaction.objectStore("opening_events").index("attempt_id").getAll(attempt.attempt_id, 256);
      events.onsuccess = () => resolve({ attempt, events: events.result as SavedEvent[] });
      events.onerror = () => reject(events.error);
    };
    request.onerror = () => reject(request.error);
  });
}

export function rejectOpeningAttempt(database: IDBDatabase, attemptId: string, message: string): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const transaction = database.transaction("opening_attempts", "readwrite");
    const store = transaction.objectStore("opening_attempts");
    const read = store.get(attemptId);
    read.onsuccess = () => { if (read.result && read.result.delivery_state !== "retained") store.put({ ...read.result, delivery_state: "rejected", rejection: message }); };
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export function removeAcknowledgedOpeningAttempt(database: IDBDatabase, attemptId: string, onRemoved: () => void): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
    const attempts = transaction.objectStore("opening_attempts");
    const read = attempts.get(attemptId);
    read.onsuccess = () => {
      if (!read.result || ["rejected", "retained"].includes(read.result.delivery_state)) return;
      attempts.delete(attemptId);
      onRemoved();
      const cursor = transaction.objectStore("opening_events").index("attempt_id").openCursor(attemptId);
      cursor.onsuccess = () => { if (cursor.result) { cursor.result.delete(); cursor.result.continue(); } };
    };
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export function retainOpeningAttempt(database: IDBDatabase, snapshot: OpeningEvidenceCheckpoint, events: OpeningDecisionEvent[], header: Omit<OpeningEvidenceCheckpoint, "events">, sessionId: string): Promise<void> {
  return new Promise<void>((resolve, reject) => {
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
}

export function freezeOpeningDelivery(database: IDBDatabase, checkpoint: OpeningEvidenceCheckpoint, operationKey: string): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const transaction = database.transaction("opening_attempts", "readwrite");
    const attempts = transaction.objectStore("opening_attempts");
    const read = attempts.get(checkpoint.attempt_id);
    read.onsuccess = () => { if (read.result) attempts.put({ ...read.result, delivery: { checkpoint, operationKey } }); };
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export function confirmOpeningDelivery(database: IDBDatabase, checkpoint: OpeningEvidenceCheckpoint, receipt: OpeningEvidenceReceipt, onConfirmed: () => void): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
    const attemptStore = transaction.objectStore("opening_attempts");
    const eventStore = transaction.objectStore("opening_events");
    const current = attemptStore.get(checkpoint.attempt_id);
    current.onsuccess = () => {
      if (!current.result || ["rejected", "retained"].includes(current.result.delivery_state)) return;
      onConfirmed();
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

export function readRecoveryAttempt(database: IDBDatabase, attemptId: string): Promise<SavedAttempt | undefined> {
  return new Promise((resolve, reject) => {
    const request = database.transaction("opening_attempts").objectStore("opening_attempts").get(attemptId);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export function readGuardedOpeningAttempts(database: IDBDatabase, guardedIds: Set<string>, persistedAttempts: Map<string, SavedAttempt | undefined>): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const transaction = database.transaction("opening_attempts");
    const store = transaction.objectStore("opening_attempts");
    for (const attemptId of guardedIds) {
      const request = store.get(attemptId);
      request.onsuccess = () => persistedAttempts.set(attemptId, request.result);
      request.onerror = () => reject(request.error);
    }
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

export function readOpeningRecoveryCandidate(database: IDBDatabase, isEligible: (attempt: SavedAttempt) => boolean): Promise<SavedAttempt | undefined> {
  return new Promise((resolve, reject) => {
    const request = database.transaction("opening_attempts").objectStore("opening_attempts").openCursor();
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) { resolve(undefined); return; }
      const attempt = cursor.value as SavedAttempt;
      if (isEligible(attempt)) resolve(attempt);
      else cursor.continue();
    };
    request.onerror = () => reject(request.error);
  });
}

export function readPreparedOpeningAggregate(database: IDBDatabase): Promise<PreparedOpeningAggregate> {
  return new Promise<PreparedOpeningAggregate>((resolve, reject) => {
    const request = database.transaction("training").objectStore("training").get("prepared-daily-queue");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export function markOrphanedOpeningAttemptPartial(database: IDBDatabase, attempt: SavedAttempt, endedAt: () => string): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const transaction = database.transaction("opening_attempts", "readwrite");
    const store = transaction.objectStore("opening_attempts");
    const read = store.get(attempt.attempt_id);
    read.onsuccess = () => {
      if (read.result && read.result.terminal?.state !== "partial" && !["rejected", "retained"].includes(read.result.delivery_state)) store.put({ ...read.result, delivery_state: "pending",
        terminal: { state: "partial", final_sequence: read.result.final_sequence,
          ended_at: read.result.terminal?.ended_at ?? endedAt() } });
    };
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error); transaction.onabort = () => reject(transaction.error);
  });
}

export function writeOpeningCompletionSignal(signal: CompletionSignal): void {
  localStorage.setItem(completionSignalKey, JSON.stringify(signal));
}

export function readPendingOpeningReviewAttempts(): PendingOpeningReviewAttempt[] {
  return JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]") as PendingOpeningReviewAttempt[];
}
