import { API_URL } from "../const";

export class PendingOperationError extends Error {
  constructor(readonly operationId: string, message?: string, readonly blocked = false) {
    super(message ?? `Save is still pending (operation ${operationId}). Retry to check its result.`);
    this.name = "PendingOperationError";
  }
}

export class FailedOperationError extends Error {
  constructor(message: string, readonly operationId: string) {
    super(message);
    this.name = "FailedOperationError";
  }
}

export async function confirmOperationResponse(response: Response): Promise<Response> {
  if (response.status !== 202) return response;
  const pending = await response.clone().json() as { operation_id?: string };
  // Existing discovery admissions also return 202, with an intent ID that
  // their own status endpoint confirms. Preserve that response for its caller.
  if (!pending.operation_id) return response;
  return readOperationResponse(pending.operation_id);
}

// Resolve a known command through the same receipt semantics as a 202 response.
export async function readOperationResponse(operationId: string): Promise<Response> {
  const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(operationId)}`);
  if (!status.ok)
    throw new PendingOperationError(operationId);
  const receipt = await status.json() as {
    state?: string;
    response?: unknown;
    error?: { message?: string };
    last_error?: { message?: string };
    message?: string;
  };
  if (receipt.state === "complete")
    return Response.json(receipt.response);
  if (receipt.state === "failed")
    throw new FailedOperationError(
      receipt.error?.message ?? "The save failed. Check the service before retrying.",
      operationId,
    );
  if (receipt.state === "blocked")
    throw new PendingOperationError(operationId,
      `Operation ${operationId} is blocked: ${receipt.last_error?.message ?? receipt.message ?? "check the local service"}. Retry it from the operation status after resolving the error.`,
      true);
  throw new PendingOperationError(operationId);
}


// Retry conflicts and delivery failures are ambiguous: resolve the same receipt
// before classifying the command, rather than treating endpoint 4xx as terminal.
export async function retryBlockedOperation(operationId: string): Promise<Response> {
  try {
    await fetch(`${API_URL}/api/operations/${encodeURIComponent(operationId)}/retry`, { method: "POST" });
  } catch {
    // The retry request may have been delivered despite a lost response.
  }
  return readOperationResponse(operationId);
}
