import { z } from "zod";
import { API_URL } from "../const";
import { importResultSchema } from "../domain/schemas";
import { FailedOperationError, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-pgn-import-v1";
const RESOLUTION_BUDGET_MS = 30_000;
const POLL_INTERVAL_MS = 1_000;
const pendingImportSchema = z.object({ operationId: z.string().min(1).max(128), fingerprint: z.string().min(1) });
const receiptSchema = z.object({
  operation_id: z.string().optional(),
  state: z.enum(["unknown", "queued", "executing", "retrying", "pending", "blocked", "complete", "failed"]),
  response: z.unknown().optional(),
  error: z.object({ message: z.string().optional(), code: z.string().optional() }).nullish(),
  last_error: z.object({ message: z.string().optional() }).nullish(),
  message: z.string().optional(),
});
type PendingImport = z.infer<typeof pendingImportSchema>;
type ImportReceipt = z.infer<typeof receiptSchema>;
type ImportResult = z.infer<typeof importResultSchema>;
export type PgnImportCommandOptions = { signal?: AbortSignal; retryBlocked?: boolean };

function unconfirmedImport(operationId: string) {
  return new PendingOperationError(operationId,
    `Import confirmation is unavailable (operation ${operationId}). Check again with the same file and settings to recover this import.`);
}
function blockedImport(pending: PendingImport, receipt: ImportReceipt) {
  return new PendingOperationError(pending.operationId,
    `Import is blocked: ${receipt.last_error?.message ?? receipt.message ?? "Check the local service."} After resolving the problem, retry this blocked import.`, true);
}
export function readPendingPgnImport(): PendingImport | null {
  const stored = localStorage.getItem(PENDING_KEY);
  if (!stored) return null;
  try { return pendingImportSchema.parse(JSON.parse(stored)); }
  catch { throw new Error("The pending PGN import is unreadable. Restore browser data before importing another file."); }
}
function clearPendingImport(operationId: string) {
  if (readPendingPgnImport()?.operationId === operationId) localStorage.removeItem(PENDING_KEY);
}
async function inspectReceipt(pending: PendingImport, signal: AbortSignal, discarding = false): Promise<ImportReceipt> {
  let rawReceipt: unknown;
  try {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`, { signal });
    if (!status.ok) throw unconfirmedImport(pending.operationId);
    rawReceipt = await status.json();
  } catch {
    signal.throwIfAborted();
    throw unconfirmedImport(pending.operationId);
  }
  signal.throwIfAborted();
  const parsedReceipt = receiptSchema.safeParse(rawReceipt);
  if (!parsedReceipt.success) throw unconfirmedImport(pending.operationId);
  const receipt = parsedReceipt.data;
  if (receipt.operation_id && receipt.operation_id !== pending.operationId)
    throw new Error("The service returned a different import operation identity. Check the local service before retrying.");
  if (!discarding && receipt.state === "pending" && receipt.message?.startsWith("Legacy receipt has no saved payload."))
    throw new PendingOperationError(pending.operationId, `Import confirmation is unavailable. ${receipt.message}`);
  return receipt;
}
function resolveTerminal(pending: PendingImport, receipt: ImportReceipt, signal: AbortSignal): ImportResult | null {
  signal.throwIfAborted();
  if (receipt.state === "failed") {
    clearPendingImport(pending.operationId);
    throw new FailedOperationError(receipt.error?.message ?? receipt.message ?? "The PGN import failed.", pending.operationId);
  }
  if (receipt.state !== "complete") return null;
  // A malformed complete response must not erase the only recovery identity.
  const result = importResultSchema.parse(receipt.response);
  clearPendingImport(pending.operationId);
  return result;
}
async function pausePolling(signal: AbortSignal) {
  signal.throwIfAborted();
  await new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, POLL_INTERVAL_MS);
    signal.addEventListener("abort", abort, { once: true });
  });
}
async function submitImport(pending: PendingImport, file: File, trainedColor: "white" | "black", initialDepth: number, signal: AbortSignal): Promise<ImportResult | null> {
  const form = new FormData();
  form.append("file", file);
  form.append("trained_color", trainedColor);
  form.append("initial_depth", String(initialDepth));
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/imports/pgn`, {
      method: "POST", headers: { "Idempotency-Key": pending.operationId }, body: form, signal,
    });
  } catch {
    signal.throwIfAborted();
    return null; // Delivery is ambiguous; consult the original durable receipt.
  }
  signal.throwIfAborted();
  if (response.status === 202) {
    let acknowledgement: unknown;
    try { acknowledgement = await response.json(); }
    catch { signal.throwIfAborted(); return null; }
    signal.throwIfAborted();
    const accepted = z.object({ operation_id: z.string().min(1) }).safeParse(acknowledgement);
    if (!accepted.success) return null; // Unreadable admission still needs durable confirmation.
    if (accepted.data.operation_id !== pending.operationId)
      throw new Error("The service returned a different import operation identity. Check the local service before retrying.");
    return null;
  }
  // The PGN route rejects these validations before command dispatch. Other
  // errors (including durable-handler 5xx) must retain identity until inspected.
  if ([400, 422].includes(response.status)) {
    clearPendingImport(pending.operationId);
    await readJsonResponse(response, importResultSchema, "PGN import", { reportHttpFailure: false });
  }
  if (response.status >= 500 || [408, 429].includes(response.status)) return null;
  const result = await readJsonResponse(response, importResultSchema, "PGN import", { reportHttpFailure: false });
  signal.throwIfAborted();
  clearPendingImport(pending.operationId);
  return result;
}

// Complete/failed terminate; blocked and known-active receipts retain identity.
// Only a matching unknown receipt can replay, at most one PGN POST per action.
export async function savePgnImportCommand(
  file: File, trainedColor: "white" | "black", initialDepth: number,
  options: PgnImportCommandOptions = {},
): Promise<ImportResult> {
  options.signal?.throwIfAborted();
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  const fingerprint = [file.name, trainedColor, initialDepth,
    Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("")].join(":");
  options.signal?.throwIfAborted();
  let pending = readPendingPgnImport();
  if (options.retryBlocked && !pending) throw new Error("There is no pending blocked PGN import to retry.");
  const controller = new AbortController();
  let lastReceipt: ImportReceipt | undefined;
  const cancel = () => controller.abort(new DOMException("Import confirmation cancelled", "AbortError"));
  options.signal?.addEventListener("abort", cancel, { once: true });
  let rejectCancellation!: (error: unknown) => void;
  const cancellation = new Promise<never>((_resolve, reject) => { rejectCancellation = reject; });
  const rejectAbort = () => rejectCancellation(controller.signal.reason);
  controller.signal.addEventListener("abort", rejectAbort, { once: true });
  // Race also bounds an unresponsive transport; every continuation checks the
  // signal before deleting state or reporting success after cancellation.
  const deadline = setTimeout(() => controller.abort(
    lastReceipt?.state === "blocked" ? blockedImport(pending!, lastReceipt)
      : lastReceipt && lastReceipt.state !== "unknown"
        ? new PendingOperationError(pending!.operationId, `Import is still processing (operation ${pending!.operationId}). Check again to confirm its result.`)
        : unconfirmedImport(pending!.operationId),
  ), RESOLUTION_BUDGET_MS);
  const resolveImport = async (): Promise<ImportResult> => {
    const signal = controller.signal;
    if (pending) {
      lastReceipt = await inspectReceipt(pending, signal);
      const previousResult = resolveTerminal(pending, lastReceipt, signal);
      if (previousResult) {
        if (pending.fingerprint === fingerprint) return previousResult;
        pending = null;
        lastReceipt = undefined;
      } else if (pending.fingerprint !== fingerprint) {
        throw new PendingOperationError(pending.operationId,
          `An earlier PGN import is unresolved (operation ${pending.operationId}). Select its original file and settings to resolve it before importing a different file or changing settings.`);
      }
    }
    if (!pending) {
      signal.throwIfAborted();
      const newerPendingImport = readPendingPgnImport();
      if (newerPendingImport) throw new PendingOperationError(newerPendingImport.operationId,
        "The pending PGN import changed. Reopen the dialog to check or discard it.");
      pending = { operationId: crypto.randomUUID(), fingerprint };
      localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
    }
    const currentImport = pending;
    let posted = false;
    let retryRequested = false;
    if (lastReceipt?.state === "blocked" && options.retryBlocked) {
      retryRequested = true;
      try {
        await fetch(`${API_URL}/api/operations/${encodeURIComponent(currentImport.operationId)}/retry`, { method: "POST", signal });
      } catch { signal.throwIfAborted(); } // Retry delivery can also be ambiguous.
      // The accepted retry still says blocked until the worker starts its cycle.
      lastReceipt = await inspectReceipt(currentImport, signal);
    }
    while (true) {
      signal.throwIfAborted();
      if (lastReceipt) {
        const result = resolveTerminal(currentImport, lastReceipt, signal);
        if (result) return result;
        if (lastReceipt.state === "blocked" && !retryRequested) throw blockedImport(currentImport, lastReceipt);
      }
      if (!lastReceipt || lastReceipt.state === "unknown") {
        if (posted || retryRequested) throw unconfirmedImport(currentImport.operationId);
        posted = true;
        const result = await submitImport(currentImport, file, trainedColor, initialDepth, signal);
        if (result) return result;
      } else await pausePolling(signal);
      lastReceipt = await inspectReceipt(currentImport, signal);
    }
  };
  try { return await Promise.race([resolveImport(), cancellation]); }
  finally {
    clearTimeout(deadline);
    options.signal?.removeEventListener("abort", cancel);
    controller.signal.removeEventListener("abort", rejectAbort);
  }
}

export async function discardPendingPgnImport(operationId: string, options: { signal?: AbortSignal } = {}): Promise<ImportResult | null> {
  const pending = readPendingPgnImport();
  if (!pending || pending.operationId !== operationId) throw new Error("The pending PGN import changed. Reopen the dialog to check it.");
  const controller = new AbortController();
  const abort = () => controller.abort(new DOMException("Discard confirmation cancelled", "AbortError"));
  options.signal?.throwIfAborted();
  options.signal?.addEventListener("abort", abort, { once: true });
  const deadline = setTimeout(() => controller.abort(unconfirmedImport(operationId)), RESOLUTION_BUDGET_MS);
  let rejectCancellation!: (reason: unknown) => void;
  const cancellation = new Promise<never>((_resolve, reject) => { rejectCancellation = reject; });
  const rejectAbort = () => rejectCancellation(controller.signal.reason);
  controller.signal.addEventListener("abort", rejectAbort, { once: true });
  const resolveDiscard = async () => {
    const signal = controller.signal;
    let response: Response | undefined;
    try { response = await fetch(`${API_URL}/api/imports/pgn/${encodeURIComponent(operationId)}/discard`, { method: "POST", signal }); }
    catch { signal.throwIfAborted(); }
    signal.throwIfAborted();
    if (response && response.status >= 400 && response.status < 500) {
      const failure = await response.json().catch(() => null) as { detail?: string } | null;
      throw new Error(failure?.detail ?? "The pending import could not be discarded.");
    }
    while (true) {
      signal.throwIfAborted();
      const receipt = await inspectReceipt(pending, signal, true);
      if (receipt.state === "failed" && receipt.error?.code === "import_discarded") {
        clearPendingImport(operationId);
        return null;
      }
      const completed = resolveTerminal(pending, receipt, signal);
      if (completed) return completed;
      await pausePolling(signal);
    }
  };
  try { return await Promise.race([resolveDiscard(), cancellation]); }
  finally {
    clearTimeout(deadline);
    options.signal?.removeEventListener("abort", abort);
    controller.signal.removeEventListener("abort", rejectAbort);
  }
}
