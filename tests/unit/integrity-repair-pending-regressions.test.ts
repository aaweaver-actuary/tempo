import { afterEach, expect, it, vi } from "vitest";
import { enqueueIntegrityRepair, pendingIntegrityRepairs, flushIntegrityRepairs, retryIntegrityRepair } from "../../app/lib/integrity-repair-outbox";

const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const choice = { repertoireId: "rep", issueId: "issue", signature: "signature", selectedMoveUci: "e2e4" };
const integrity = { repertoire_id: "rep", status: "needs_repair", issue_count: 1, first_issue_id: "issue",
  scan_status: "idle", scan_generation: "scan:1", scan_progress: { completed: 1, total: 1 }, last_scan_error: null,
  issues: [{ id: "issue", kind: "multiple_responses", fen_key: startingFen.split(" ").slice(0, 4).join(" "),
    fen: startingFen, trained_color: "white", signature: "signature", moves: [], sources: [] }] };
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("legacy direct integrity repair retries through its signature-deduplicated endpoint", async () => {
  let posts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) return Response.json({}, { status: 404 });
    if (init?.method === "POST") {
      posts += 1;
      if (posts === 1) throw new Error("response lost after enqueue");
      return Response.json({ task_id: "repair-task", repertoire_id: "rep", issue_id: "issue", state: "queued" });
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

it.each(["missing", "foreign"])("C14 %s integrity 202 cannot adopt a task before the original receipt", async kind => {
  let operationId = "", confirmed = false;
  const reads: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/operations/")) {
      reads.push(decodeURIComponent(url.split("/").pop()!));
      return confirmed ? Response.json({ operation_id: operationId, state: "complete", response: {
        task_id: "original-task", repertoire_id: "rep", issue_id: "issue", state: "queued" } })
        : Response.json({ state: "unknown" });
    }
    if (init?.method === "POST") {
      operationId = new Headers(init.headers).get("Idempotency-Key")!;
      return Response.json({ task_id: "foreign-task", repertoire_id: "rep", issue_id: "issue", state: "queued",
        ...(kind === "foreign" ? { operation_id: "foreign" } : {}) }, { status: 202 });
    }
    return Response.json(integrity);
  }));
  const saved = enqueueIntegrityRepair(choice);
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, ...choice });
  expect(pendingIntegrityRepairs()[0].taskId).toBeUndefined();
  expect(reads).toEqual([saved.operationId]);
  confirmed = true;
  vi.spyOn(Date, "now").mockReturnValue(Date.now() + 3001);
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, taskId: "original-task" });
  expect(reads.every(id => id === saved.operationId)).toBe(true);
});

it("C14 matching integrity 202 resolves only its original operation receipt", async () => {
  let savedId = "", sent = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      expect(decodeURIComponent(String(input).split("/").pop()!)).toBe(savedId);
      return sent ? Response.json({ operation_id: savedId, state: "complete", response: {
        task_id: "original-task", repertoire_id: "rep", issue_id: "issue", state: "queued" } })
        : Response.json({ state: "unknown" });
    }
    if (init?.method === "POST") {
      expect(new Headers(init.headers).get("Idempotency-Key")).toBe(savedId);
      sent = true;
      return Response.json({ operation_id: savedId, state: "queued" }, { status: 202 });
    }
    return Response.json(integrity);
  }));
  const saved = enqueueIntegrityRepair(choice); savedId = saved.operationId;
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: savedId, taskId: "original-task" });
});


it.each(["own", "missing", "foreign", "malformed"])("C14 %s operation-retry 202 retains original identity and receipt recovery", async kind => {
  let id = "", advanced = false;
  const retries: string[] = [], reads: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/retry") && init?.method === "POST") {
      retries.push(decodeURIComponent(url.split("/").at(-2)!));
      if (kind === "malformed") return new Response("not-json", { status: 202 });
      return Response.json({ state: "blocked", ...(kind === "own" ? { operation_id: id }
        : kind === "foreign" ? { operation_id: "foreign" } : {}) }, { status: 202 });
    }
    if (url.includes("/api/operations/")) {
      reads.push(decodeURIComponent(url.split("/").pop()!));
      return advanced ? Response.json({ operation_id: id, state: "complete", retry_cycle: 2,
        response: { task_id: "original-task", repertoire_id: "rep", issue_id: "issue", state: "queued" } })
        : Response.json({ operation_id: id, state: "blocked", retry_cycle: 1, attempt_count: 3 });
    }
    throw new Error(`Unexpected request ${url}`);
  }));
  const saved = enqueueIntegrityRepair(choice); id = saved.operationId;
  await flushIntegrityRepairs();
  await retryIntegrityRepair(id);
  const held = pendingIntegrityRepairs()[0];
  expect(held).toMatchObject({ operationId: id, ...choice, phase: "saving", retry: { kind: "operation", deliveryAccepted: kind === "own" } });
  expect(retries).toEqual([id]);
  advanced = true;
  vi.spyOn(Date, "now").mockReturnValue(Date.now() + 31_000);
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: id, phase: "validating", taskId: "original-task" });
  expect(reads.every(read => read === id)).toBe(true);
  expect(retries).toEqual([id]);
});

it('C14 foreign retry acknowledgment permits replay of the same blocked operation', async () => {
  let id = '', posts = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/retry') && init?.method === 'POST') {
      expect(decodeURIComponent(url.split('/').at(-2)!)).toBe(id);
      posts++;
      return Response.json({ operation_id: posts === 1 ? 'foreign' : id, state: 'blocked' }, { status: 202 });
    }
    return Response.json({ operation_id: id, state: 'blocked', retry_cycle: 1, attempt_count: 3 });
  }));
  id = enqueueIntegrityRepair(choice).operationId;
  await flushIntegrityRepairs(); await retryIntegrityRepair(id);
  expect(pendingIntegrityRepairs()[0].retry).toMatchObject({ kind: 'operation', deliveryAccepted: false });
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 31_000);
  await flushIntegrityRepairs();
  expect(posts).toBe(2);
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: id, retry: { deliveryAccepted: true } });
});
