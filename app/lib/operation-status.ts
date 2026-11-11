import { decodeOperationResult } from "../domain/adapters/operation-result-adapter";
import { defaultStudyClient, type StudyClient } from "./study-connection";
import { notifyOperationStatusChange } from "./operation-status-events";

type OperationResponseOptions = { background?: boolean; signal?: AbortSignal; fetch?: typeof fetch; allowMissing?: boolean; baseUrl?: string; client?: StudyClient };
type ClientBinding = { client: StudyClient; generation: number; origin: string };
function bindClient(client?: StudyClient): ClientBinding | undefined {
  return client ? { client, generation: client.generation, origin: client.origin } : undefined;
}
function requireBinding(binding?: ClientBinding): void {
  if (binding && (binding.client.generation !== binding.generation || binding.client.origin !== binding.origin))
    throw new Error("Study authority changed. Recover the original operation with its original service.");
}


export class PendingOperationError extends Error {
  constructor(readonly operationId: string, message?: string, readonly blocked = false) {
    super(message ?? `Save is still pending (operation ${operationId}). Retry to check its result.`);
    this.name = "PendingOperationError";
  }
}

export class FailedOperationError extends Error {
  constructor(message: string, readonly operationId: string, readonly status?: number,
    readonly code?: string, readonly retryable?: boolean, readonly detail?: unknown) {
    super(message);
    this.name = "FailedOperationError";
  }
}

export async function confirmOperationResponse(response: Response, options: OperationResponseOptions = {}): Promise<Response> {
  if (response.status !== 202) return response;
  const binding = operationBinding(options);
  const pending = await response.clone().json() as { operation_id?: string };
  requireBinding(binding);
  // Existing discovery admissions also return 202, with an intent ID that
  // their own status endpoint confirms. Preserve that response for its caller.
  if (!pending.operation_id) return response;
  return readReceipt(pending.operation_id, options, binding);
}

// Resolve a known command through the same receipt semantics as a 202 response.
function operationBinding(options: OperationResponseOptions): ClientBinding | undefined {
  if (options.client && options.fetch) throw new Error("Choose one operation transport capability.");
  return bindClient(options.client ?? (options.fetch ? undefined : defaultStudyClient));
}
export async function readOperationResponse(operationId: string, options: OperationResponseOptions = {}): Promise<Response> {
  return readReceipt(operationId, options, operationBinding(options));
}
async function readReceipt(operationId: string, options: OperationResponseOptions, binding?: ClientBinding): Promise<Response> {
  requireBinding(binding);
  const request = binding ? binding.client.learningFetch : options.fetch!;
  const init: RequestInit | undefined = options.background && binding
    ? { ...(options.signal ? { signal: options.signal } : {}), headers: { "X-Tempo-Work-Class": "background" } }
    : options.signal ? { signal: options.signal } : undefined;
  const status = await request(`${options.baseUrl ?? binding?.origin ?? defaultStudyClient.origin}/api/operations/${encodeURIComponent(operationId)}`, init);
  requireBinding(binding);
  if (status.status === 404 && options.allowMissing) return status;
  if (!status.ok)
    throw new PendingOperationError(operationId);
  let wire: unknown;
  try {
    wire = await status.json();
  } catch (error) {
    requireBinding(binding);
    if (error instanceof SyntaxError) throw new PendingOperationError(operationId);
    throw error;
  }
  requireBinding(binding);
  const result = decodeOperationResult(wire, operationId, binding?.client.independent ?? defaultStudyClient.independent);
  if (result.kind === "malformed" && result.reason === "identity")
    throw new PendingOperationError(operationId, "Learning did not confirm the original operation identity.");
  // A missing receipt can only be replayed by a caller retaining the original command.
  if (result.kind === "unknown" && options.allowMissing)
    return new Response(null, { status: 404 });
  if (["pending", "complete", "failed"].includes(result.kind))
    notifyOperationStatusChange(operationId);
  if (result.kind === "complete") return Response.json(result.response);
  if (result.kind === "failed")
    throw new FailedOperationError(
      result.error.message ?? "The save failed. Check the service before retrying.",
      operationId, result.error.status_code, result.error.code,
      result.error.retryable ?? (result.error.status_code === 409 ? false : undefined), result.error.detail,
    );
  if (result.kind === "blocked")
    throw new PendingOperationError(operationId,
      `Operation ${operationId} is blocked: ${result.error?.message ?? "check the local service"}. Retry it from the operation status after resolving the error.`,
      true);
  throw new PendingOperationError(operationId);
}


// Retry conflicts and delivery failures are ambiguous: resolve the same receipt
// before classifying the command, rather than treating endpoint 4xx as terminal.
export async function retryBlockedOperation(operationId: string, options: { client?: StudyClient } = {}): Promise<Response> {
  const binding = bindClient(options.client ?? defaultStudyClient)!;
  requireBinding(binding);
  try {
    await binding.client.learningFetch(`${binding.origin}/api/operations/${encodeURIComponent(operationId)}/retry`, { method: "POST" });
  } catch {
    // The retry request may have been delivered despite a lost response.
  }
  requireBinding(binding);
  return readReceipt(operationId, {}, binding);
}
