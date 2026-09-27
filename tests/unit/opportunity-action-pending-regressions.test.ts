import { afterEach, expect, it, vi } from "vitest";
import { applyOpportunityCommand } from "../../app/lib/opportunity-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending discovery action reuses its operation ID and blocks another action on that discovery", async () => {
  const keys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: { acknowledged: true },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    keys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(applyOpportunityCommand("white", "discovery-1", "acknowledge"))
    .rejects.toBeInstanceOf(PendingOperationError);
  await expect(applyOpportunityCommand("white", "discovery-1", "dismiss"))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(keys).toHaveLength(1);
  await applyOpportunityCommand("white", "discovery-1", "acknowledge");
  expect(keys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-opportunity-v1:discovery-1")).toBeNull();
});
