import { afterEach, expect, it, vi } from "vitest";
import { enqueueIntegrityRepair, flushIntegrityRepairs, pendingIntegrityRepairs, retryIntegrityRepair,
  INTEGRITY_REPAIR_CONFIRMED } from "../../app/lib/integrity-repair-outbox";
const choice = { repertoireId: "rep", issueId: "issue", signature: "signature", selectedMoveUci: "e2e4" };
const submission = { task_id: "graph", task_generation: 2, repertoire_id: "rep", issue_id: "issue", state: "queued" };
const complete = { task_id: "graph", task_generation: 2, state: "complete", issue_count: 0, reason: null, retry_task_id: null };
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("repair outbox survives reload and confirms the same operation once", async () => {
  const repair = enqueueIntegrityRepair(choice); let posts = 0; let delivered = false;
  const confirmed = vi.fn(); window.addEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) return Response.json(delivered ?
      { state: "complete", response: submission } : { state: "unknown" });
    if (init?.method === "POST") { posts++; delivered = true; throw new Error("response lost"); }
    expect(new Headers(init?.headers).get("X-Tempo-Work-Class")).toBe("background");
    return Response.json(complete);
  }));
  try {
    await flushIntegrityRepairs();
    expect(pendingIntegrityRepairs()[0].operationId).toBe(repair.operationId);
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + 60_000);
    await flushIntegrityRepairs();
    expect(pendingIntegrityRepairs()[0].phase).toBe("validating");
    await flushIntegrityRepairs(); await flushIntegrityRepairs();
    expect(posts).toBe(1); expect(confirmed).toHaveBeenCalledOnce(); expect(pendingIntegrityRepairs()).toEqual([]);
  } finally { window.removeEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed); }
});

it("repair acceptance does not announce publication success", async () => {
  enqueueIntegrityRepair(choice);
  vi.stubGlobal("fetch", vi.fn(async input => String(input).includes("operations") ?
    Response.json({ state: "complete", response: submission }) : Response.json({ ...complete, state: "waiting" })));
  await flushIntegrityRepairs(); await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ phase: "validating", taskGeneration: 2 });
});

it("a pending repair receipt is polled without resubmitting or changing its operation key", async () => {
  const repair = enqueueIntegrityRepair(choice);
  const fetcher = vi.fn(async () => Response.json({ state: "pending" })); vi.stubGlobal("fetch", fetcher);
  await flushIntegrityRepairs(); await flushIntegrityRepairs();
  expect(fetcher.mock.calls).toHaveLength(2);
  expect(pendingIntegrityRepairs()[0].operationId).toBe(repair.operationId);
});

it("legacy repair choice migration retains the original request and operation key", () => {
  localStorage.setItem("tempo-pending-integrity-repair-v1", JSON.stringify({ operationId: "original",
    fingerprint: JSON.stringify(["rep", "issue", JSON.stringify({ signature: "signature", selected_move_uci: "e2e4" })]) }));
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ ...choice, operationId: "original" });
  expect(localStorage.getItem("tempo-pending-integrity-repair-v1")).toBeNull();
});

it("malformed repair storage is retained and cannot be replaced by a new save", () => {
  localStorage.setItem("tempo-pending-integrity-repairs-v2", "bad data");
  expect(() => enqueueIntegrityRepair(choice)).toThrow();
  expect(localStorage.getItem("tempo-pending-integrity-repairs-v2")).toBe("bad data");
});

it("duplicate repair choices are rejected before another operation is created", () => {
  enqueueIntegrityRepair(choice); expect(() => enqueueIntegrityRepair(choice)).toThrow(/already/);
  expect(pendingIntegrityRepairs()).toHaveLength(1);
});

it("repair replay resolves a completed receipt after a stale endpoint response", async () => {
  enqueueIntegrityRepair(choice); let reads = 0;
  vi.stubGlobal("fetch", vi.fn(async input => String(input).includes("operations") ?
    Response.json(++reads === 1 ? { state: "unknown" } : { state: "complete", response: submission }) :
    Response.json({ detail: "Issue no longer exists" }, { status: 404 })));
  await flushIntegrityRepairs(); expect(pendingIntegrityRepairs()[0].phase).toBe("validating");
});

it("failed validation retries its task without repeating the source edit", async () => {
  const repair = enqueueIntegrityRepair(choice); let retried = false; let posts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("operations")) return Response.json({ state: "complete", response: submission });
    if (init?.method === "POST") { expect(url).toContain("/tasks/graph/retry"); posts++; retried = true; return Response.json({ retried: true }); }
    return Response.json(retried ? { ...complete, state: "waiting", task_generation: 3 } :
      { ...complete, state: "failed", reason: "Graph failed", retry_task_id: "graph" });
  }));
  await flushIntegrityRepairs(); await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0].phase).toBe("failed");
  await retryIntegrityRepair(repair.operationId);
  expect(posts).toBe(1); expect(pendingIntegrityRepairs()[0].phase).toBe("validating");
});

it("rejected repair retries require current evidence and preserve stale choices for explicit replacement", async () => {
  const repair = enqueueIntegrityRepair(choice);
  vi.stubGlobal("fetch", vi.fn(async input => String(input).includes("operations") ?
    Response.json({ state: "failed", error: { message: "Source changed" } }) :
    Response.json({ scan_status: "idle", issues: [{ id: "issue", signature: "changed" }] })));
  await flushIntegrityRepairs(); await retryIntegrityRepair(repair.operationId);
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: repair.operationId, phase: "stale" });
});

it("repair outbox confirms each pending choice fairly and follows successor generations", async () => {
  const first = enqueueIntegrityRepair(choice);
  const second = enqueueIntegrityRepair({ ...choice, issueId: "second" });
  vi.stubGlobal("fetch", vi.fn(async input => {
    const url = String(input);
    if (url.includes("operations")) return Response.json({ state: "complete", response: {
      ...submission, issue_id: url.endsWith(second.operationId) ? "second" : "issue", task_id: url.endsWith(second.operationId) ? "next-graph" : "graph" } });
    return Response.json({ ...complete, task_id: url.includes("next-graph") ? "next-graph" : "graph", task_generation: 3, state: "waiting" });
  }));
  await flushIntegrityRepairs(); await flushIntegrityRepairs(); await flushIntegrityRepairs(); await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()).toEqual(expect.arrayContaining([
    expect.objectContaining({ operationId: first.operationId, taskGeneration: 3 }),
    expect.objectContaining({ operationId: second.operationId, taskGeneration: 3 }),
  ]));
});

it("a later integrity scan failure receives a fresh retry key while preserving the accepted repair operation", async () => {
  const repair = enqueueIntegrityRepair(choice); let failed = true;
  const retryKeys: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("operations")) return Response.json({ state: "complete", response: submission });
    if (init?.method === "POST") {
      retryKeys.push(new Headers(init.headers).get("Idempotency-Key")!); failed = false;
      return Response.json({ state: "queued" });
    }
    return Response.json({ ...complete, state: failed ? "failed" : "waiting", reason: failed ? "Scan failed" : null,
      retry_task_id: failed ? "integrity-scan" : null });
  }));
  await flushIntegrityRepairs(); await flushIntegrityRepairs(); await retryIntegrityRepair(repair.operationId);
  failed = true; await flushIntegrityRepairs(); await retryIntegrityRepair(repair.operationId);
  expect(retryKeys).toHaveLength(2); expect(retryKeys[0]).not.toBe(retryKeys[1]);
  expect(pendingIntegrityRepairs()[0].operationId).toBe(repair.operationId);
});
