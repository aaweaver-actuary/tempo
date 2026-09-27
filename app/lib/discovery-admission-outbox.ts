import { API_URL } from "../const";
import { confirmOperationResponse } from "./operation-status";

export type PendingDiscoveryAdmission = {
  opportunityId: string;
  selectedMoveUci: string;
  evidenceFingerprint: string;
  intentId?: string;
  state: "pending" | "accepted" | "failed";
  error?: string;
  retryCount?: number;
  nextAttemptAt?: number;
};

export const DISCOVERY_ADMISSIONS_CHANGED = "tempo-discovery-admissions-changed";
export const DISCOVERY_ADMISSION_QUEUED = "tempo-discovery-admission-queued";
const storageKey = "tempo-pending-discovery-admissions-v1";
const requestTimeoutMs = 15_000;
const retryDelayMs = 3_000;
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
    !["pending", "accepted", "failed"].includes(item.state) ||
    (item.intentId !== undefined && typeof item.intentId !== "string") ||
    (item.error !== undefined && typeof item.error !== "string") ||
    (item.retryCount !== undefined && (!Number.isInteger(item.retryCount) || item.retryCount < 0)) ||
    (item.nextAttemptAt !== undefined && !Number.isFinite(item.nextAttemptAt)))) {
    throw new Error("Saved discovery requests are invalid. Restore your browser data before continuing.");
  }
  return parsed as PendingDiscoveryAdmission[];
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

export function enqueuePendingDiscoveryAdmission(admission: Omit<PendingDiscoveryAdmission, "state">) {
  const current = pendingDiscoveryAdmissions();
  if (current.some((item) => item.opportunityId === admission.opportunityId))
    throw new Error("This discovery already has a pending save.");
  writeAdmissions([...current, { ...admission, state: "pending" }]);
}

export function retryPendingDiscoveryAdmission(opportunityId: string) {
  replaceAdmission(opportunityId, (admission) => ({
    ...admission, intentId: admission.state === "failed" ? undefined : admission.intentId,
    state: "pending", error: undefined, retryCount: 0, nextAttemptAt: undefined,
  }));
  return flushPendingDiscoveryAdmissions();
}

export function recoverUnacknowledgedDiscoveryAdmissions() {
  const admissions = pendingDiscoveryAdmissions();
  let changed = false;
  const recovered = admissions.map((admission) => {
    if (admission.state !== "accepted" &&
        (admission.state !== "failed" ||
         !admission.error?.startsWith("Discovery save timed out after 15 seconds.")))
      return admission;
    changed = true;
    return { ...admission, intentId: undefined, state: "pending" as const,
      error: admission.state === "failed"
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
  response = await confirmOperationResponse(response);
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
          "Idempotency-Key": `discovery:${admission.opportunityId}:${admission.evidenceFingerprint}` },
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

export function flushPendingDiscoveryAdmissions(): Promise<void> {
  if (!activeFlush) {
    activeFlush = Promise.all(pendingDiscoveryAdmissions()
      .filter((admission) => admission.state !== "failed" &&
        (admission.nextAttemptAt ?? 0) <= Date.now())
      .map(processAdmission)).then(() => undefined).finally(() => { activeFlush = undefined; });
  }
  return activeFlush;
}
