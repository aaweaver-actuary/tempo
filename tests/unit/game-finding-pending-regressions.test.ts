import { afterEach, expect, it, vi } from "vitest";
import { curateGameFinding, decideGameFinding } from "../../app/lib/game-finding-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending finding decision keeps its operation ID and blocks another action", async () => {
  const dispatchedKeys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: {
          id: "finding-one", status: "accepted", scheduling: null, queued: true,
        },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    dispatchedKeys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(decideGameFinding("finding-one", "accepted"))
    .rejects.toBeInstanceOf(PendingOperationError);
  await expect(curateGameFinding("finding-one", "ignore"))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(dispatchedKeys).toHaveLength(1);
  const saved = await decideGameFinding("finding-one", "accepted");
  expect(saved).toEqual({ id: "finding-one", status: "accepted", scheduling: null, queued: true });
  expect(localStorage.getItem("tempo-pending-finding-v1:finding-one")).toBeNull();
});
