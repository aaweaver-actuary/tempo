import { afterEach, expect, it, vi } from "vitest";
import { deleteRepertoireCommand } from "../../app/lib/repertoire-delete-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending repertoire deletion cannot remove another repertoire and reuses its operation ID", async () => {
  const commandKeys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: { deleted: true, id: "white" },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    commandKeys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(deleteRepertoireCommand("white")).rejects.toBeInstanceOf(PendingOperationError);
  await expect(deleteRepertoireCommand("black")).rejects.toBeInstanceOf(PendingOperationError);
  expect(commandKeys).toHaveLength(1);
  await deleteRepertoireCommand("white");
  expect(commandKeys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-repertoire-delete-v1")).toBeNull();
});
