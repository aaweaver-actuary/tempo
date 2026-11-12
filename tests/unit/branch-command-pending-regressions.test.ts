import { afterEach, expect, it, vi } from "vitest";
import { saveBranchCommand } from "../../app/lib/branch-command";
import { PendingOperationError } from "../../app/lib/operation-status";

const payload = {
  repertoire_id: "rep",
  starting_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  moves: ["e2e4"], trained_color: "white", name: "e4",
};
const completed = {
  id: "line", duplicate: false, moves: ["e2e4"],
  integrity: { status: "unchecked", issue_count: 0, first_issue_id: null },
};

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending branch save keeps its operation ID and cannot appear saved", async () => {
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
  await expect(saveBranchCommand(payload)).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-repertoire-branch-v1")).not.toBeNull();
  expect((await saveBranchCommand(payload)).id).toBe("line");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(localStorage.getItem("tempo-pending-repertoire-branch-v1")).toBeNull();
});

it.each([
  ["malformed completion", { state: "complete", response: {} }],
  ["foreign completion", { state: "complete", operation_id: "foreign", response: completed }],
])("%s retains the exact earlier operation when a changed request arrives", async (_label, receipt) => {
  const original = JSON.stringify({ operationId: "original-operation", body: JSON.stringify(payload) });
  localStorage.setItem("tempo-pending-repertoire-branch-v1", original);
  const fetcher = vi.fn(async () => Response.json(receipt));
  vi.stubGlobal("fetch", fetcher);
  await expect(saveBranchCommand({ ...payload, name: "changed" })).rejects.toThrow();
  expect(localStorage.getItem("tempo-pending-repertoire-branch-v1")).toBe(original);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("keeps changed work blocked while pending and clears a confirmed terminal failure", async () => {
  const original = JSON.stringify({ operationId: "original-operation", body: JSON.stringify(payload) });
  localStorage.setItem("tempo-pending-repertoire-branch-v1", original);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ state: "pending" }))
    .mockResolvedValueOnce(Response.json({ state: "failed", error: { message: "terminal" } })));
  await expect(saveBranchCommand({ ...payload, name: "changed" })).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-repertoire-branch-v1")).toBe(original);
  await expect(saveBranchCommand(payload)).rejects.toThrow("terminal");
  expect(localStorage.getItem("tempo-pending-repertoire-branch-v1")).toBeNull();
});
