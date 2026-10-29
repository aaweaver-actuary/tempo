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

const accepted = {
  source_card_id: "combined", source_revision: 1, applied: true, idempotent: false,
  parent: { card_id: "short", starting_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", moves: ["e2e4"], tested_player_moves: 1 },
  continuation: { card_id: "tail", starting_fen: "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", moves: ["g1f3"], tested_player_moves: 1 },
};

it("manual acceptance retains its displayed attempt identity across pending retries", async () => {
  const bodies: unknown[] = [];
  let reads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      reads += 1;
      return Response.json({ state: reads < 3 ? "pending" : "complete", response: accepted });
    }
    bodies.push(JSON.parse(String(init?.body)));
    return Response.json({ operation_id: "saved-split", state: "pending" }, { status: 202 });
  }));
  await expect(acceptPrefixSplitCommand("combined", 1, 17)).rejects.toBeInstanceOf(PendingOperationError);
  await expect(acceptPrefixSplitCommand("combined", 1, 18)).rejects.toBeInstanceOf(PendingOperationError);
  expect(bodies).toEqual([{ expected_revision: 1, queue_entry_id: 17 }]);
  expect((await acceptPrefixSplitCommand("combined", 1, 17)).applied).toBe(true);
  expect(bodies).toHaveLength(1);
});

it("legacy split callers keep their original request payload", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    expect(JSON.parse(String(init?.body))).toEqual({ expected_revision: 1 });
    return Response.json(accepted);
  }));
  expect((await acceptPrefixSplitCommand("combined", 1)).applied).toBe(true);
});
