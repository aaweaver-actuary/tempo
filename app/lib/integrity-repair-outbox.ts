import { z } from "zod";
import { API_URL } from "../const";
import { integrityRepairSubmissionSchema, repertoireIntegritySchema, repertoiresResponseSchema } from "../domain/schemas";
import { backgroundFetch } from "./background-fetch";
import { readJsonResponse } from "./validated-data";

export const INTEGRITY_REPAIRS_CHANGED = "tempo-integrity-repairs-changed";
export const INTEGRITY_REPAIR_CONFIRMED = "tempo-integrity-repair-confirmed";
const storagePrefix = "tempo-pending-integrity-repairs-v3:";
const legacyQueueKey = "tempo-pending-integrity-repairs-v2";
const legacyCommandKey = "tempo-pending-integrity-repair-v1";
const retrySchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("operation"), baselineRetryCycle: z.number().int().nonnegative(),
    baselineAttemptCount: z.number().int().nonnegative(), deliveryAccepted: z.boolean() }),
  z.object({ kind: z.literal("task"), taskId: z.string(), taskResetConfirmed: z.boolean() }),
]);
const repairSchema = z.object({
  repertoireId: z.string().min(1), issueId: z.string().min(1), signature: z.string().min(1),
  selectedMoveUci: z.string().regex(/^[a-h][1-8][a-h][1-8][qrbn]?$/),
  operationId: z.string().min(1).max(128), queuedAt: z.number().finite().default(0),
  phase: z.enum(["queued", "saving", "validating", "failed", "blocked", "stale"]),
  taskId: z.string().optional(), taskGeneration: z.number().int().positive().optional(),
  retryTaskId: z.string().nullable().optional(), retryOperationId: z.string().optional(),
  retry: retrySchema.optional(), terminalOperationFailure: z.boolean().optional(),
  error: z.string().optional(), attempts: z.number().int().nonnegative().default(0),
  nextAttemptAt: z.number().finite().default(0), pollOrder: z.number().int().nonnegative().default(0),
});
export type PendingIntegrityRepair = z.infer<typeof repairSchema>;
type RepairChoice = Pick<PendingIntegrityRepair, "repertoireId" | "issueId" | "signature" | "selectedMoveUci">;
let activeFlush: Promise<void> | undefined;
const activeRetries = new Map<string, Promise<void>>();

function changed() { window.dispatchEvent(new Event(INTEGRITY_REPAIRS_CHANGED)); }
export function subscribeIntegrityRepairs(listener: () => void) {
  const storageChanged = (event: StorageEvent) => {
    if (event.key === null || event.key.startsWith("tempo-pending-integrity-repair")) listener();
  };
  window.addEventListener(INTEGRITY_REPAIRS_CHANGED, listener);
  window.addEventListener("storage", storageChanged);
  return () => {
    window.removeEventListener(INTEGRITY_REPAIRS_CHANGED, listener);
    window.removeEventListener("storage", storageChanged);
  };
}
function persist(repair: PendingIntegrityRepair) {
  localStorage.setItem(storagePrefix + repair.operationId, JSON.stringify(repairSchema.parse(repair)));
}
function sameRepairChoice(left: RepairChoice, right: RepairChoice) {
  return left.repertoireId === right.repertoireId && left.issueId === right.issueId
    && left.signature === right.signature && left.selectedMoveUci === right.selectedMoveUci;
}
function migrateLegacyRepairs() {
  const oldQueue = localStorage.getItem(legacyQueueKey);
  const oldCommand = localStorage.getItem(legacyCommandKey);
  if (!oldQueue && !oldCommand) return;
  const recovered = oldQueue ? z.array(repairSchema).parse(JSON.parse(oldQueue)) : [];
  if (oldCommand) {
    const command = z.object({ operationId: z.string(), fingerprint: z.string() }).parse(JSON.parse(oldCommand));
    const [repertoireId, issueId, body] = z.tuple([z.string(), z.string(), z.string()]).parse(JSON.parse(command.fingerprint));
    const choice = z.object({ signature: z.string(), selected_move_uci: z.string() }).parse(JSON.parse(body));
    const commandRepair = repairSchema.parse({
      repertoireId, issueId, signature: choice.signature, selectedMoveUci: choice.selected_move_uci,
      operationId: command.operationId, phase: "saving",
    });
    const matchingOperation = recovered.find(repair => repair.operationId === command.operationId);
    if (matchingOperation && !sameRepairChoice(matchingOperation, commandRepair)) throw new Error("Legacy repair journals disagree about the saved choice.");
    if (!matchingOperation) recovered.push(commandRepair);
  }
  recovered.forEach((repair, index) => {
    // Never overwrite progress already recovered by another tab or an earlier migration attempt.
    const existingRecord = localStorage.getItem(storagePrefix + repair.operationId);
    if (!existingRecord) persist({ ...repair, queuedAt: repair.queuedAt || index });
    else {
      const existing = repairSchema.parse(JSON.parse(existingRecord));
      if (existing.operationId !== repair.operationId || !sameRepairChoice(existing, repair)
        || repair.taskId && existing.taskId !== repair.taskId
        || (existing.taskGeneration ?? 1) < (repair.taskGeneration ?? 1)) throw new Error("Recovered repair identity or task progress conflicts with its original journal.");
    }
  });
  // All writes must succeed before either original journal is removed.
  if (oldQueue) localStorage.removeItem(legacyQueueKey);
  if (oldCommand) localStorage.removeItem(legacyCommandKey);
}
export function pendingIntegrityRepairs(): PendingIntegrityRepair[] {
  try {
    migrateLegacyRepairs();
    const keys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
      .filter((key): key is string => Boolean(key?.startsWith(storagePrefix)));
    return keys.map(key => {
      const repair = repairSchema.parse(JSON.parse(localStorage.getItem(key)!));
      if (key !== storagePrefix + repair.operationId) throw new Error("Repair identity does not match its journal");
      return repair;
    }).sort((left, right) => left.queuedAt - right.queuedAt || left.operationId.localeCompare(right.operationId));
  } catch (cause) {
    throw new Error("Saved repair choices could not be read. Preserve browser data and retry after resolving the storage error.", { cause });
  }
}
function updateRepair(operationId: string, change: (repair: PendingIntegrityRepair) => PendingIntegrityRepair | null) {
  const repair = pendingIntegrityRepairs().find(item => item.operationId === operationId);
  if (!repair) return;
  const updated = change(repair);
  if (updated) persist(updated);
  else localStorage.removeItem(storagePrefix + operationId);
  changed();
}
export function enqueueIntegrityRepair(choice: RepairChoice) {
  const storedRepairs = pendingIntegrityRepairs();
  const existing = storedRepairs.find(repair => repair.repertoireId === choice.repertoireId && repair.issueId === choice.issueId);
  if (existing && existing.phase !== "stale") throw new Error("This conflict already has a saved choice. Check its progress before choosing again.");
  const repair = repairSchema.parse({ ...choice, operationId: crypto.randomUUID(), phase: "queued",
    queuedAt: existing?.queuedAt ?? Math.max(Date.now(), ...storedRepairs.map(repair => repair.queuedAt + 1)) });
  persist(repair);
  if (existing) localStorage.removeItem(storagePrefix + existing.operationId);
  changed();
  return repair;
}
export function discardStaleIntegrityRepair(operationId: string) {
  updateRepair(operationId, repair => {
    if (repair.phase !== "stale") throw new Error("Confirm this save before discarding its choice.");
    return null;
  });
}
async function timedRequest(url: string, init?: RequestInit, background = true) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await (background ? backgroundFetch : fetch)(url, { ...init, signal: controller.signal });
    // Keep the deadline active while reading the body, including a response stalled after headers.
    const body = await response.text();
    return new Response([204, 205, 304].includes(response.status) ? null : body,
      { status: response.status, statusText: response.statusText, headers: response.headers });
  }
  finally { window.clearTimeout(timeout); }
}
const receiptSchema = z.object({ state: z.enum(["unknown", "pending", "executing", "retrying", "complete", "failed", "blocked"]),
  operation_id: z.string().optional(), response: z.unknown().optional(), message: z.string().optional(),
  retry_cycle: z.number().int().nonnegative().default(0), attempt_count: z.number().int().nonnegative().default(0),
  error: z.object({ message: z.string().optional(), status_code: z.number().optional() }).optional(),
  last_error: z.object({ message: z.string().optional() }).optional(),
});
async function receipt(operationId: string) {
  const response = await timedRequest(`${API_URL}/api/operations/${encodeURIComponent(operationId)}`);
  if (response.status === 404) return receiptSchema.parse({ state: "unknown" });
  const result = await readJsonResponse(response, receiptSchema, "repair receipt");
  if (result.operation_id && result.operation_id !== operationId) throw new Error("The repair receipt has an unrelated identity.");
  return result;
}
function accepted(repair: PendingIntegrityRepair, response: unknown) {
  // Previously shipped receipts are immutable and can include the removed generation field.
  const { task_generation: legacyTaskGeneration, ...submission } = z.object({
    task_generation: z.number().int().positive().optional(),
  }).passthrough().parse(response);
  const result = integrityRepairSubmissionSchema.parse(submission);
  if (result.repertoire_id !== repair.repertoireId || result.issue_id !== repair.issueId) throw new Error("Repair confirmation does not match its saved choice.");
  updateRepair(repair.operationId, current => ({ ...current, phase: "validating", taskId: result.task_id,
    taskGeneration: legacyTaskGeneration ?? current.taskGeneration, retryTaskId: undefined,
    retry: undefined, retryOperationId: undefined, terminalOperationFailure: false,
    error: undefined, attempts: 0, nextAttemptAt: 0 }));
}
function fail(repair: PendingIntegrityRepair, phase: "failed" | "blocked" | "stale", error: string, terminalOperationFailure = false) {
  updateRepair(repair.operationId, current => ({ ...current, phase, error, terminalOperationFailure,
    retry: undefined, retryOperationId: undefined }));
}
async function confirmReceipt(repair: PendingIntegrityRepair, observed?: z.infer<typeof receiptSchema>) {
  const status = observed ?? await receipt(repair.operationId);
  if (status.state === "complete") { accepted(repair, status.response); return true; }
  if (status.state === "failed") {
    fail(repair, [404, 409, 422].includes(status.error?.status_code ?? 0) ? "stale" : "failed",
      status.error?.message ?? "Repair save failed.", true); return true;
  }
  if (status.state === "blocked") { fail(repair, "blocked", status.last_error?.message ?? "Repair delivery is blocked. Check the service, then retry."); return true; }
  if (status.state === "unknown") return false;
  // The API cannot replay legacy no-payload receipts. The complete saved choice is the recovery evidence.
  if (status.state === "pending" && status.message?.startsWith("Legacy receipt has no saved payload.")) return false;
  updateRepair(repair.operationId, current => ({ ...current, phase: "saving", error: undefined }));
  return true;
}
async function currentIntegrity(repair: PendingIntegrityRepair) {
  const response = await timedRequest(`${API_URL}/api/repertoires/${encodeURIComponent(repair.repertoireId)}/integrity`);
  if (response.status === 404) {
    fail(repair, "stale", "This repertoire was removed. Review the saved choice before discarding it.");
    return null;
  }
  const integrity = await readJsonResponse(response,
    repertoireIntegritySchema, "repair evidence");
  if (integrity.repertoire_id !== repair.repertoireId) throw new Error("Repair evidence has an unrelated repertoire identity.");
  return integrity;
}
async function stillCurrent(repair: PendingIntegrityRepair) {
  const integrity = await currentIntegrity(repair);
  if (!integrity) return false;
  if (integrity.scan_status === "failed") {
    fail(repair, "failed", integrity.last_scan_error ?? "Integrity evidence failed. Retry the scan in Analysis activity before saving this choice.");
    return false;
  }
  if (integrity.scan_status !== "idle" || integrity.status === "unchecked") return false;
  if (!integrity.issues.some(issue => issue.id === repair.issueId && issue.signature === repair.signature)) {
    fail(repair, "stale", "An earlier edit changed this conflict. Review the current position before saving another choice.");
    return false;
  }
  return true;
}
const tasksSchema = z.object({ tasks: z.array(z.object({ id: z.string(), kind: z.string(), deduplication_key: z.string(),
  generation: z.number().int().positive(), state: z.enum(["queued", "leased", "retrying", "complete", "failed"]),
  last_error: z.string().nullable().optional() })) });
async function validateRepair(repair: PendingIntegrityRepair) {
  const tasks = await readJsonResponse(await timedRequest(`${API_URL}/api/system/tasks`), tasksSchema, "repair tasks");
  const task = tasks.tasks.find(item => item.id === repair.taskId);
  if (task && (!['opening_graph_rebuild', 'integrity_repair'].includes(task.kind)
    || task.kind === "opening_graph_rebuild" && task.deduplication_key !== repair.repertoireId
    || task.kind === "integrity_repair" && task.deduplication_key !== `${repair.repertoireId}:${repair.signature}`
    || task.generation < (repair.taskGeneration ?? 1))) throw new Error("Repair validation returned an unrelated task or generation.");
  if (task?.state === "failed") {
    updateRepair(repair.operationId, current => ({ ...current, phase: "failed", retryTaskId: task.id,
      error: task.last_error ?? "Repair validation failed. Retry the task.", retry: undefined })); return;
  }
  if (task && task.state !== "complete") return;
  const integrity = await currentIntegrity(repair);
  if (!integrity) return;
  if (integrity.scan_status === "failed") {
    const scanTask = tasks.tasks.find(item => item.kind === "integrity_scan" && item.deduplication_key === repair.repertoireId);
    updateRepair(repair.operationId, current => ({ ...current, phase: "failed", retryTaskId: scanTask?.id,
      error: integrity.last_scan_error ?? "Integrity validation failed. Check Analysis activity.", retry: undefined })); return;
  }
  if (integrity.scan_status !== "idle" || integrity.status === "unchecked") return;
  if (task?.kind === "opening_graph_rebuild" || !task) {
    const response = await timedRequest(`${API_URL}/api/repertoires`);
    const repertoires = await readJsonResponse(response, repertoiresResponseSchema, "repair graph publication");
    const repertoire = repertoires.repertoires.find(item => item.id === repair.repertoireId);
    if (!repertoire) { fail(repair, "stale", "This repertoire was removed. Review the saved choice before discarding it."); return; }
    const requiredGeneration = task?.generation ?? repair.taskGeneration ?? 1;
    if (repertoire.graph_state !== "ready" || (repertoire.graph_generation ?? 0) < requiredGeneration) return;
  }
  if (integrity.issues.some(issue => issue.id === repair.issueId)) {
    fail(repair, "stale", "The repair finished but this position still needs review. Choose from the current evidence."); return;
  }
  updateRepair(repair.operationId, () => null);
  window.dispatchEvent(new CustomEvent(INTEGRITY_REPAIR_CONFIRMED, { detail: { repertoireId: repair.repertoireId, operationId: repair.operationId } }));
}
async function processRetry(repair: PendingIntegrityRepair) {
  const retry = repair.retry!;
  if (retry.kind === "operation") {
    const status = await receipt(repair.operationId);
    const advanced = status.retry_cycle > retry.baselineRetryCycle || status.attempt_count > retry.baselineAttemptCount
      || !["blocked", "failed"].includes(status.state);
    if (status.state === "unknown") throw new Error("The original repair receipt is unavailable. Its retry is preserved.");
    if (advanced) {
      updateRepair(repair.operationId, current => ({ ...current, retry: undefined, retryOperationId: undefined }));
      await confirmReceipt(repair, status);
    } else if (!retry.deliveryAccepted) {
      const response = await timedRequest(`${API_URL}/api/operations/${encodeURIComponent(repair.operationId)}/retry`, { method: "POST" }, false);
      if (!response.ok) throw new Error("Repair retry delivery is unconfirmed. Check its original receipt.");
      updateRepair(repair.operationId, current => ({ ...current, retry: { ...retry, deliveryAccepted: true } }));
    }
    return false;
  }
  if (!retry.taskResetConfirmed) {
    const status = await receipt(repair.retryOperationId!);
    if (status.state === "complete") {
      const result = z.object({ id: z.string() }).parse(status.response);
      if (result.id !== retry.taskId) throw new Error("Retry receipt does not match the saved task.");
    } else if (["failed", "blocked"].includes(status.state)) {
      fail(repair, "failed", status.error?.message ?? status.last_error?.message ?? "The validation retry needs attention."); return false;
    } else if (status.state === "unknown") {
      const response = await timedRequest(`${API_URL}/api/system/tasks/${encodeURIComponent(retry.taskId)}/retry`, {
        method: "POST", headers: { "Idempotency-Key": repair.retryOperationId! },
      }, false);
      if (!response.ok) throw new Error("Validation retry delivery is unconfirmed. Its identity is preserved.");
      const result = z.object({ operation_id: z.string().optional(), id: z.string().optional() }).parse(await response.json());
      if (result.operation_id && result.operation_id !== repair.retryOperationId || result.id && result.id !== retry.taskId) throw new Error("Validation retry returned an unrelated identity.");
      if (response.status === 202 || result.operation_id) return false;
      if (result.id !== retry.taskId) throw new Error("Validation retry was not confirmed.");
    } else return false;
    updateRepair(repair.operationId, current => ({ ...current, retry: undefined, retryOperationId: undefined, retryTaskId: undefined }));
  }
  return true;
}
async function processRepair(repair: PendingIntegrityRepair) {
  try {
    if (repair.retry && !await processRetry(repair)) return;
    if (repair.taskId) { await validateRepair(repair); return; }
    if (await confirmReceipt(repair) || !await stillCurrent(repair)) return;
    updateRepair(repair.operationId, current => ({ ...current, phase: "saving" }));
    const response = await timedRequest(`${API_URL}/api/repertoires/${encodeURIComponent(repair.repertoireId)}` +
      `/integrity/issues/${encodeURIComponent(repair.issueId)}/resolve`, {
      method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": repair.operationId },
      body: JSON.stringify({ signature: repair.signature, selected_move_uci: repair.selectedMoveUci }),
    }, false);
    const body: unknown = await response.json();
    if (response.ok && !(body && typeof body === "object" && "operation_id" in body)) { accepted(repair, body); return; }
    // A stale endpoint response can race a successful earlier delivery. Resolve its receipt first.
    if (await confirmReceipt(repair)) return;
    if ([404, 409, 422].includes(response.status)) {
      const detail = z.object({ detail: z.string().optional() }).parse(body);
      fail(repair, "stale", detail.detail ?? "This conflict changed. Review its current evidence."); return;
    }
    if (!response.ok) throw new Error(`Repair delivery is unconfirmed (HTTP ${response.status}).`);
  } catch (cause) {
    updateRepair(repair.operationId, current => {
      const attempts = current.attempts + 1;
      return { ...current, attempts, error: cause instanceof Error ? cause.message : "Repair confirmation is pending.",
        nextAttemptAt: Date.now() + Math.min(30_000, 3_000 * 2 ** Math.min(attempts - 1, 4)) };
    });
  }
}
export function flushIntegrityRepairs(): Promise<void> {
  if (activeFlush) return activeFlush;
  const flush = async () => {
    const heads = new Map<string, PendingIntegrityRepair>();
    for (const repair of pendingIntegrityRepairs()) if (!heads.has(repair.repertoireId)) heads.set(repair.repertoireId, repair);
    const eligible = [...heads.values()].filter(repair => ["queued", "saving", "validating"].includes(repair.phase)
      && repair.nextAttemptAt <= Date.now()).sort((left, right) => left.pollOrder - right.pollOrder).slice(0, 2);
    let pollOrder = Math.max(0, ...pendingIntegrityRepairs().map(repair => repair.pollOrder));
    for (const repair of eligible) updateRepair(repair.operationId, current => ({ ...current, pollOrder: ++pollOrder }));
    await Promise.all(eligible.map(processRepair));
  };
  // Separate records avoid lost array updates. A browser-wide flush lock serializes delivery across tabs.
  activeFlush = (navigator.locks ? navigator.locks.request("tempo-integrity-repair-flush", { ifAvailable: true }, lock => lock ? flush() : undefined)
    : flush()).then(() => undefined).finally(() => { activeFlush = undefined; });
  return activeFlush;
}
export function retryIntegrityRepair(operationId: string): Promise<void> {
  const active = activeRetries.get(operationId);
  if (active) return active;
  const retry = (async () => {
    if (activeFlush) await activeFlush;
    const repair = pendingIntegrityRepairs().find(item => item.operationId === operationId);
    if (!repair) return;
    if (repair.phase === "stale") throw new Error("Review this conflict before choosing again.");
    if (!repair.retry && repair.phase === "blocked") {
      const status = await receipt(operationId);
      if (status.state !== "blocked") { await confirmReceipt(repair, status); return; }
      updateRepair(operationId, current => ({ ...current, phase: "saving", error: undefined, nextAttemptAt: 0,
        retry: { kind: "operation", baselineRetryCycle: status.retry_cycle, baselineAttemptCount: status.attempt_count, deliveryAccepted: false } }));
    } else if (!repair.retry && repair.retryTaskId) {
      updateRepair(operationId, current => ({ ...current, phase: "validating", error: undefined, nextAttemptAt: 0,
        retryOperationId: crypto.randomUUID(), retry: { kind: "task", taskId: repair.retryTaskId!, taskResetConfirmed: false } }));
    } else if (!repair.retry && repair.terminalOperationFailure) {
      if (!await stillCurrent(repair)) return;
      const replacement = { ...repair, operationId: crypto.randomUUID(), phase: "queued" as const,
        terminalOperationFailure: false, error: undefined, attempts: 0, nextAttemptAt: 0 };
      persist(replacement); localStorage.removeItem(storagePrefix + operationId); changed();
    } else updateRepair(operationId, current => ({ ...current, phase: current.taskId ? "validating" : "saving",
      error: undefined, attempts: 0, nextAttemptAt: 0 }));
    await flushIntegrityRepairs();
  })().finally(() => { activeRetries.delete(operationId); });
  activeRetries.set(operationId, retry);
  return retry;
}
