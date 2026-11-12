import { afterEach, expect, it, vi } from "vitest";
import { renameRepertoireCommand } from "../../app/lib/repertoire-rename-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending repertoire rename reuses its operation ID and waits for a receipt", async () => {
  const keys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: { id: "rep", name: "New name" },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    keys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(renameRepertoireCommand("rep", "New name")).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-repertoire-rename-v1")).not.toBeNull();
  await renameRepertoireCommand("rep", "New name");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(localStorage.getItem("tempo-pending-repertoire-rename-v1")).toBeNull();
});

it.each([
  ["malformed", { state: "complete" }],
  ["foreign", { state: "complete", operation_id: "other", response: { id: "rep", name: "old" } }],
])("%s receipt preserves the saved operation", async (_label, receipt) => {
  const original = JSON.stringify({ operationId: "original", repertoireId: "rep", name: "old" });
  localStorage.setItem("tempo-pending-repertoire-rename-v1", original);
  const fetcher = vi.fn(async () => Response.json(receipt));
  vi.stubGlobal("fetch", fetcher);
  await expect(renameRepertoireCommand("rep", "new")).rejects.toThrow();
  expect(localStorage.getItem("tempo-pending-repertoire-rename-v1")).toBe(original);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
