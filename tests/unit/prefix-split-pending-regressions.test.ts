import { afterEach, expect, it, vi } from "vitest";
import { acceptPrefixSplitCommand, rejectPrefixSplitCommand } from "../../app/lib/prefix-split-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending prefix split reuses its command ID and blocks a conflicting rejection", async () => {
  const commandKeys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json({ state: receiptReads < 3 ? "pending" : "complete", response: { rejected_after_review_id: 7 } });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    commandKeys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(rejectPrefixSplitCommand("card-1", 3)).rejects.toBeInstanceOf(PendingOperationError);
  await expect(acceptPrefixSplitCommand("card-1", 3)).rejects.toBeInstanceOf(PendingOperationError);
  expect(commandKeys).toHaveLength(1);
  expect((await rejectPrefixSplitCommand("card-1", 3)).rejected_after_review_id).toBe(7);
  expect(commandKeys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-prefix-split-v1")).toBeNull();
});
