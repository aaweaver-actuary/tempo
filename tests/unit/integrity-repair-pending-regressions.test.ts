import { afterEach, expect, it, vi } from "vitest";
import { enqueueIntegrityRepair, flushIntegrityRepairs, pendingIntegrityRepairs } from "../../app/lib/integrity-repair-outbox";

const submission = { task_id: "graph-task", task_generation: 1, repertoire_id: "rep", issue_id: "issue", state: "queued" };
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("a pending integrity repair retains its command ID until its receipt completes", async () => {
  const repair = enqueueIntegrityRepair({ repertoireId: "rep", issueId: "issue", signature: "signature", selectedMoveUci: "e2e4" });
  let receiptReads = 0;
  const fetcher = vi.fn(async () => Response.json(++receiptReads < 3 ? { state: "pending" } : { state: "complete", response: submission }));
  vi.stubGlobal("fetch", fetcher);
  await flushIntegrityRepairs(); await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: repair.operationId, phase: "saving" });
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: repair.operationId, phase: "validating" });
  expect(fetcher.mock.calls).toHaveLength(3);
});

it("SQLite integrity repair retries through its signature-deduplicated endpoint", async () => {
  const repair = enqueueIntegrityRepair({ repertoireId: "rep", issueId: "issue", signature: "signature", selectedMoveUci: "e2e4" });
  const keys: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) return Response.json({}, { status: 404 });
    keys.push(new Headers(init?.headers).get("Idempotency-Key")!);
    if (keys.length === 1) throw new Error("response lost after enqueue");
    return Response.json(submission, { status: 202 });
  }));
  await flushIntegrityRepairs();
  vi.spyOn(Date, "now").mockReturnValue(Date.now() + 60_000);
  await flushIntegrityRepairs();
  expect(keys).toEqual([repair.operationId, repair.operationId]);
  expect(pendingIntegrityRepairs()[0].phase).toBe("validating");
});
