import { afterEach, expect, it, vi } from "vitest";
import { enqueueIntegrityRepair, pendingIntegrityRepairs, flushIntegrityRepairs } from "../../app/lib/integrity-repair-outbox";

const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const choice = { repertoireId: "rep", issueId: "issue", signature: "signature", selectedMoveUci: "e2e4" };
const integrity = { repertoire_id: "rep", status: "needs_repair", issue_count: 1, first_issue_id: "issue",
  scan_status: "idle", scan_generation: "scan:1", scan_progress: { completed: 1, total: 1 }, last_scan_error: null,
  issues: [{ id: "issue", kind: "multiple_responses", fen_key: startingFen.split(" ").slice(0, 4).join(" "),
    fen: startingFen, trained_color: "white", signature: "signature", moves: [], sources: [] }] };
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("SQLite integrity repair retries through its signature-deduplicated endpoint", async () => {
  let posts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) return Response.json({}, { status: 404 });
    if (init?.method === "POST") {
      posts += 1;
      if (posts === 1) throw new Error("response lost after enqueue");
      return Response.json({ task_id: "repair-task", repertoire_id: "rep", issue_id: "issue", state: "queued" }, { status: 202 });
    }
    return Response.json(integrity);
  }));
  const saved = enqueueIntegrityRepair(choice);
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, error: "response lost after enqueue" });
  vi.spyOn(Date, "now").mockReturnValue(Date.now() + 3_001);
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, taskId: "repair-task" });
  expect(posts).toBe(2);
});

it("hung repair submission times out and releases service to another repertoire without losing identity", async () => {
  vi.useFakeTimers();
  const submittedKeys: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/operations/")) return Response.json({ state: "unknown" });
    if (init?.method === "POST") {
      submittedKeys.push(new Headers(init.headers).get("Idempotency-Key")!);
      if (url.includes("/other/")) return Response.json({ task_id: "other-task", repertoire_id: "other", issue_id: "issue", state: "queued" });
      return new Promise<Response>((_resolve, reject) => init.signal!.addEventListener("abort", () => reject(new Error("Request timed out"))));
    }
    return Response.json({ ...integrity, repertoire_id: url.includes("/other/") ? "other" : "rep" });
  }));
  const saved = enqueueIntegrityRepair(choice);
  enqueueIntegrityRepair({ ...choice, repertoireId: "other" });
  const firstFlush = flushIntegrityRepairs();
  expect(flushIntegrityRepairs()).toBe(firstFlush);
  await vi.advanceTimersByTimeAsync(15_000); await firstFlush;
  expect(submittedKeys).toHaveLength(2);
  expect(pendingIntegrityRepairs().find(repair => repair.repertoireId === "rep")).toMatchObject({ operationId: saved.operationId, error: "Request timed out" });
  expect(pendingIntegrityRepairs().find(repair => repair.repertoireId === "other")?.phase).toBe("validating");
});

it("repair request deadlines include stalled response bodies without losing the saved choice", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response(new ReadableStream({
    start(controller) { init?.signal?.addEventListener("abort", () => controller.error(new Error("Body timed out"))); },
  }))));
  const saved = enqueueIntegrityRepair(choice);
  const flushing = flushIntegrityRepairs();
  await vi.advanceTimersByTimeAsync(15_000); await flushing;
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, error: "Body timed out" });
});
