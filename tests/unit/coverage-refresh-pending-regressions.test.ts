import { afterEach, expect, it, vi } from "vitest";
import { requestCoverageRefresh } from "../../app/lib/coverage-refresh-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending coverage refresh keeps its command ID until the build is queued", async () => {
  const dispatchedKeys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 4 ? { state: "pending" } : {
        state: "complete", response: { run_id: "run-one", status: "queued" },
      });
    }
    const operationId = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    dispatchedKeys.push(operationId);
    return Response.json({ operation_id: operationId, state: "pending" }, { status: 202 });
  }));
  await expect(requestCoverageRefresh("rep")).rejects.toBeInstanceOf(PendingOperationError);
  await expect(requestCoverageRefresh("rep")).rejects.toBeInstanceOf(PendingOperationError);
  await requestCoverageRefresh("rep");
  expect(dispatchedKeys).toHaveLength(2);
  expect(dispatchedKeys[0]).toBe(dispatchedKeys[1]);
  expect(localStorage.getItem("tempo-pending-coverage-refresh-v1:rep")).toBeNull();
});
