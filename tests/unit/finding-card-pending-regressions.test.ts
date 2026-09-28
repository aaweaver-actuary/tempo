import { afterEach, expect, it, vi } from "vitest";
import { prepareFindingCard } from "../../app/lib/finding-card-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("finding card preview does not save and a pending card save keeps its operation ID", async () => {
  const dispatchedKeys: string[] = [];
  let receiptReads = 0;
  const preview = {
    starting_fen: "8/8/8/8/8/8/4K3/7k w - - 0 1", moves: ["e2e3"],
    best_move: "e2e3", trained_color: "white", existing_card_id: null,
  };
  const saved = { preview: { ...preview, existing_card_id: "card-one" },
    saved: true, card_id: "card-one", reused: false };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 2 ? { state: "pending" } :
        { state: "complete", response: saved });
    }
    const body = JSON.parse(String(init?.body)) as { save: boolean };
    if (!body.save) return Response.json({ preview, saved: false });
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    dispatchedKeys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  expect((await prepareFindingCard("finding-one", { save: false })).saved).toBe(false);
  expect(dispatchedKeys).toHaveLength(0);
  await expect(prepareFindingCard("finding-one", { save: true }))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(await prepareFindingCard("finding-one", { save: true })).toEqual(saved);
  expect(dispatchedKeys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-finding-card-v1:finding-one")).toBeNull();
});
