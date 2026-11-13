import type { IntentRecord, IntentStore } from "@tempo/browser-intents";
import type { LearningClient } from "@tempo/study-session";

/** Every persisted kind has one explicit delivery or preservation disposition. */
export const STUDY_INTENT_KINDS = ["review", "teaching", "study-command", "tactic-command",
  "burial-command", "settings-command", "defense-command", "endgame-command"] as const;
export type StudyIntentKind = typeof STUDY_INTENT_KINDS[number];
export type StudyAdapterEntry =
  | { kind: StudyIntentKind; disposition: "deliver"; adapter: LearningClient }
  | { kind: StudyIntentKind; disposition: "held"; reason: string };
export type StudyRuntimeOptions = {
  store: () => Promise<IntentStore>;
  isCurrent: (store: IntentStore) => boolean;
  adapters: readonly StudyAdapterEntry[];
};

/** The original record remains inspectable and its identity is never replaced. */
export class HeldStudyIntentError extends Error {
  readonly key: string;
  readonly kind: string;
  readonly requestId: string;
  readonly attemptId: string;
  readonly endpoint: string;
  readonly revision: number | undefined;
  constructor(record: IntentRecord, reason: string) {
    super(`Saved ${record.kind} request ${record.requestId} is held: ${reason}`);
    this.name = "HeldStudyIntentError";
    this.key = record.key; this.kind = record.kind; this.requestId = record.requestId;
    this.attemptId = record.attemptId; this.endpoint = record.endpoint; this.revision = record.revision;
  }
}
