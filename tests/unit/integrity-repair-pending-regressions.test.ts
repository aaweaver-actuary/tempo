import { afterEach, expect, it, vi } from "vitest";
import { saveIntegrityRepairCommand } from "../../app/lib/integrity-repair-command";
import { PendingOperationError } from "../../app/lib/operation-status";

const submission = {
  task_id: "graph-task", repertoire_id: "rep", issue_id: "issue",
  state: "queued",
};

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending integrity repair retains its command ID until its receipt completes", async () => {
  const keys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: submission,
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    keys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(saveIntegrityRepairCommand("rep", "issue", "signature", "e2e4"))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect((await saveIntegrityRepairCommand("rep", "issue", "signature", "e2e4")).task_id)
    .toBe("graph-task");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(localStorage.getItem("tempo-pending-integrity-repair-v1")).toBeNull();
});

it("SQLite integrity repair retries through its signature-deduplicated endpoint", async () => {
  let posts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/api/operations/"))
      return Response.json({ detail: "not available" }, { status: 404 });
    posts += 1;
    if (posts === 1) throw new Error("response lost after enqueue");
    return Response.json(submission, { status: 202 });
  }));
  await expect(saveIntegrityRepairCommand("rep", "issue", "signature", "e2e4"))
    .rejects.toThrow("response lost");
  expect((await saveIntegrityRepairCommand("rep", "issue", "signature", "e2e4")).task_id)
    .toBe("graph-task");
  expect(posts).toBe(2);
});
