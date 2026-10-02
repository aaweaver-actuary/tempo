import * as z from "zod";
import { API_URL } from "../const";
import { canonicalPrefixSchema, canonicalPrefixPreviewAdmissionSchema } from "../domain/canonical-prefix";
import { readJsonResponse } from "./validated-data";
import { confirmOperationResponse, FailedOperationError, PendingOperationError } from "./operation-status";

const pendingSchema = z.strictObject({ operationId: z.string().min(1), body: z.record(z.string(), z.union([z.string(), z.number()])) });
type PrefixAction = "preview" | "save";
const storageKey = (repertoireId: string, action: PrefixAction) => `tempo-canonical-prefix-${action}-v1:${repertoireId}`;

async function sendPrefixCommand(repertoireId: string, action: PrefixAction, body: z.infer<typeof pendingSchema>["body"]): Promise<Response> {
  const key = storageKey(repertoireId, action);
  const rawPending = localStorage.getItem(key);
  let pending = rawPending ? pendingSchema.parse(JSON.parse(rawPending)) : null;
  if (pending) {
    let receipt: { state?: string; response?: unknown; error?: { message?: string }; last_error?: { message?: string } } = { state: "unknown" };
    try {
      const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
      if (status.ok) receipt = await status.json();
    } catch {
      // A lost send may have no receipt. Replay the original identity and payload.
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(key);
      if (JSON.stringify(pending.body) === JSON.stringify(body)) return Response.json(receipt.response);
      pending = null;
    } else if (receipt.state === "failed") {
      localStorage.removeItem(key);
      throw new Error(receipt.error?.message ?? "The earlier prefix operation failed. Check the prefix again.");
    } else if (receipt.state === "blocked") {
      throw new PendingOperationError(pending.operationId, receipt.last_error?.message, true);
    } else if (JSON.stringify(pending.body) !== JSON.stringify(body)) {
      throw new PendingOperationError(pending.operationId, "An earlier prefix operation is still pending. Retry to check its result.");
    }
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), body };
    localStorage.setItem(key, JSON.stringify(pending));
  }
  try {
    const sent = await fetch(
      `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/canonical-prefix${action === "preview" ? "/preview" : ""}`, {
        method: action === "preview" ? "POST" : "PUT",
        headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
        body: JSON.stringify(pending.body),
      },
    );
    if (sent.status >= 500) throw new PendingOperationError(pending.operationId, "The service could not confirm this prefix operation. Retry to recover its result.");
    const response = await confirmOperationResponse(sent);
    localStorage.removeItem(key);
    return response;
  } catch (error) {
    if (error instanceof FailedOperationError) localStorage.removeItem(key);
    throw error;
  }
}

export async function requestCanonicalPrefixPreview(repertoireId: string, movetext: string) {
  return readJsonResponse(await sendPrefixCommand(repertoireId, "preview", { movetext }), canonicalPrefixPreviewAdmissionSchema, "canonical prefix preview");
}
export async function saveCanonicalPrefixCommand(repertoireId: string, previewId: string, expectedRevision: number) {
  return readJsonResponse(await sendPrefixCommand(repertoireId, "save", { preview_id: previewId, expected_revision: expectedRevision }), canonicalPrefixSchema, "canonical prefix save");
}
export async function recoverCanonicalPrefixSave(repertoireId: string): Promise<void> {
  const rawPending = localStorage.getItem(storageKey(repertoireId, "save"));
  if (!rawPending) return;
  const pending = pendingSchema.parse(JSON.parse(rawPending));
  await readJsonResponse(await sendPrefixCommand(repertoireId, "save", pending.body), canonicalPrefixSchema, "canonical prefix save");
}

export async function recoverCanonicalPrefixPreview(repertoireId: string) {
  const rawPending = localStorage.getItem(storageKey(repertoireId, "preview"));
  if (!rawPending) return undefined;
  const pending = pendingSchema.parse(JSON.parse(rawPending));
  return readJsonResponse(await sendPrefixCommand(repertoireId, "preview", pending.body), canonicalPrefixPreviewAdmissionSchema, "canonical prefix preview");
}
