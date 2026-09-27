import { afterEach, expect, it, vi } from "vitest";
import { saveAnalysisPasteCommand } from "../../app/lib/analysis-paste-command";
import { PendingOperationError } from "../../app/lib/operation-status";

const payload = {
  text: "1. e4",
  starting_fen: null,
  preview_token: "preview-1",
  selections: [{ index: 0, repertoire_id: "white", acknowledge_conflict: false }],
};
const completed = {
  saved: [{ index: 0, repertoire_id: "white", duplicate: false, conflict: false }],
  affected_repertoire_ids: ["white"], gap_resolved: false,
};

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending analysis paste save reuses its operation ID and blocks a changed selection", async () => {
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
  await expect(saveAnalysisPasteCommand(payload)).rejects.toBeInstanceOf(PendingOperationError);
  await expect(saveAnalysisPasteCommand({ ...payload, text: "1. d4" }))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(keys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-analysis-paste-v1")).not.toBeNull();
  expect((await saveAnalysisPasteCommand(payload)).affected_repertoire_ids).toEqual(["white"]);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(localStorage.getItem("tempo-pending-analysis-paste-v1")).toBeNull();
});
