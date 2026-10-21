/** Existing journal storage facts; application owns capture, replay and recovery policy. */
import type { OpeningDecisionEvent, OpeningEvidenceCheckpoint } from "./opening-evidence";

export type AttemptHeader = Omit<OpeningEvidenceCheckpoint, "events">;
export type SavedAttempt = AttemptHeader & { owner_session_id: string; final_sequence: number;
  delivery_state: "idle" | "pending" | "complete" | "rejected" | "retained"; rejection?: string;
  retention_reason?: "local_storage_quota";
  local_capture_gap?: string;
  delivery?: { checkpoint: OpeningEvidenceCheckpoint; operationKey: string } };
export type SavedEvent = OpeningDecisionEvent & { attempt_id: string };
export type CompletionSignal = { attemptId: string; signalId: string; sourceSessionId: string };
export type SavedOpeningJournal = { attempt: SavedAttempt; events: SavedEvent[] };
export type OpeningEvidenceReceipt = { persisted?: boolean; attempt_id?: string; received_sequences?: number[]; contiguous_sequence?: number };
export type PreparedOpeningAggregate = { attempts?: { attemptId?: string; serverReviewId?: number; serverAcknowledged?: boolean }[] } | undefined;
export type PendingOpeningReviewAttempt = { attemptId?: string };
