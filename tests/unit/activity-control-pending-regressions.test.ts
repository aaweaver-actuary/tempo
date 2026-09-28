import { afterEach, expect, it, vi } from "vitest";
import { requestActivityControl } from "../../app/lib/activity-control-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending activity control retains its ID and blocks an overtaking action", async () => {
  const dispatchedKeys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: { ok: true },
      });
    }
    const idempotencyKey = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    dispatchedKeys.push(idempotencyKey);
    return Response.json({ operation_id: idempotencyKey, state: "pending" }, { status: 202 });
  }));
  await expect(requestActivityControl("durable", "job-1", "pause"))
    .rejects.toBeInstanceOf(PendingOperationError);
  await expect(requestActivityControl("durable", "job-1", "resume"))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(dispatchedKeys).toHaveLength(1);
  await requestActivityControl("durable", "job-1", "pause");
  expect(dispatchedKeys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-activity-control-v1:durable:job-1")).toBeNull();
});
