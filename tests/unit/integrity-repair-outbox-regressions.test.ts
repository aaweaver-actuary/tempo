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
    if (url.includes("operations")) return Response.json(url.endsWith(repair.operationId) ?
      { state: "complete", response: submission } : { state: "unknown" });
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
    if (url.includes("operations")) return Response.json(url.endsWith(repair.operationId) ?
      { state: "complete", response: submission } : { state: "unknown" });
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

it("blocked repair retry keeps old receipts pollable across reload until the original operation advances", async () => {
  const repair = enqueueIntegrityRepair(choice);
  let serverReceipt: object = { state: "blocked", retry_cycle: 2, attempt_count: 10, cycle_attempt_count: 5 };
  let retryPosts = 0, sourcePosts = 0;
  const confirmed = vi.fn(); window.addEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") {
      if (url.endsWith(`/operations/${repair.operationId}/retry`)) { retryPosts++; return Response.json({ state: "blocked" }, { status: 202 }); }
      sourcePosts++; throw new Error("The original source edit must not be submitted again");
    }
    return Response.json(url.includes("operations") ? serverReceipt : complete);
  }));
  try {
    await flushIntegrityRepairs(); expect(pendingIntegrityRepairs()[0].phase).toBe("blocked");
    await retryIntegrityRepair(repair.operationId);
    await flushIntegrityRepairs(); await flushIntegrityRepairs();
    expect(pendingIntegrityRepairs()[0].phase).toBe("saving");
    vi.resetModules(); const recovered = await import("../../app/lib/integrity-repair-outbox");
    await recovered.flushIntegrityRepairs(); expect(recovered.pendingIntegrityRepairs()[0].phase).toBe("saving");
    serverReceipt = { state: "executing", retry_cycle: 3, attempt_count: 11, cycle_attempt_count: 1 };
    await recovered.flushIntegrityRepairs();
    serverReceipt = { state: "complete", response: submission, retry_cycle: 3, attempt_count: 11 };
    await recovered.flushIntegrityRepairs(); expect(recovered.pendingIntegrityRepairs()[0].phase).toBe("validating");
    await recovered.flushIntegrityRepairs(); await recovered.flushIntegrityRepairs();
    expect(retryPosts).toBe(1); expect(sourcePosts).toBe(0); expect(confirmed).toHaveBeenCalledOnce();
  } finally { window.removeEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed); }
});

it("task repair retry waits for its durable retry command across old failures and reload", async () => {
  const repair = enqueueIntegrityRepair(choice);
  let retryCommand = "unknown", validation = "failed", retryPosts = 0, sourcePosts = 0;
  const confirmed = vi.fn(); window.addEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") {
      if (!url.endsWith("/tasks/graph/retry")) { sourcePosts++; throw new Error("Repeated source edit"); }
      retryPosts++; retryCommand = "queued";
      return Response.json({ operation_id: new Headers(init.headers).get("Idempotency-Key"), state: "queued" }, { status: 202 });
    }
    if (url.endsWith(`/operations/${repair.operationId}`)) return Response.json({ state: "complete", response: submission });
    if (url.includes("operations")) return Response.json({ state: retryCommand,
      response: retryCommand === "complete" ? { id: "graph", generation: 2, state: "queued" } : undefined });
    return Response.json({ ...complete, state: validation, task_generation: validation === "failed" ? 2 : 3,
      reason: validation === "failed" ? "Graph failed" : null, retry_task_id: validation === "failed" ? "graph" : null });
  }));
  try {
    await flushIntegrityRepairs(); await flushIntegrityRepairs();
    await retryIntegrityRepair(repair.operationId); await flushIntegrityRepairs(); await flushIntegrityRepairs();
    expect(pendingIntegrityRepairs()[0].phase).toBe("validating");
    vi.resetModules(); const recovered = await import("../../app/lib/integrity-repair-outbox");
    await recovered.flushIntegrityRepairs(); expect(recovered.pendingIntegrityRepairs()[0].phase).toBe("validating");
    retryCommand = "complete"; validation = "waiting";
    await recovered.flushIntegrityRepairs(); await recovered.flushIntegrityRepairs();
    expect(recovered.pendingIntegrityRepairs()[0]).toMatchObject({ taskGeneration: 3, operationId: repair.operationId });
    validation = "complete"; await recovered.flushIntegrityRepairs(); await recovered.flushIntegrityRepairs();
    expect(retryPosts).toBe(1); expect(sourcePosts).toBe(0); expect(confirmed).toHaveBeenCalledOnce();
  } finally { window.removeEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed); }
});

it("an advanced blocked retry cycle is actionable even when no intermediate executing poll was observed", async () => {
  const repair = enqueueIntegrityRepair(choice); let cycle = 2;
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") return Response.json({ state: "blocked" }, { status: 202 });
    return Response.json({ state: "blocked", retry_cycle: cycle, attempt_count: cycle * 5, cycle_attempt_count: 5,
      last_error: { message: "Database unavailable" } });
  }));
  await flushIntegrityRepairs(); await retryIntegrityRepair(repair.operationId);
  expect(pendingIntegrityRepairs()[0].phase).toBe("saving");
  cycle = 3; await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ phase: "blocked", error: "Database unavailable" });
});

it("a task retry that completes then fails again is actionable without an intermediate waiting poll", async () => {
  const repair = enqueueIntegrityRepair(choice); let commandComplete = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") return Response.json({ operation_id: new Headers(init.headers).get("Idempotency-Key") }, { status: 202 });
    if (url.endsWith(`/operations/${repair.operationId}`)) return Response.json({ state: "complete", response: submission });
    if (url.includes("operations")) return Response.json(commandComplete ?
      { state: "complete", response: { id: "graph", state: "queued", generation: 2 } } : { state: "queued" });
    return Response.json({ ...complete, state: "failed", reason: "Graph failed again", retry_task_id: "graph" });
  }));
  await flushIntegrityRepairs(); await flushIntegrityRepairs(); await retryIntegrityRepair(repair.operationId);
  expect(pendingIntegrityRepairs()[0].phase).toBe("validating");
  commandComplete = true; await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ phase: "failed", error: "Graph failed again", operationId: repair.operationId });
});

it.each(["operation", "task"])("a lost %s retry response replays the same retry identity after reload", async kind => {
  const repair = enqueueIntegrityRepair(choice);
  let retryReceiptVisible = false, finished = false;
  const retryKeys: string[] = [], confirmed = vi.fn();
  window.addEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") {
      expect(url).toContain("/retry");
      const key = new Headers(init.headers).get("Idempotency-Key")!;
      expect(pendingIntegrityRepairs()[0].retryOperationId).toBe(key);
      retryKeys.push(key);
      if (retryKeys.length === 1) throw new Error("Retry response lost");
      return Response.json({ operation_id: kind === "task" ? key : repair.operationId }, { status: 202 });
    }
    if (url.endsWith(`/operations/${repair.operationId}`)) return Response.json(kind === "operation" && !retryReceiptVisible ?
      { state: "blocked", retry_cycle: 1, attempt_count: 5 } : { state: "complete", response: submission });
    if (url.includes("operations")) return Response.json(retryReceiptVisible ?
      { state: "complete", response: { id: "graph", generation: 2, state: "queued" } } : { state: "unknown" });
    return Response.json({ ...complete, state: finished ? "complete" : retryReceiptVisible ? "waiting" : "failed",
      retry_task_id: retryReceiptVisible ? null : "graph" });
  }));
  try {
    await flushIntegrityRepairs(); if (kind === "task") await flushIntegrityRepairs();
    await retryIntegrityRepair(repair.operationId);
    const storedRetryId = pendingIntegrityRepairs()[0].retryOperationId;
    expect(pendingIntegrityRepairs()[0].phase).toBe(kind === "task" ? "validating" : "saving");
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + 60_000);
    vi.resetModules(); const recovered = await import("../../app/lib/integrity-repair-outbox");
    await recovered.flushIntegrityRepairs();
    expect(retryKeys).toEqual([storedRetryId, storedRetryId]);
    retryReceiptVisible = true; await recovered.flushIntegrityRepairs(); await recovered.flushIntegrityRepairs();
    finished = true; await recovered.flushIntegrityRepairs(); await recovered.flushIntegrityRepairs();
    expect(confirmed).toHaveBeenCalledOnce(); expect(recovered.pendingIntegrityRepairs()).toEqual([]);
    expect(retryKeys).toHaveLength(2);
  } finally { window.removeEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed); }
});

it("retry storage failure preserves the terminal choice before any retry POST", async () => {
  const repair = enqueueIntegrityRepair(choice); const retryPosts = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") retryPosts();
    return Response.json({ state: "blocked", retry_cycle: 1, attempt_count: 5 });
  }));
  await flushIntegrityRepairs();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage full"); });
  await expect(retryIntegrityRepair(repair.operationId)).rejects.toThrow("Storage full");
  expect(retryPosts).not.toHaveBeenCalled();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: repair.operationId, phase: "blocked" });
});

it("concurrent repair Retry clicks share one persisted retry identity and admission", async () => {
  const repair = enqueueIntegrityRepair(choice); let baselineReads = 0, posts = 0;
  let releaseBaseline!: (response: Response) => void;
  const baseline = new Promise<Response>(resolve => { releaseBaseline = resolve; });
  const blocked = { state: "blocked", retry_cycle: 1, attempt_count: 5 };
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") { posts++; return Response.json({ state: "blocked" }, { status: 202 }); }
    if (++baselineReads === 2) return baseline;
    return Response.json(blocked);
  }));
  await flushIntegrityRepairs();
  const firstClick = retryIntegrityRepair(repair.operationId), secondClick = retryIntegrityRepair(repair.operationId);
  expect(firstClick).toBe(secondClick);
  releaseBaseline(Response.json(blocked)); await firstClick; await secondClick;
  expect(posts).toBe(1); expect(pendingIntegrityRepairs()[0].phase).toBe("saving");
});
