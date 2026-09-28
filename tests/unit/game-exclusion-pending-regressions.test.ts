import { afterEach, expect, it, vi } from "vitest";
import { setGameExclusion } from "../../app/lib/game-exclusion-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("game exclusion keeps its operation ID and never treats a pending receipt as saved", async () => {
  const dispatchedKeys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 4 ? { state: "pending" } : {
        state: "complete", response: { game_id: "provider:one", excluded: true },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    dispatchedKeys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(setGameExclusion("provider:one", true)).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-game-exclusion-v1:provider:one")).not.toBeNull();
  await expect(setGameExclusion("provider:one", true)).rejects.toBeInstanceOf(PendingOperationError);
  await setGameExclusion("provider:one", true);
  expect(dispatchedKeys).toHaveLength(2);
  expect(dispatchedKeys[0]).toBe(dispatchedKeys[1]);
  expect(localStorage.getItem("tempo-pending-game-exclusion-v1:provider:one")).toBeNull();
});

it("a different game exclusion decision waits for the prior receipt", async () => {
  const dispatched = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json(
    { operation_id: "exclusion-first", state: "pending" }, { status: 202 },
  ));
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) return Response.json({ state: "pending" });
    return dispatched(input, init);
  }));
  await expect(setGameExclusion("provider:one", true)).rejects.toBeInstanceOf(PendingOperationError);
  await expect(setGameExclusion("provider:one", false)).rejects.toBeInstanceOf(PendingOperationError);
  expect(dispatched).toHaveBeenCalledTimes(1);
});
