import { API_URL } from "../const";
import { integrityRepairSubmissionSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-integrity-repair-v1";
type PendingRepair = { operationId: string; fingerprint: string };

export async function saveIntegrityRepairCommand(
  repertoireId: string, issueId: string, signature: string, selectedMoveUci: string,
) {
  const body = JSON.stringify({ signature, selected_move_uci: selectedMoveUci });
  const fingerprint = JSON.stringify([repertoireId, issueId, body]);
  const stored = localStorage.getItem(PENDING_KEY);
  let pending: PendingRepair | null = stored ? JSON.parse(stored) as PendingRepair : null;
  if (pending) {
    if (!pending.operationId || !pending.fingerprint)
      throw new Error("The pending integrity repair is invalid. Restore browser data before retrying.");
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    // The current SQLite product has no operation endpoint; its durable repair
    // task is deduplicated by issue signature, so the same POST remains safe.
    if (status.status !== 404) {
      if (!status.ok) throw new PendingOperationError(pending.operationId);
      const receipt = await status.json() as {
        state?: string; response?: unknown; error?: { message?: string };
      };
      if (receipt.state === "failed") {
        localStorage.removeItem(PENDING_KEY);
        throw new Error(receipt.error?.message ?? "The earlier integrity repair failed.");
      }
      if (receipt.state === "complete") {
        localStorage.removeItem(PENDING_KEY);
        if (pending.fingerprint === fingerprint)
          return integrityRepairSubmissionSchema.parse(receipt.response);
        pending = null;
      } else if (receipt.state === "pending") {
        if (pending.fingerprint !== fingerprint) throw new PendingOperationError(pending.operationId);
      } else throw new PendingOperationError(pending.operationId);
    } else if (pending.fingerprint !== fingerprint) {
      throw new PendingOperationError(pending.operationId);
    }
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), fingerprint };
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }
  let response = await fetch(
    `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/integrity/issues/${encodeURIComponent(issueId)}/resolve`,
    { method: "POST", headers: {
      "Content-Type": "application/json", "Idempotency-Key": pending.operationId,
    }, body },
  );
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, integrityRepairSubmissionSchema, "integrity repair");
  localStorage.removeItem(PENDING_KEY);
  return result;
}
