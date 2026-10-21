import { setImmediate } from "node:timers";
import { vi } from "vitest";
import type { IntentEnvelope } from "@tempo/browser-intents";
import * as storage from "../../../database/browser/study-intent-storage";
import * as commands from "../../../app/lib/study-command-client";

export function deferred<Value>() {
  let resolve!: (value: Value) => void;
  const promise = new Promise<Value>(settle => { resolve = settle; });
  return { promise, resolve };
}

export function observe<Value>(operation: Promise<Value>) {
  return operation.then(value => ({ status: "fulfilled" as const, value }), reason => ({ status: "rejected" as const, reason }));
}

export function observeRecoveryPasses() {
  const outcomes: ReturnType<typeof observe<void>>[] = [];
  const recover = commands.recoverStudyRequests;
  const spy = vi.spyOn(commands, "recoverStudyRequests").mockImplementation(signal => {
    const operation = recover(signal); outcomes.push(observe(operation)); return operation;
  });
  return { outcomes, restore: () => spy.mockRestore() };
}

/** The real acquisition returns in both implementations, including a store-only cache hit. */
export async function enterRecovery<Value>(start: () => Promise<Value>, trace: string[], name: string) {
  const acquired = deferred<void>();
  const acquire = storage.studyIntentStore;
  const acquisition = vi.spyOn(storage, "studyIntentStore").mockImplementation(async () => {
    const store = await acquire();
    acquired.resolve();
    return store;
  });
  const outcome = observe(start());
  try {
    await acquired.promise;
    // One task boundary places the caller's continuation after the observed acquisition.
    // The acquisition witness, rather than elapsed time, establishes entry on the baseline.
    await new Promise<void>(resolve => setImmediate(resolve));
    trace.push(`${name}:storage-acquired`);
    return { outcome };
  } finally { acquisition.mockRestore(); }
}

export function recoveryCommand(identity = "original"): IntentEnvelope {
  const scope = "study:ownership:exercise:review:3";
  return { key: `${scope}:${identity}`, kind: "study-command", requestId: identity, attemptId: `attempt-${identity}`,
    endpoint: "/api/studies/ownership/exercises/exercise/attempts", revision: 3, createdAt: "2026-10-15T16:00:00Z",
    payload: { scope, headers: { "Content-Type": "application/json", "Idempotency-Key": identity }, body: {
      attempt_id: `attempt-${identity}`, revision: 3, context: "review", card_id: "original-card", queue_entry_id: 41,
      answer: { type: "choice", option_ids: ["a"], displayed_order: ["a", "b"] },
    } } };
}

export function completedRecoveryReceipt(command: IntentEnvelope) {
  return { operation_id: command.requestId, state: "complete", response: { attempt_id: command.attemptId,
    assessment: { outcome: "correct", feedback: "Original result confirmed" }, rating: "correct" } };
}
