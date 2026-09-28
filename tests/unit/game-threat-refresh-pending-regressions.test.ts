import { afterEach, expect, it, vi } from "vitest";
import { requestGameThreatRefresh } from "../../app/lib/game-threat-refresh-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("manual defensive scan retains its operation ID until admission is confirmed", async () => {
  const dispatchedKeys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 4 ? { state: "pending" } : {
        state: "complete", response: { status: "queued", analysis_version: 3 },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    dispatchedKeys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(requestGameThreatRefresh("provider:one")).rejects.toBeInstanceOf(PendingOperationError);
  await expect(requestGameThreatRefresh("provider:one")).rejects.toBeInstanceOf(PendingOperationError);
  await requestGameThreatRefresh("provider:one");
  expect(dispatchedKeys).toHaveLength(2);
  expect(dispatchedKeys[0]).toBe(dispatchedKeys[1]);
  expect(localStorage.getItem("tempo-pending-game-threat-refresh-v1:provider:one")).toBeNull();
});
