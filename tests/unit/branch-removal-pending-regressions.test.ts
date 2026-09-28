import { afterEach, expect, it, vi } from "vitest";
import { removeBranchCommand } from "../../app/lib/branch-removal-command";
import { PendingOperationError } from "../../app/lib/operation-status";

const payload = {
  repertoire_id: "white",
  starting_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  moves: ["e2e4"],
};
const completed = {
  deleted_line_count: 1, deleted_card_count: 0, retained_line_count: 2,
  integrity: { status: "unchecked", issue_count: 0, first_issue_id: null },
};

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending branch removal reuses its operation ID and cannot delete a changed route", async () => {
  const keys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 4 ? { state: "pending" } : {
        state: "complete", response: completed,
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    keys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(removeBranchCommand(payload)).rejects.toBeInstanceOf(PendingOperationError);
  await expect(removeBranchCommand({ ...payload, moves: ["d2d4"] }))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(keys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-branch-removal-v1")).not.toBeNull();
  expect((await removeBranchCommand(payload)).deleted_line_count).toBe(1);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(localStorage.getItem("tempo-pending-branch-removal-v1")).toBeNull();
});
