import { API_URL } from "../const";
import { confirmOperationResponse, FailedOperationError } from "./operation-status";

export type PendingDiscoveryAdmission = {
  opportunityId: string;
  selectedMoveUci: string;
  evidenceFingerprint: string;
  operationId: string;
  intentId?: string;
  state: "pending" | "accepted" | "failed";
  error?: string;
  retryCount?: number;
  nextAttemptAt?: number;
  confirmationPollOrder?: number;
};

export const DISCOVERY_ADMISSIONS_CHANGED = "tempo-discovery-admissions-changed";
export const DISCOVERY_ADMISSION_QUEUED = "tempo-discovery-admission-queued";
const storageKey = "tempo-pending-discovery-admissions-v1";
const requestTimeoutMs = 15_000;
const retryDelayMs = 3_000;
const maximumSubmissionsPerFlush = 2;
const maximumConfirmationsPerFlush = 2;
const legacyOversizedKeyError = "Idempotency-Key must be at most 128 characters";
let activeFlush: Promise<void> | undefined;

function notifyChanged() {
  window.dispatchEvent(new Event(DISCOVERY_ADMISSIONS_CHANGED));
}

export function pendingDiscoveryAdmissions(): PendingDiscoveryAdmission[] {
  const stored = localStorage.getItem(storageKey);
  if (!stored) return [];
  const parsed: unknown = JSON.parse(stored);
  if (!Array.isArray(parsed) || parsed.some((item) =>
    !item || typeof item !== "object" ||
    typeof item.opportunityId !== "string" ||
    typeof item.selectedMoveUci !== "string" ||
    typeof item.evidenceFingerprint !== "string" ||
    (item.operationId !== undefined &&
      (typeof item.operationId !== "string" || !item.operationId ||
        (item.operationId.length > 128 &&
          !(item.state === "failed" && item.error === legacyOversizedKeyError)))) ||
    !["pending", "accepted", "failed"].includes(item.state) ||
    (item.intentId !== undefined && typeof item.intentId !== "string") ||
    (item.error !== undefined && typeof item.error !== "string") ||
    (item.retryCount !== undefined && (!Number.isInteger(item.retryCount) || item.retryCount < 0)) ||
    (item.nextAttemptAt !== undefined && !Number.isFinite(item.nextAttemptAt)) ||
    (item.confirmationPollOrder !== undefined &&
      (!Number.isSafeInteger(item.confirmationPollOrder) || item.confirmationPollOrder < 0)))) {
    throw new Error("Saved discovery requests are invalid. Restore your browser data before continuing.");
  }
  const admissions = parsed as PendingDiscoveryAdmission[];
  if (admissions.some((admission) => !admission.operationId)) {
    const upgraded = admissions.map((admission) => ({ ...admission,
      operationId: admission.operationId || crypto.randomUUID() }));
    localStorage.setItem(storageKey, JSON.stringify(upgraded));
    return upgraded;
  }
  return admissions;
}

function writeAdmissions(admissions: PendingDiscoveryAdmission[]) {
  localStorage.setItem(storageKey, JSON.stringify(admissions));
  notifyChanged();
}

function replaceAdmission(opportunityId: string, change: (admission: PendingDiscoveryAdmission) => PendingDiscoveryAdmission | null) {
  writeAdmissions(pendingDiscoveryAdmissions().flatMap((admission) => {
    if (admission.opportunityId !== opportunityId) return [admission];
    const updated = change(admission);
    return updated ? [updated] : [];
  }));
}

export function enqueuePendingDiscoveryAdmission(admission: Omit<PendingDiscoveryAdmission, "state" | "operationId">) {
  const current = pendingDiscoveryAdmissions();
  if (current.some((item) => item.opportunityId === admission.opportunityId))
    throw new Error("This discovery already has a pending save.");
  writeAdmissions([...current, { ...admission, operationId: crypto.randomUUID(), state: "pending" }]);
}

export function retryPendingDiscoveryAdmission(opportunityId: string) {
  replaceAdmission(opportunityId, (admission) => ({
    ...admission, intentId: admission.state === "failed" ? undefined : admission.intentId,
    operationId: admission.state === "failed" ? crypto.randomUUID() : admission.operationId,
    state: "pending", error: undefined, retryCount: 0, nextAttemptAt: undefined,
  }));
  return flushPendingDiscoveryAdmissions(opportunityId);
}

export function recoverUnacknowledgedDiscoveryAdmissions() {
  const admissions = pendingDiscoveryAdmissions();
  let changed = false;
  const recovered = admissions.map((admission) => {
    const oldKeyWasRejected = admission.state === "failed" &&
      admission.error === legacyOversizedKeyError;
    const oldTimeoutNeedsRecovery = admission.state === "failed" &&
      admission.error?.startsWith("Discovery save timed out after 15 seconds.");
    if (admission.state !== "accepted" && !oldTimeoutNeedsRecovery && !oldKeyWasRejected)
      return admission;
    changed = true;
    return { ...admission, intentId: undefined, state: "pending" as const,
      operationId: admission.state === "accepted" || oldKeyWasRejected
        ? crypto.randomUUID() : admission.operationId,
      error: oldKeyWasRejected ? undefined : admission.state === "failed"
        ? "Discovery save timed out after 15 seconds; confirmation is pending."
        : admission.error,
      retryCount: 0, nextAttemptAt: undefined };
  });
  if (changed) writeAdmissions(recovered);
}

async function responseError(response: Response): Promise<string> {
  const body = await response.json().catch(() => ({})) as { detail?: string };
  return body.detail ?? `Local service returned HTTP ${response.status}.`;
}

async function requestWithTimeout(url: string, options?: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), requestTimeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (cause) {
    if (controller.signal.aborted)
      throw new Error("Discovery save timed out after 15 seconds; confirmation is pending.", { cause });
    throw cause;
  } finally { window.clearTimeout(timeout); }
}

class ConfirmedSaveError extends Error {}

function retryLater(admission: PendingDiscoveryAdmission, cause: unknown) {
  const nextRetryCount = (admission.retryCount ?? 0) + 1;
  replaceAdmission(admission.opportunityId, (current) => ({ ...current,
    state: current.intentId ? "accepted" : "pending",
    error: cause instanceof Error ? cause.message : "Could not confirm the discovery save",
    retryCount: nextRetryCount,
    nextAttemptAt: Date.now() + Math.min(30_000, retryDelayMs * 2 ** (nextRetryCount - 1)),
  }));
}

async function checkedResponse(response: Response): Promise<Response> {
  try { response = await confirmOperationResponse(response); }
  catch (cause) {
    if (cause instanceof FailedOperationError) throw new ConfirmedSaveError(cause.message);
    throw cause;
  }
  if (!response.ok) {
    const message = await responseError(response);
    if (response.status < 500 && response.status !== 408 && response.status !== 429)
      throw new ConfirmedSaveError(message);
    throw new Error(message);
  }
  return response;
}

async function processAdmission(admission: PendingDiscoveryAdmission) {
  try {
    if (!admission.intentId) {
      const response = await checkedResponse(await requestWithTimeout(`${API_URL}/api/discoveries/${admission.opportunityId}/accept`, {
        method: "POST", headers: { "Content-Type": "application/json",
          "Idempotency-Key": admission.operationId },
        body: JSON.stringify({ selected_move_uci: admission.selectedMoveUci,
          evidence_fingerprint: admission.evidenceFingerprint }),
      }));
      const body = await response.json() as { intent_id?: string };
      if (!body.intent_id) throw new ConfirmedSaveError("Local service did not confirm the discovery save.");
      replaceAdmission(admission.opportunityId, (current) => ({
        ...current, intentId: body.intent_id, state: "accepted", error: undefined,
        retryCount: 0, nextAttemptAt: undefined,
      }));
      return;
    }
    const response = await checkedResponse(await requestWithTimeout(`${API_URL}/api/discovery-admissions/${admission.intentId}`));
    const body = await response.json() as { state?: string; error?: string | null };
    if (body.state === "queued") {
      replaceAdmission(admission.opportunityId, () => null);
      window.dispatchEvent(new CustomEvent(DISCOVERY_ADMISSION_QUEUED,
        { detail: { opportunityId: admission.opportunityId } }));
    } else if (body.state === "failed") {
      throw new ConfirmedSaveError(body.error ?? "Discovery admission failed. Retry the save.");
    } else if (body.state !== "preparing") {
      throw new ConfirmedSaveError("Local service returned an unknown discovery save status.");
    } else if (admission.error) {
      replaceAdmission(admission.opportunityId, (current) => ({ ...current,
        error: undefined, retryCount: 0, nextAttemptAt: undefined,
      }));
    }
  } catch (cause) {
    if (cause instanceof ConfirmedSaveError)
      replaceAdmission(admission.opportunityId, (current) => ({ ...current, state: "failed",
        error: cause.message, nextAttemptAt: undefined }));
    else retryLater(admission, cause);
  }
}

export function flushPendingDiscoveryAdmissions(preferredOpportunityId?: string): Promise<void> {
  if (activeFlush && preferredOpportunityId)
    return activeFlush.then(() => flushPendingDiscoveryAdmissions(preferredOpportunityId));
  if (!activeFlush) {
    const readyAdmissions = pendingDiscoveryAdmissions()
      .filter((admission) => admission.state !== "failed" &&
        (admission.nextAttemptAt ?? 0) <= Date.now());
    const submissions = readyAdmissions.filter((admission) => !admission.intentId)
      .sort((left, right) => Number(right.opportunityId === preferredOpportunityId) -
        Number(left.opportunityId === preferredOpportunityId))
      .slice(0, maximumSubmissionsPerFlush);
    // Poll order lives with each admission so a reload cannot favor the first entries again.
    // For N continuously eligible confirmations, each is serviced within ceil(N / 2) flushes.
    const confirmations = readyAdmissions.filter((admission) => admission.intentId)
      .sort((left, right) => (left.confirmationPollOrder ?? 0) -
        (right.confirmationPollOrder ?? 0) ||
        Number(right.opportunityId === preferredOpportunityId) -
        Number(left.opportunityId === preferredOpportunityId))
      .slice(0, maximumConfirmationsPerFlush);
    let nextPollOrder = Math.max(0, ...pendingDiscoveryAdmissions().map(
      (admission) => admission.confirmationPollOrder ?? 0));
    for (const admission of confirmations) {
      nextPollOrder += 1;
      replaceAdmission(admission.opportunityId, (current) => ({
        ...current, confirmationPollOrder: nextPollOrder,
      }));
    }
    activeFlush = Promise.all([...submissions, ...confirmations].map(processAdmission))
      .then(() => undefined).finally(() => { activeFlush = undefined; });
  }
  return activeFlush;
}
