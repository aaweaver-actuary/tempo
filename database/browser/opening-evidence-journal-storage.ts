import type { OfflineDatabaseBinding } from "./offline-training-storage";
/** Complete fixed browser-journal operations; caller owns connection, policy and notifications. */
import type { OpeningDecisionEvent, OpeningEvidenceCheckpoint } from "../../app/domain/opening-evidence";
import type { SavedAttempt, SavedEvent, SavedOpeningJournal, OpeningEvidenceReceipt, CompletionSignal, PreparedOpeningAggregate, PendingOpeningReviewAttempt } from "../../app/domain/opening-evidence-journal";

export class OpeningEvidenceRebindConflict extends Error {}
/** Observed only from IDB transaction abort, never from an ambiguous completion. */
export class OpeningEvidenceAppendAborted extends Error {
  constructor(cause: unknown) { super(String(cause), { cause }); }
}
type MissingAnchorProof = { canInsert: () => boolean; registerAbort: (abort?: () => void) => void };

export const completionSignalKey = "tempo-opening-evidence-completion-v1";

export function appendOpeningAttempt(database: IDBDatabase, attempt: SavedAttempt, pendingEvents: OpeningDecisionEvent[], snapshot: OpeningEvidenceCheckpoint, binding?: OfflineDatabaseBinding, validateRebind = false, missingAnchorProof?: MissingAnchorProof): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    binding?.requireCurrent();
    let failure: unknown;
    const transaction = database.transaction(missingAnchorProof
      ? ["opening_attempts", "opening_events", "training"] : ["opening_attempts", "opening_events"], "readwrite");
    const attempts = transaction.objectStore("opening_attempts");
    const events = transaction.objectStore("opening_events");
    const prior = attempts.get(attempt.attempt_id);
    const abort = (error: unknown) => { failure = error; transaction.abort(); };
    missingAnchorProof?.registerAbort(() => { try { transaction.abort(); } catch { /* Already settled. */ } });
    const write = (saved: SavedAttempt | undefined) => {
      binding?.requireCurrent();
      attempts.put({ ...attempt,
        ...(saved?.delivery ? { delivery: saved.delivery } : {}),
        ...(["rejected", "retained"].includes(saved?.delivery_state ?? "")
          ? { delivery_state: saved?.delivery_state, rejection: saved?.rejection, retention_reason: saved?.retention_reason } : {}),
      });
      for (const event of pendingEvents) events.put({ ...event, attempt_id: snapshot.attempt_id });
    };
    prior.onsuccess = () => { try {
      binding?.requireCurrent();
      const saved = prior.result as SavedAttempt | undefined;
      if (!validateRebind) { write(saved); return; }
      const rows = events.index("attempt_id").getAll(attempt.attempt_id, 257);
      rows.onsuccess = () => { try {
        binding?.requireCurrent();
        const immutable = ["attempt_id", "manifest", "origin_queue_entry_id", "queue_entry_id", "parent_attempt_id", "started_at", "study_timezone", "source", "position_plan"] as const;
        // A missing first append has no durable session/header anchor. The server
        // attempt status proves neither local ownership nor a missing in-flight write.
        // Keep that original in memory until an owner capability can prove it.
        if (!saved && rows.result.length === 0 && missingAnchorProof?.canInsert()) {
          // A phone aggregate is in this same database. Its absence must be
          // checked atomically with the first header and event insertion.
          const aggregate = transaction.objectStore("training").get("prepared-daily-queue");
          aggregate.onsuccess = () => { try {
            binding?.requireCurrent();
            const prepared = aggregate.result as PreparedOpeningAggregate;
            if (!missingAnchorProof.canInsert() || (prepared && (!Array.isArray(prepared.attempts) ||
                prepared.attempts.some(item => !item || typeof item !== "object" ||
                  typeof item.attemptId !== "string" || item.attemptId === attempt.attempt_id))))
              throw new OpeningEvidenceRebindConflict("The original opening aggregate is uncertain; its unsaved evidence remains held.");
            write(undefined);
          } catch (error) { abort(error); } };
          return;
        }
        if (rows.result.length > 256 || !saved || (saved && (saved.owner_session_id !== attempt.owner_session_id ||
            saved.final_sequence > snapshot.events.length ||
            immutable.some(key => JSON.stringify(saved[key]) !== JSON.stringify(attempt[key])) ||
            (saved.terminal && JSON.stringify(saved.terminal) !== JSON.stringify(snapshot.terminal)) ||
            ["rejected", "retained"].includes(saved.delivery_state))))
          throw new OpeningEvidenceRebindConflict("The original opening journal changed; its unsaved evidence remains held.");
        for (const row of rows.result as SavedEvent[]) {
          const { attempt_id: _attemptId, ...event } = row;
          if (JSON.stringify(event) !== JSON.stringify(snapshot.events[event.sequence - 1]))
            throw new OpeningEvidenceRebindConflict("The original opening response sequence changed; its unsaved evidence remains held.");
        }
        write(saved);
      } catch (error) { abort(error); } };
    } catch (error) { abort(error); } };
    transaction.oncomplete = () => { missingAnchorProof?.registerAbort(); try { binding?.requireCurrent(); resolve(); } catch (error) { reject(error); } };
    // An error alone is ambiguous; only onabort proves no transaction commit.
    transaction.onerror = () => {};
    transaction.onabort = () => {
      missingAnchorProof?.registerAbort();
      reject(failure instanceof OpeningEvidenceRebindConflict ? failure : new OpeningEvidenceAppendAborted(failure ?? transaction.error));
    };
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

/** Read the complete retained position capture, including acknowledged events. */
export function readPositionOpeningJournal(database: IDBDatabase, attemptId: string): Promise<SavedOpeningJournal | undefined> {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(["opening_attempts", "opening_events"]);
    const request = transaction.objectStore("opening_attempts").get(attemptId);
    request.onsuccess = () => {
      const attempt = request.result as SavedAttempt | undefined;
      if (!attempt) { resolve(undefined); return; }
      const events = transaction.objectStore("opening_events").index("attempt_id").getAll(attemptId, 256);
      events.onsuccess = () => resolve({ attempt, events: events.result as SavedEvent[] });
      events.onerror = () => reject(events.error);
    };
    request.onerror = () => reject(request.error);
  });
}

export function rejectOpeningAttempt(database: IDBDatabase, attemptId: string, message: string, binding?: OfflineDatabaseBinding): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    binding?.requireCurrent();
    const transaction = database.transaction("opening_attempts", "readwrite");
    const store = transaction.objectStore("opening_attempts"); let failure: unknown;
    const read = store.get(attemptId);
    read.onsuccess = () => { try { binding?.requireCurrent(); if (read.result && read.result.delivery_state !== "retained") store.put({ ...read.result, delivery_state: "rejected", rejection: message }); } catch (error) { failure = error; transaction.abort(); } };
    transaction.oncomplete = () => { try { binding?.requireCurrent(); resolve(); } catch (error) { reject(error); } };
    transaction.onerror = () => reject(failure ?? transaction.error);
    transaction.onabort = () => reject(failure ?? transaction.error);
  });
}

export function removeAcknowledgedOpeningAttempt(database: IDBDatabase, attemptId: string, onRemoved: () => void, binding?: OfflineDatabaseBinding): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    binding?.requireCurrent();
    const transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
    const attempts = transaction.objectStore("opening_attempts"); let failure: unknown;
    const abort = (error: unknown) => { failure = error; transaction.abort(); };
    const read = attempts.get(attemptId);
    read.onsuccess = () => { try {
      binding?.requireCurrent();
      if (!read.result || ["rejected", "retained"].includes(read.result.delivery_state)) return;
      attempts.delete(attemptId);
      onRemoved();
      const cursor = transaction.objectStore("opening_events").index("attempt_id").openCursor(attemptId);
      cursor.onsuccess = () => { try { binding?.requireCurrent(); if (cursor.result) { cursor.result.delete(); cursor.result.continue(); } } catch (error) { abort(error); } };
    } catch (error) { abort(error); } };
    transaction.oncomplete = () => { try { binding?.requireCurrent(); resolve(); } catch (error) { reject(error); } };
    transaction.onerror = () => reject(failure ?? transaction.error);
    transaction.onabort = () => reject(failure ?? transaction.error);
  });
}

export function retainOpeningAttempt(database: IDBDatabase, snapshot: OpeningEvidenceCheckpoint, events: OpeningDecisionEvent[], header: Omit<OpeningEvidenceCheckpoint, "events">, sessionId: string, binding?: OfflineDatabaseBinding): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    binding?.requireCurrent();
    const transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
    const attempts = transaction.objectStore("opening_attempts"); let failure: unknown;
    const prior = attempts.get(snapshot.attempt_id);
    prior.onsuccess = () => { try { binding?.requireCurrent(); attempts.put({ ...prior.result, ...header, owner_session_id: prior.result?.owner_session_id ?? sessionId,
      final_sequence: events.length, delivery_state: "retained", retention_reason: "local_storage_quota" }); } catch (error) { failure = error; transaction.abort(); } };
    // Restore the full bounded envelope even if earlier checkpoints were acknowledged.
    try { for (const event of events) { binding?.requireCurrent(); transaction.objectStore("opening_events").put({ ...event, attempt_id: snapshot.attempt_id }); } }
    catch (error) { failure = error; transaction.abort(); }
    transaction.oncomplete = () => { try { binding?.requireCurrent(); resolve(); } catch (error) { reject(error); } };
    transaction.onerror = () => reject(failure ?? transaction.error);
    transaction.onabort = () => reject(failure ?? transaction.error);
  });
}

export function freezeOpeningDelivery(database: IDBDatabase, checkpoint: OpeningEvidenceCheckpoint, operationKey: string, binding?: OfflineDatabaseBinding): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    binding?.requireCurrent();
    let failure: unknown;
    const transaction = database.transaction("opening_attempts", "readwrite");
    const attempts = transaction.objectStore("opening_attempts");
    const read = attempts.get(checkpoint.attempt_id);
    read.onsuccess = () => { try { binding?.requireCurrent(); if (read.result) attempts.put({ ...read.result, delivery: { checkpoint, operationKey } });  } catch (error) { failure = error; transaction.abort(); } };
    transaction.oncomplete = () => { try { binding?.requireCurrent(); resolve(); } catch (error) { reject(error); } };
    transaction.onerror = () => reject(failure ?? transaction.error);
    transaction.onabort = () => reject(failure ?? transaction.error);
  });
}

export function confirmOpeningDelivery(database: IDBDatabase, checkpoint: OpeningEvidenceCheckpoint, receipt: OpeningEvidenceReceipt, onConfirmed: () => void, binding?: OfflineDatabaseBinding): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    binding?.requireCurrent();
    let failure: unknown;
    const transaction = database.transaction(["opening_attempts", "opening_events"], "readwrite");
    const attemptStore = transaction.objectStore("opening_attempts");
    const eventStore = transaction.objectStore("opening_events");
    const current = attemptStore.get(checkpoint.attempt_id);
    current.onsuccess = () => { try { binding?.requireCurrent();
      if (!current.result || ["rejected", "retained"].includes(current.result.delivery_state)) return;
      onConfirmed();
      if (current.result.position_plan !== undefined) {
        // Position reload needs the full original response sequence, even after
        // the server acknowledged it. Only the aggregate receipt removes it.
        attemptStore.put({ ...current.result, delivery: undefined,
          local_capture_gap: receipt.contiguous_sequence === current.result.final_sequence ? undefined : current.result.local_capture_gap,
          delivery_state: (receipt.contiguous_sequence ?? 0) < current.result.final_sequence ? "pending" : "idle" });
        return;
      }
      for (const event of checkpoint.events) if (receipt.received_sequences!.includes(event.sequence)) eventStore.delete([checkpoint.attempt_id, event.sequence]);
      const remaining = eventStore.index("attempt_id").count(checkpoint.attempt_id);
      remaining.onsuccess = () => { try { binding?.requireCurrent();
        const terminalPending = current.result.terminal?.state === "partial" &&
          JSON.stringify(current.result.terminal) !== JSON.stringify(checkpoint.terminal);
        if (checkpoint.terminal?.state === "partial" && receipt.contiguous_sequence === checkpoint.terminal.final_sequence && !remaining.result && !terminalPending) attemptStore.delete(checkpoint.attempt_id);
        else attemptStore.put({ ...current.result, delivery: undefined,
          local_capture_gap: receipt.contiguous_sequence === current.result.final_sequence ? undefined : current.result.local_capture_gap,
          delivery_state: remaining.result || terminalPending ? "pending" : "idle" });
      } catch (error) { failure = error; transaction.abort(); } };
     } catch (error) { failure = error; transaction.abort(); } };
    transaction.oncomplete = () => { try { binding?.requireCurrent(); resolve(); } catch (error) { reject(error); } };
    transaction.onerror = () => reject(failure ?? transaction.error);
    transaction.onabort = () => reject(failure ?? transaction.error);
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

export function markOrphanedOpeningAttemptPartial(database: IDBDatabase, attempt: SavedAttempt, endedAt: () => string, binding?: OfflineDatabaseBinding): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    binding?.requireCurrent();
    let failure: unknown;
    const transaction = database.transaction("opening_attempts", "readwrite");
    const store = transaction.objectStore("opening_attempts");
    const read = store.get(attempt.attempt_id);
    read.onsuccess = () => { try { binding?.requireCurrent();
      if (read.result && read.result.terminal?.state !== "partial" && !["rejected", "retained"].includes(read.result.delivery_state)) store.put({ ...read.result, delivery_state: "pending",
        terminal: { state: "partial", final_sequence: read.result.final_sequence,
          ended_at: read.result.terminal?.ended_at ?? endedAt() } });
     } catch (error) { failure = error; transaction.abort(); } };
    transaction.oncomplete = () => { try { binding?.requireCurrent(); resolve(); } catch (error) { reject(error); } };
    transaction.onerror = () => reject(failure ?? transaction.error); transaction.onabort = () => reject(failure ?? transaction.error);
  });
}

export function writeOpeningCompletionSignal(signal: CompletionSignal): void {
  localStorage.setItem(completionSignalKey, JSON.stringify(signal));
}

export function readPendingOpeningReviewAttempts(): PendingOpeningReviewAttempt[] {
  return JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1") ?? "[]") as PendingOpeningReviewAttempt[];
}
