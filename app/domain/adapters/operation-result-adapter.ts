import type { StudyOperationFailure, StudyOperationReceiptWire, StudyOperationResult } from "../transport";

function failure(value: unknown): StudyOperationFailure | undefined | null {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "object" || Array.isArray(value)) return null;
  const error = value as Record<string, unknown>;
  if ((error.message !== undefined && typeof error.message !== "string") ||
      (error.status_code !== undefined && (typeof error.status_code !== "number" || !Number.isInteger(error.status_code))) ||
      (error.code !== undefined && typeof error.code !== "string") ||
      (error.retryable !== undefined && typeof error.retryable !== "boolean")) return null;
  return error as StudyOperationFailure;
}

/** Decode an untrusted receipt without changing its original operation identity. */
export function decodeOperationResult(value: unknown, operationId: string, requireIdentity: boolean): StudyOperationResult {
  const malformed = (): StudyOperationResult => ({ kind: "malformed", operationId });
  if (!value || typeof value !== "object" || Array.isArray(value)) return malformed();
  const receipt = value as StudyOperationReceiptWire;
  if ((receipt.operation_id !== undefined || requireIdentity) && receipt.operation_id !== operationId)
    return { kind: "malformed", operationId, reason: "identity" };
  switch (receipt.state) {
    case "queued": case "executing": case "retrying": case "pending": return { kind: "pending", operationId };
    case "unknown": return { kind: "unknown", operationId };
    case "complete": return Object.hasOwn(receipt, "response") && receipt.response !== undefined
      ? { kind: "complete", operationId, response: receipt.response } : malformed();
    case "failed": {
      const error = failure(receipt.error);
      return error === null ? malformed() : { kind: "failed", operationId, error: error ?? {} };
    }
    case "blocked": {
      const error = failure(receipt.last_error);
      if (error === null || (receipt.message !== undefined && typeof receipt.message !== "string")) return malformed();
      return { kind: "blocked", operationId, error: error?.message !== undefined || receipt.message === undefined
        ? error : { ...error, message: receipt.message as string } };
    }
    default: return malformed();
  }
}
