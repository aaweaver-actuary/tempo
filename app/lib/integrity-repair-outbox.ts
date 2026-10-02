import { z } from "zod";
import { API_URL } from "../const";
import { integrityRepairSubmissionSchema } from "../domain/schemas";
import { backgroundFetch } from "./background-fetch";

export const INTEGRITY_REPAIRS_CHANGED = "tempo-integrity-repairs-changed";
export const INTEGRITY_REPAIR_CONFIRMED = "tempo-integrity-repair-confirmed";
const storageKey = "tempo-pending-integrity-repairs-v2";
const legacyKey = "tempo-pending-integrity-repair-v1";
const retrySchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("operation"), baselineRetryCycle: z.number().int().nonnegative(),
    baselineAttemptCount: z.number().int().nonnegative(), deliveryAccepted: z.boolean() }),
  z.object({ kind: z.literal("task"), taskId: z.string().min(1),
    taskResetConfirmed: z.boolean() }),
]);
const repairSchema = z.object({
  repertoireId: z.string().min(1), issueId: z.string().min(1), signature: z.string().min(1),
  selectedMoveUci: z.string().regex(/^[a-h][1-8][a-h][1-8][qrbn]?$/),
  operationId: z.string().min(1).max(128),
  phase: z.enum(["queued", "saving", "validating", "failed", "blocked", "stale"]),
  taskId: z.string().optional(), taskGeneration: z.number().int().positive().optional(),
  retryTaskId: z.string().nullable().optional(), retryOperationId: z.string().optional(),
  retry: retrySchema.optional(),
  terminalOperationFailure: z.boolean().optional(), error: z.string().optional(),
  attempts: z.number().int().nonnegative().default(0), nextAttemptAt: z.number().finite().default(0),
  pollOrder: z.number().int().nonnegative().default(0),
});
export type PendingIntegrityRepair = z.infer<typeof repairSchema>;
let activeFlush: Promise<void> | undefined;
const activeRetries = new Map<string, Promise<void>>();

function writeRepairs(repairs: PendingIntegrityRepair[]) {
  localStorage.setItem(storageKey, JSON.stringify(repairs));
  window.dispatchEvent(new Event(INTEGRITY_REPAIRS_CHANGED));
}
export function pendingIntegrityRepairs(): PendingIntegrityRepair[] {
  const stored = localStorage.getItem(storageKey);
  const parsed = z.array(repairSchema).safeParse(JSON.parse(stored ?? "[]"));
  if (!parsed.success) throw new Error("Saved repair choices are invalid. Restore browser data before retrying.");
  const legacy = localStorage.getItem(legacyKey);
  if (!legacy) return parsed.data;
  const old = z.object({ operationId: z.string().min(1).max(128), fingerprint: z.string() }).parse(JSON.parse(legacy));
  const [repertoireId, issueId, body] = z.tuple([z.string(), z.string(), z.string()]).parse(JSON.parse(old.fingerprint));
  const choice = z.object({ signature: z.string(), selected_move_uci: z.string() }).parse(JSON.parse(body));
  const recovered = repairSchema.parse({ repertoireId, issueId, signature: choice.signature,
    selectedMoveUci: choice.selected_move_uci, operationId: old.operationId, phase: "saving" });
  const repairs = parsed.data.some(item => item.operationId === old.operationId) ? parsed.data : [...parsed.data, recovered];
  // Write first; a storage failure must preserve the original record.
  localStorage.setItem(storageKey, JSON.stringify(repairs));
  localStorage.removeItem(legacyKey);
  return repairs;
}
function updateRepair(operationId: string, change: (repair: PendingIntegrityRepair) => PendingIntegrityRepair | null) {
  writeRepairs(pendingIntegrityRepairs().flatMap(repair => {
    if (repair.operationId !== operationId) return [repair];
    const changed = change(repair); return changed ? [changed] : [];
  }));
}
export function enqueueIntegrityRepair(choice: Pick<PendingIntegrityRepair,
  "repertoireId" | "issueId" | "signature" | "selectedMoveUci">) {
  const repairs = pendingIntegrityRepairs();
  if (repairs.some(item => item.repertoireId === choice.repertoireId && item.issueId === choice.issueId))
    throw new Error("This position already has a saved repair choice. Check its progress before saving again.");
  const repair = repairSchema.parse({ ...choice, operationId: crypto.randomUUID(), phase: "queued" });
  writeRepairs([...repairs, repair]);
  return repair;
}
export function discardStaleIntegrityRepair(operationId: string) {
  updateRepair(operationId, repair => {
    if (repair.phase !== "stale") throw new Error("Confirm this save before replacing its choice.");
    return null;
  });
}
async function timedRequest(url: string, init?: RequestInit, background = true) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15_000);
  try { return await (background ? backgroundFetch : fetch)(url, { ...init, signal: controller.signal }); }
  finally { window.clearTimeout(timeout); }
}
async function receipt(operationId: string) {
  const response = await timedRequest(`${API_URL}/api/operations/${encodeURIComponent(operationId)}`);
  if (response.status === 404) return { state: "unknown", retry_cycle: 0, attempt_count: 0 };
  if (!response.ok) throw new Error("Could not confirm repair delivery. Its saved choice will be retried.");
  return z.object({ state: z.string(), response: z.unknown().optional(),
    retry_cycle: z.number().int().nonnegative().default(0),
    attempt_count: z.number().int().nonnegative().default(0),
    error: z.object({ message: z.string().optional() }).optional(),
    last_error: z.object({ message: z.string().optional() }).optional() }).parse(await response.json());
}
function accepted(repair: PendingIntegrityRepair, response: unknown) {
  const result = integrityRepairSubmissionSchema.parse(response);
  if (result.repertoire_id !== repair.repertoireId || result.issue_id !== repair.issueId)
    throw new Error("Repair confirmation does not match its saved choice.");
  updateRepair(repair.operationId, current => ({ ...current, phase: "validating",
    taskId: result.task_id, taskGeneration: result.task_generation ?? 1,
    retry: undefined, retryOperationId: undefined, retryTaskId: undefined, terminalOperationFailure: false,
    error: undefined, attempts: 0, nextAttemptAt: 0 }));
}
function fail(repair: PendingIntegrityRepair, phase: "failed" | "blocked" | "stale", error: string, terminalOperationFailure = false) {
  updateRepair(repair.operationId, current => ({ ...current, phase, error, terminalOperationFailure,
    retry: undefined, retryOperationId: undefined }));
}
async function confirmReceipt(repair: PendingIntegrityRepair, observed?: Awaited<ReturnType<typeof receipt>>) {
  const status = observed ?? await receipt(repair.operationId);
  if (status.state === "complete") { accepted(repair, status.response); return true; }
  if (status.state === "failed") { fail(repair, "failed", status.error?.message ?? "Repair save failed.", true); return true; }
  if (status.state === "blocked") { fail(repair, "blocked", status.last_error?.message ?? "Repair delivery is blocked. Resolve the service error, then retry."); return true; }
  if (status.state !== "unknown") updateRepair(repair.operationId, current => ({ ...current, phase: "saving" }));
  return status.state !== "unknown";
}
async function submitRetry(repair: PendingIntegrityRepair) {
  const retry = repair.retry!;
  const endpoint = retry.kind === "task" ? `${API_URL}/api/system/tasks/${encodeURIComponent(retry.taskId)}/retry`
    : `${API_URL}/api/operations/${encodeURIComponent(repair.operationId)}/retry`;
  const response = await timedRequest(endpoint, { method: "POST",
    headers: { "Idempotency-Key": repair.retryOperationId! } }, false);
  if (!response.ok) throw new Error("Could not confirm the repair retry. Its saved request will be checked again.");
  const result = z.object({ operation_id: z.string().optional(), id: z.string().optional() }).parse(await response.json());
  if (retry.kind === "task" && (result.operation_id && result.operation_id !== repair.retryOperationId
      || result.id && result.id !== retry.taskId)) throw new Error("Retry confirmation does not match the saved task.");
  const applied = retry.kind === "task" && response.status !== 202 && !result.operation_id;
  updateRepair(repair.operationId, current => ({ ...current,
    retry: retry.kind === "task" ? { ...retry, taskResetConfirmed: applied } : { ...retry, deliveryAccepted: true } }));
  return applied;
}
async function processRetry(repair: PendingIntegrityRepair): Promise<boolean> {
  const retry = repair.retry!;
  if (retry.kind === "operation") {
    const status = await receipt(repair.operationId);
    if (status.state === "unknown") throw new Error("The original repair receipt is unavailable. Its retry choice is preserved.");
    const advanced = status.retry_cycle > retry.baselineRetryCycle || status.attempt_count > retry.baselineAttemptCount
      || !["blocked", "failed"].includes(status.state);
    if (advanced) {
      updateRepair(repair.operationId, current => ({ ...current, retry: undefined, retryOperationId: undefined }));
      await confirmReceipt(repair, status);
    } else if (!retry.deliveryAccepted) await submitRetry(repair);
    // An unchanged terminal observation is still the pre-retry failure.
    return false;
  }
  if (!retry.taskResetConfirmed) {
    const status = await receipt(repair.retryOperationId!);
    if (status.state === "complete") {
      const result = z.object({ id: z.string(), generation: z.number().int().positive(), state: z.string() }).parse(status.response);
      if (result.id !== retry.taskId) throw new Error("Retry receipt does not match the saved task.");
    } else if (["failed", "blocked"].includes(status.state)) {
      fail(repair, "failed", status.error?.message ?? status.last_error?.message ?? "The task retry failed. Check Analysis activity and retry again.");
      return false;
    } else if (status.state === "unknown") {
      if (!await submitRetry(repair)) return false;
    } else return false;
    // This receipt (or synchronous result) proves the atomic task reset committed.
    // A later terminal read is a new failure even if no queued poll was observed.
    updateRepair(repair.operationId, current => ({ ...current, retry: { ...retry, taskResetConfirmed: true } }));
  }
  return true;
}
async function processRepair(repair: PendingIntegrityRepair) {
  try {
    if (repair.retry && !await processRetry(repair)) return;
    if (!repair.taskId) {
      if (await confirmReceipt(repair)) return;
      updateRepair(repair.operationId, current => ({ ...current, phase: "saving" }));
      const response = await timedRequest(`${API_URL}/api/repertoires/${encodeURIComponent(repair.repertoireId)}` +
        `/integrity/issues/${encodeURIComponent(repair.issueId)}/resolve`, {
        method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": repair.operationId },
        body: JSON.stringify({ signature: repair.signature, selected_move_uci: repair.selectedMoveUci }),
      }, false);
      const body: unknown = await response.json();
      if (response.ok && !(response.status === 202 && body && typeof body === "object" && "operation_id" in body)) {
        accepted(repair, body); return;
      }
      // Even a 404/409 can race a successful earlier delivery. The receipt wins.
      if (await confirmReceipt(repair)) return;
      if (response.status === 404 || response.status === 409 || response.status === 422) {
        const detail = z.object({ detail: z.string().optional() }).parse(body);
        fail(repair, "stale", detail.detail ?? "This issue changed. Refresh repair evidence and choose again.");
        return;
      }
      if (!response.ok) throw new Error(`Repair delivery is unconfirmed (HTTP ${response.status}).`);
      return;
    }
    const params = new URLSearchParams({ generation: String(repair.taskGeneration ?? 1), issue_id: repair.issueId });
    const response = await timedRequest(`${API_URL}/api/repertoires/${encodeURIComponent(repair.repertoireId)}` +
      `/integrity/repairs/${encodeURIComponent(repair.taskId)}?${params}`);
    if (!response.ok) throw new Error(`Repair validation could not be checked (HTTP ${response.status}). Check Analysis activity.`);
    const status = z.object({ task_id: z.string(), task_generation: z.number().int().positive(),
      state: z.enum(["waiting", "complete", "failed"]), issue_count: z.number().int().nonnegative(),
      reason: z.string().nullable(), retry_task_id: z.string().nullable() }).parse(await response.json());
    if (status.task_id !== repair.taskId || status.task_generation < (repair.taskGeneration ?? 1))
      throw new Error("Repair validation returned an unrelated generation.");
    if (status.state === "complete") {
      updateRepair(repair.operationId, () => null);
      window.dispatchEvent(new CustomEvent(INTEGRITY_REPAIR_CONFIRMED, { detail: { repertoireId: repair.repertoireId } }));
    } else if (status.state === "failed") {
      updateRepair(repair.operationId, current => ({ ...current, phase: status.retry_task_id ? "failed" : "stale",
        error: status.reason ?? "Repair validation failed", retryTaskId: status.retry_task_id,
        taskGeneration: status.task_generation, retry: undefined, retryOperationId: undefined }));
    } else {
      updateRepair(repair.operationId, current => ({ ...current, error: undefined, attempts: 0, nextAttemptAt: 0,
        taskGeneration: status.task_generation, retry: undefined, retryOperationId: undefined, retryTaskId: undefined }));
    }
  } catch (error) {
    updateRepair(repair.operationId, current => {
      const attempts = current.attempts + 1;
      return { ...current, error: error instanceof Error ? error.message : "Repair confirmation is pending.",
        attempts, nextAttemptAt: Date.now() + Math.min(30_000, 3_000 * 2 ** Math.min(attempts - 1, 4)) };
    });
  }
}
export function flushIntegrityRepairs(): Promise<void> {
  if (activeFlush) return activeFlush;
  activeFlush = (async () => {
    const repairs = pendingIntegrityRepairs().filter(repair =>
      ["queued", "saving", "validating"].includes(repair.phase) && repair.nextAttemptAt <= Date.now());
    // Reserve service for new submissions independently of long-running validation.
    const submission = repairs.filter(repair => !repair.taskId).sort((a, b) => a.pollOrder - b.pollOrder)[0];
    const confirmation = repairs.filter(repair => repair.taskId).sort((a, b) => a.pollOrder - b.pollOrder)[0];
    let pollOrder = Math.max(0, ...pendingIntegrityRepairs().map(repair => repair.pollOrder));
    for (const repair of [submission, confirmation]) {
      if (!repair) continue;
      updateRepair(repair.operationId, current => ({ ...current, pollOrder: ++pollOrder }));
      await processRepair(repair);
    }
  })().finally(() => { activeFlush = undefined; });
  return activeFlush;
}
export function retryIntegrityRepair(operationId: string): Promise<void> {
  const active = activeRetries.get(operationId);
  if (active) return active;
  const retry = requestIntegrityRepairRetry(operationId).finally(() => activeRetries.delete(operationId));
  activeRetries.set(operationId, retry);
  return retry;
}
async function requestIntegrityRepairRetry(operationId: string) {
  // Do not let an already-running status read overwrite a new retry baseline.
  if (activeFlush) await activeFlush;
  const repair = pendingIntegrityRepairs().find(item => item.operationId === operationId);
  if (!repair) return;
  if (repair.phase === "stale") throw new Error("Refresh the issue and choose a response again.");
  if (repair.retry) {
    updateRepair(operationId, current => ({ ...current, error: undefined, attempts: 0, nextAttemptAt: 0 }));
    return flushIntegrityRepairs();
  }
  if (repair.retryTaskId || repair.phase === "blocked") {
    const retryOperationId = repair.retryOperationId ?? crypto.randomUUID();
    let retry: z.infer<typeof retrySchema>;
    if (repair.retryTaskId) retry = { kind: "task", taskId: repair.retryTaskId, taskResetConfirmed: false };
    else {
      const status = await receipt(operationId);
      if (status.state !== "blocked") {
        await confirmReceipt(repair, status);
        return flushIntegrityRepairs();
      }
      retry = { kind: "operation", baselineRetryCycle: status.retry_cycle, baselineAttemptCount: status.attempt_count, deliveryAccepted: false };
    }
    // Persist intent before network I/O, including an ambiguous/lost POST response.
    updateRepair(operationId, current => ({ ...current, retryOperationId, retry,
      phase: current.taskId ? "validating" : "saving", error: undefined, attempts: 0, nextAttemptAt: 0 }));
    return flushIntegrityRepairs();
  } else if (repair.terminalOperationFailure) {
    const response = await timedRequest(`${API_URL}/api/repertoires/${encodeURIComponent(repair.repertoireId)}/integrity`);
    if (!response.ok) throw new Error("Could not refresh the rejected repair. Its choice has been preserved.");
    const evidence = z.object({ scan_status: z.string(), issues: z.array(z.object({ id: z.string(), signature: z.string() })) }).parse(await response.json());
    if (evidence.scan_status !== "idle" || !evidence.issues.some(issue => issue.id === repair.issueId && issue.signature === repair.signature)) {
      fail(repair, "stale", "This issue changed. Refresh its evidence and choose a response again.");
      return;
    }
    // A new operation is allowed only after a confirmed terminal rejection and explicit retry.
    const next = { ...repair, operationId: crypto.randomUUID(), phase: "queued" as const,
      terminalOperationFailure: false, error: undefined, attempts: 0, nextAttemptAt: 0 };
    updateRepair(operationId, () => next);
    return flushIntegrityRepairs();
  }
  updateRepair(operationId, current => ({ ...current, phase: current.taskId ? "validating" : "saving",
    error: undefined, attempts: 0, nextAttemptAt: 0 }));
  return flushIntegrityRepairs();
}
