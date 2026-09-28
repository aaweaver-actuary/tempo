import { afterEach, expect, it, vi } from "vitest";
import { requestOpportunityRefresh } from "../../app/lib/opportunity-refresh-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending discovery refresh keeps one command ID until its queue receipt completes", async () => {
  const dispatchedKeys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 4 ? { state: "pending" } : {
        state: "complete", response: { queued: true },
      });
    }
    const idempotencyKey = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    dispatchedKeys.push(idempotencyKey);
    return Response.json({ operation_id: idempotencyKey, state: "pending" }, { status: 202 });
  }));
  await expect(requestOpportunityRefresh("rep-1")).rejects.toBeInstanceOf(PendingOperationError);
  await expect(requestOpportunityRefresh("rep-1")).rejects.toBeInstanceOf(PendingOperationError);
  await requestOpportunityRefresh("rep-1");
  expect(dispatchedKeys).toHaveLength(2);
  expect(dispatchedKeys[0]).toBe(dispatchedKeys[1]);
  expect(localStorage.getItem("tempo-pending-opportunity-refresh-v1:rep-1")).toBeNull();
});
