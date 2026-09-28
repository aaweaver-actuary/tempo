import { afterEach, expect, it, vi } from "vitest";
import { startEndgameAttempt } from "../../app/lib/endgame-attempt-command";
import { PendingOperationError } from "../../app/lib/operation-status";

const completed = {
  id: "attempt", fen: "8/8/8/8/8/8/4k3/4K3 w - - 0 1", target: "draw", moves: [],
};

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending endgame attempt keeps its operation ID and does not create another position", async () => {
  const keys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: completed,
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    keys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(startEndgameAttempt("template")).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-endgame-attempt-v1")).not.toBeNull();
  expect((await startEndgameAttempt("template")).id).toBe("attempt");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(localStorage.getItem("tempo-pending-endgame-attempt-v1")).toBeNull();
});
