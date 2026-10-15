import { openingEvidenceCheckpointSchema } from "../domain/opening-evidence";
import type { PendingReview, ReviewConflictInformation } from "./review-outbox";
import type { PreparedNextIdentity } from "./prepared-review-continuation";

export function validLegacyReview(value: unknown): value is PendingReview {
  if (!value || typeof value !== "object") return false;
  const item = value as PendingReview;
  return typeof item.backendId === "string" && !!item.backendId &&
    Number.isSafeInteger(item.queueEntryId) && item.queueEntryId > 0 &&
    ["again", "correct"].includes(item.outcome) && typeof item.guided === "boolean" &&
    (item.attemptId === undefined || (typeof item.attemptId === "string" && !!item.attemptId)) &&
    (item.completedAt === undefined || typeof item.completedAt === "string") &&
    (item.openingEvidenceCompletion === undefined || openingEvidenceCheckpointSchema.safeParse(item.openingEvidenceCompletion).success) &&
    (item.evidenceRejected === undefined || typeof item.evidenceRejected === "string") &&
    (item.evidenceFallbackReason === undefined || item.evidenceFallbackReason === "local_storage_quota") &&
    (item.expectedRevision === undefined || (Number.isSafeInteger(item.expectedRevision) && item.expectedRevision > 0)) &&
    (item.preparedNext === undefined || validPreparedIdentity(item.preparedNext)) &&
    (item.reconciliationSequence === undefined || (Number.isSafeInteger(item.reconciliationSequence) && item.reconciliationSequence > 0)) &&
    (item.automaticRecoverySuppressed === undefined || ["failed", "blocked"].includes(item.automaticRecoverySuppressed)) &&
    (item.state === undefined || ["pending", "reconciling", "conflicted"].includes(item.state)) &&
    (item.state !== "conflicted" || validConflict(item.conflict));
}
export function validConflict(value: unknown): value is ReviewConflictInformation {
  return !!value && typeof value === "object" && "code" in value && typeof value.code === "string" &&
    "message" in value && typeof value.message === "string" && "retryable" in value && typeof value.retryable === "boolean";
}
function validPreparedIdentity(value: unknown): value is PreparedNextIdentity {
  if (!value || typeof value !== "object") return false;
  const candidate = value as PreparedNextIdentity;
  return Number.isSafeInteger(candidate.queue_entry_id) && candidate.queue_entry_id > 0 &&
    typeof candidate.card_id === "string" && !!candidate.card_id && Number.isSafeInteger(candidate.revision) && candidate.revision > 0 &&
    Number.isSafeInteger(candidate.cycle) && candidate.cycle >= 0 && /^\d{4}-\d{2}-\d{2}$/.test(candidate.local_date);
}
export function validLegacyFailure(value: unknown): boolean {
  if (typeof value === "number") return Number.isSafeInteger(value) && value > 0;
  if (!value || typeof value !== "object") return false;
  const marker = value as { queueEntryId: number; operationId: string; backendId?: string; expectedRevision?: number };
  return Number.isSafeInteger(marker.queueEntryId) && marker.queueEntryId > 0 &&
    typeof marker.operationId === "string" && marker.operationId.length > 0 && marker.operationId.length <= 128 &&
    (marker.backendId === undefined || (typeof marker.backendId === "string" && !!marker.backendId)) &&
    (marker.expectedRevision === undefined || (Number.isSafeInteger(marker.expectedRevision) && marker.expectedRevision > 0));
}
