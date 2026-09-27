import { afterEach, expect, it, vi } from "vitest";
import { admitEndgameTemplate } from "../../app/lib/endgame-template-command";
import { PendingOperationError } from "../../app/lib/operation-status";

const request = {
  name: "King and rook", white_material: "KR", black_material: "K",
  trained_color: "white", goal_mix: "both",
};
const completed = {
  id: "template", card_id: "card",
  sample_fen: "8/8/8/8/8/8/4k3/4K3 w - - 0 1", already_exists: false,
};

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("endgame admission remains pending and reuses its operation ID", async () => {
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
  await expect(admitEndgameTemplate(request)).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-endgame-template-v1")).not.toBeNull();
  expect((await admitEndgameTemplate(request)).card_id).toBe("card");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(localStorage.getItem("tempo-pending-endgame-template-v1")).toBeNull();
});
