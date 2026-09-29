import { API_URL } from "../const";

export class PendingOperationError extends Error {
  constructor(readonly operationId: string) {
    super(`Save is still pending (operation ${operationId}). Retry to check its result.`);
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
  const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operation_id)}`);
  if (!status.ok)
    throw new PendingOperationError(pending.operation_id);
  const receipt = await status.json() as {
    state?: string;
    response?: unknown;
    error?: { message?: string };
  };
  if (receipt.state === "complete")
    return Response.json(receipt.response);
  if (receipt.state === "failed")
    throw new FailedOperationError(
      receipt.error?.message ?? "The save failed. Check the service before retrying.",
      pending.operation_id,
    );
  throw new PendingOperationError(pending.operation_id);
}
