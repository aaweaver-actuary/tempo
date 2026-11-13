/** Semantic review capabilities. Persistence and presentation owners stay behind these ports. */
import type { OpeningEvidenceCheckpoint } from "../../domain/opening-evidence";
import type { AttemptToken } from "../../domain/attempt";
import type { PracticeCard } from "../../domain/cards";

export type ReviewIdentity = Readonly<{ backendId: string; queueEntryId: number; attemptId?: string }>;
export type PreparedNextIdentity = { queue_entry_id: number; card_id: string; revision: number; cycle: number; local_date: string };
export type PreparedContinuation = PreparedNextIdentity & { attempt_failed?: boolean };
export type ReviewConflict = { code: string; message: string; retryable: boolean };
export type ReviewIntent = ReviewIdentity & {
  outcome: "again" | "correct"; guided: boolean; completedAt?: string;
  openingEvidenceCompletion?: OpeningEvidenceCheckpoint; positionStudy?: true;
  evidenceRejected?: string; evidenceFallbackReason?: "local_storage_quota";
  expectedRevision?: number; preparedNext?: PreparedNextIdentity;
  automaticRecoverySuppressed?: "failed" | "blocked"; deliveryStarted?: boolean;
  state?: "pending" | "reconciling" | "conflicted"; reconciliationSequence?: number; conflict?: ReviewConflict;
};
export type ReviewConfirmation = { persistedAttemptIds: string[]; conflictedAttemptIds: string[];
  preparedContinuations?: Record<string, PreparedContinuation> };
export type ReviewFailure = { kind: "review" | "pending" | "other"; message: string; blocked: boolean;
  classification?: "conflict" | "pending" | "transient" | "failed" | "storage";
  identity?: ReviewIdentity; operationId?: string; endpoint?: string; status?: number; code?: string; retryable?: boolean;
  confirmedBeforeFailure?: ReviewConfirmation };
export type ReviewAuthority = Readonly<{ authority: string; isCurrent(): boolean }>;
export type ReviewAttempt = Pick<AttemptToken, "attemptId" | "generation">;
export type ReviewNotice = { severity: "info" | "success" | "warning" | "error"; source: string; message: string;
  key?: string; active?: boolean; details?: Record<string, string | number | boolean | string[]> };
export type ReviewNoticeUpdate = Partial<ReviewNotice>;

export interface ReviewServices {
  reviews: {
    retain(intent: ReviewIntent): Promise<string>;
    pending(): ReviewIntent[];
    recoverable(explicitAttemptId?: string): ReviewIntent[];
    conflicted(): ReviewIntent[];
    logicalAttemptId(identity: ReviewIdentity): string;
    flush(options?: { maximum?: number; verifyReceiptFirst?: boolean; explicitRetryAttemptId?: string; deferredAttemptIds?: ReadonlySet<string>; stopAfterAttemptId?: string }): Promise<ReviewConfirmation>;
    recover(attemptId: string): Promise<ReviewConfirmation>;
  };
  authority: { capture(): Promise<ReviewAuthority> };
  progression: {
    currentAttempt(): ReviewAttempt;
    currentCard(): PracticeCard;
    setReviewSaveError(message: string): void;
    setPendingReviewError(message: string): void;
    advancePrepared(continuation: PreparedContinuation | undefined, attemptId: string, generation: number): boolean;
    startReview(attemptId: string, authority: ReviewAuthority): Promise<void>;
    retryNext(attemptId: string, authority: ReviewAuthority, acquire: (signal: AbortSignal, acquisition: number) => Promise<unknown>, stillCurrent: () => boolean, retryable: (error: unknown) => boolean): Promise<void>;
    invalidateWorkspace(): void;
  };
  diagnostics: {
    classify(error: unknown): ReviewFailure;
    reportSaveFailure(error: unknown, identity: ReviewIdentity): void;
    reportReplayFailure(error: unknown): void;
    publish(notice: ReviewNotice): string;
    update(id: string, changes: ReviewNoticeUpdate): void;
    resolve(id: string, changes?: ReviewNoticeUpdate): void;
    find(id: string): { active: boolean; details?: ReviewNotice['details'] } | undefined;
    beginTiming(kind: 'queue-reconciliation' | 'next-card-readiness' | 'review-persistence'): (cancelled?: boolean) => void;
  };
}
