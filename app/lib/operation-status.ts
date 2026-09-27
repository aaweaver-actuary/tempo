import { API_URL } from "../const";

export class PendingOperationError extends Error {
  constructor(readonly operationId: string) {
    super(`Save is still pending (operation ${operationId}). Retry to check its result.`);
    this.name = "PendingOperationError";
  }
}

export async function confirmOperationResponse(response: Response): Promise<Response> {
  if (response.status !== 202) return response;
  const pending = await response.json() as { operation_id?: string };
  if (!pending.operation_id)
    throw new Error("The service accepted a save without an operation ID. Retry with the same idempotency key.");
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
    throw new Error(receipt.error?.message ?? "The save failed. Check the service before retrying.");
  throw new PendingOperationError(pending.operation_id);
}
