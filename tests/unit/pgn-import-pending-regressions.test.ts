import { afterEach, expect, it, vi } from "vitest";
import { savePgnImportCommand } from "../../app/lib/pgn-import-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending PGN import reuses its operation ID and does not report a save", async () => {
  const keys: string[] = [];
  let receiptReads = 0;
  const file = new File(["[Event \"Test\"]\n\n1. e4 e5 *"], "opening.pgn");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: {
          repertoire_id: "rep", source_name: "opening.pgn", games_found: 1,
          unique_lines: 1, cards_created: 1, duplicates_merged: 0,
          cards_admitted_today: 0, integrity: {
            status: "unchecked", issue_count: 0, first_issue_id: null,
          },
          decision_cards_created: 1, shared_decisions_reused: 0,
          prefix_cards_created: 0, shared_prefixes_reused: 0,
          descendant_decision_cards_created: 1, graph_state: "refreshing",
        },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    keys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(savePgnImportCommand(file, "white", 4)).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-pgn-import-v1")).not.toBeNull();
  expect((await savePgnImportCommand(file, "white", 4)).repertoire_id).toBe("rep");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(localStorage.getItem("tempo-pending-pgn-import-v1")).toBeNull();
});
