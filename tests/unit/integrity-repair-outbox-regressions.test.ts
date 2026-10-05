import { afterEach, expect, it, vi } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { enqueueIntegrityRepair, pendingIntegrityRepairs, flushIntegrityRepairs, retryIntegrityRepair,
  INTEGRITY_REPAIR_CONFIRMED } from "../../app/lib/integrity-repair-outbox";

const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const choice = { repertoireId: "rep", issueId: "first", signature: "first-signature", selectedMoveUci: "e2e4" };
const issue = (id = "first") => ({ id, kind: "multiple_responses", fen_key: startingFen.split(" ").slice(0, 4).join(" "),
  fen: startingFen, trained_color: "white", signature: `${id}-signature`, moves: [], sources: [] });
const evidence = (repertoireId = "rep", issues = [issue(), issue("second")]) => ({ repertoire_id: repertoireId,
  status: issues.length ? "needs_repair" : "clean", issue_count: issues.length, first_issue_id: issues[0]?.id ?? null,
  scan_status: "idle", scan_generation: "scan:2", scan_progress: { completed: 2, total: 2 }, last_scan_error: null, issues });
const graph = (id = "rep") => ({ id, name: id, source_name: "repair.pgn", line_count: 1, card_count: 1, due_count: 1,
  graph_state: "ready", graph_generation: 2 });
const submission = (repertoireId = "rep", issueId = "first") => ({ task_id: `graph-${repertoireId}`, repertoire_id: repertoireId, issue_id: issueId, state: "queued" });
const task = (repertoireId = "rep", state = "queued") => ({ id: `graph-${repertoireId}`, kind: "opening_graph_rebuild",
  deduplication_key: repertoireId, generation: 2, state, last_error: null as string | null });

function fixture() {
  const receipts = new Map<string, unknown>();
  const currentEvidence = new Map<string, unknown>([["rep", evidence()], ["other", evidence("other")]]);
  const tasks = [task(), task("other")];
  const repertoires = [graph(), graph("other")];
  const posts: { key: string; url: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const operationMatch = url.match(/\/operations\/([^/]+)$/);
    if (operationMatch) return Response.json(receipts.get(operationMatch[1]) ?? { state: "unknown" });
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body ?? "{}"));
      const key = new Headers(init.headers).get("Idempotency-Key")!;
      posts.push({ key, url, body });
      const match = url.match(/repertoires\/([^/]+)\/integrity\/issues\/([^/]+)\/resolve/);
      if (!match) return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
      const result = submission(match[1], match[2]);
      receipts.set(key, { state: "complete", response: result });
      return Response.json(result);
    }
    if (url.endsWith("/system/tasks")) return Response.json({ tasks });
    if (url.endsWith("/repertoires")) return Response.json({ repertoires });
    const repertoireMatch = url.match(/repertoires\/([^/]+)\/integrity$/);
    if (repertoireMatch) return currentEvidence.has(repertoireMatch[1]) ? Response.json(currentEvidence.get(repertoireMatch[1]))
      : Response.json({ detail: "Repertoire not found" }, { status: 404 });
    throw new Error(`Unexpected request ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { receipts, currentEvidence, tasks, repertoires, posts, fetchMock };
}
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const operationLifecycleCases = [
  { state: "unknown", phase: "validating" },
  { state: "pending", phase: "saving" },
  { state: "queued", phase: "saving" },
  { state: "executing", phase: "saving" },
  { state: "retrying", phase: "saving" },
  { state: "blocked", phase: "blocked" },
  { state: "complete", phase: "validating" },
  { state: "failed", phase: "failed" },
] as const;
const taskLifecycleStates = ["queued", "leased", "retrying", "complete", "failed", "superseded"] as const;

function producerConstraintStates(tableName: "operation_receipts" | "background_tasks") {
  const migrationDirectory = resolve("backend/migrations");
  let stateLiterals: string | undefined;
  for (const filename of readdirSync(migrationDirectory).filter(filename => /^\d+.*\.sql$/.test(filename)).sort()) {
    const sql = readFileSync(resolve(migrationDirectory, filename), "utf8");
    // Only these two table state constraints are audited; later constraint replacements win.
    const tableConstraint = sql.match(new RegExp(`CREATE TABLE ${tableName} \\([^\\n]*?CHECK \\(state IN \\(([^)]+)\\)\\)`));
    if (tableConstraint) stateLiterals = tableConstraint[1];
    const replacementConstraint = sql.match(new RegExp(`ALTER TABLE ${tableName} ADD CONSTRAINT ${tableName}_state_check\\s+CHECK \\(state IN \\(([^)]+)\\)\\)`));
    if (replacementConstraint) stateLiterals = replacementConstraint[1];
  }
  expect(stateLiterals, `No state constraint found for ${tableName}`).toBeDefined();
  return Array.from(stateLiterals!.matchAll(/'([^']+)'/g), match => match[1]).sort();
}

it("repair outbox handles every backend receipt and task lifecycle state deliberately", () => {
  expect(operationLifecycleCases.map(item => item.state).filter(state => state !== "unknown").sort())
    .toEqual(producerConstraintStates("operation_receipts"));
  expect([...taskLifecycleStates].sort()).toEqual(producerConstraintStates("background_tasks"));
  const sqliteSchema = readFileSync(resolve("backend/app/database.py"), "utf8");
  const sqliteTaskStates = sqliteSchema.match(/CREATE TABLE IF NOT EXISTS background_tasks[\s\S]*?CHECK\(state IN \(([^)]+)\)\)/);
  expect(sqliteTaskStates).not.toBeNull();
  expect(Array.from(sqliteTaskStates![1].matchAll(/'([^']+)'/g), match => match[1]).sort()).toEqual([...taskLifecycleStates].sort());
  const operationProducer = readFileSync(resolve("backend/app/command_gateway.py"), "utf8");
  expect(operationProducer.slice(operationProducer.indexOf("def read_operation("))).toContain('"state": "unknown"');
});

it.each(operationLifecycleCases)("operation receipt lifecycle $state maps to $phase without changing repair identity", async ({ state, phase }) => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice);
  environment.receipts.set(saved.operationId, { operation_id: saved.operationId, state,
    attempt_count: 2, retry_cycle: 1, cycle_attempt_count: 2, response: submission(),
    error: { status_code: 500, message: "Repair save failed" }, last_error: { message: "Delivery blocked" } });
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase, attempts: 0 });
  expect(environment.posts).toHaveLength(state === "unknown" ? 1 : 0);
  if (["pending", "queued", "executing", "retrying", "complete", "unknown"].includes(state))
    expect(pendingIntegrityRepairs()[0].error).toBeUndefined();
  if (state === "blocked") expect(pendingIntegrityRepairs()[0].error).toBe("Delivery blocked");
  if (state === "failed") expect(pendingIntegrityRepairs()[0]).toMatchObject({ error: "Repair save failed", terminalOperationFailure: true });
});

it("queued operation receipt remains pollable without resubmitting or requiring Retry", async () => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice);
  environment.receipts.set(saved.operationId, { operation_id: saved.operationId, state: "queued", attempt_count: 3,
    retry_cycle: 1, cycle_attempt_count: 0, next_retry_at: "2026-10-05T16:00:00+00:00" });
  await flushIntegrityRepairs(); await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "saving", attempts: 0, nextAttemptAt: 0 });
  expect(pendingIntegrityRepairs()[0].error).toBeUndefined();
  expect(pendingIntegrityRepairs()[0].retry).toBeUndefined();
  expect(environment.posts).toHaveLength(0);
  environment.receipts.set(saved.operationId, { operation_id: saved.operationId, state: "complete", response: submission() });
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "validating", taskId: "graph-rep" });
  expect(environment.posts).toHaveLength(0);
});

it("legacy pending receipt without payload recovers only the original saved choice", async () => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice);
  environment.receipts.set(saved.operationId, { operation_id: saved.operationId, state: "pending",
    message: "Legacy receipt has no saved payload. Recover only from matching journal or outbox evidence." });
  await flushIntegrityRepairs();
  expect(environment.posts).toEqual([expect.objectContaining({ key: saved.operationId,
    body: { signature: choice.signature, selected_move_uci: choice.selectedMoveUci } })]);
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "validating" });
});

it.each([404, 409, 422])("terminal receipt failure %s requires review and retains the saved operation", async statusCode => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice);
  environment.receipts.set(saved.operationId, { state: "failed", error: { status_code: statusCode, message: "Conflict changed" } });
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "stale",
    terminalOperationFailure: true, error: "Conflict changed" });
  expect(environment.posts).toHaveLength(0);
});

it.each(["queued", "leased", "retrying", "complete"])("unrelated superseded task cannot interrupt repair validation (%s)", async matchingState => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice); await flushIntegrityRepairs();
  environment.tasks.unshift({ id: "retired-prefix-preview", kind: "canonical_prefix_preview", deduplication_key: "obsolete-preview",
    generation: 7, state: "superseded", last_error: null });
  environment.tasks[1].state = matchingState;
  environment.currentEvidence.set("rep", evidence("rep", []));
  const confirmed = vi.fn(); window.addEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
  try {
    await flushIntegrityRepairs();
    if (matchingState === "complete") {
      expect(pendingIntegrityRepairs()).toEqual([]);
      expect(confirmed).toHaveBeenCalledOnce();
    } else {
      expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "validating", attempts: 0 });
      expect(pendingIntegrityRepairs()[0].error).toBeUndefined();
      expect(confirmed).not.toHaveBeenCalled();
      environment.tasks[1].state = "complete";
      await flushIntegrityRepairs();
      expect(pendingIntegrityRepairs()).toEqual([]);
      expect(confirmed).toHaveBeenCalledOnce();
    }
    expect(environment.posts).toHaveLength(1);
  } finally { window.removeEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed); }
});

it.each(taskLifecycleStates)("durable task lifecycle %s has deliberate repair behavior", async state => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice); await flushIntegrityRepairs();
  environment.tasks[0].state = state;
  environment.tasks[0].last_error = state === "failed" ? "Task failed" : null;
  environment.currentEvidence.set("rep", evidence("rep", []));
  await flushIntegrityRepairs();
  if (state === "complete") expect(pendingIntegrityRepairs()).toEqual([]);
  else {
    const pending = pendingIntegrityRepairs()[0];
    expect(pending).toMatchObject({ operationId: saved.operationId, attempts: 0,
      phase: state === "failed" ? "failed" : state === "superseded" ? "stale" : "validating" });
    if (state === "failed") expect(pending).toMatchObject({ retryTaskId: "graph-rep", error: "Task failed" });
    else if (state === "superseded") expect(pending.error).toContain("superseded");
    else expect(pending.error).toBeUndefined();
  }
  expect(environment.posts).toHaveLength(1);
});

for (const kind of ["opening_graph_rebuild", "integrity_repair"] as const) {
  for (const generation of [2, 3]) {
    it(`superseded repair validation task requires review without confirming or retrying (${kind}, generation ${generation})`, async () => {
      const environment = fixture(); const saved = enqueueIntegrityRepair(choice);
      environment.receipts.set(saved.operationId, { state: "complete", response: { ...submission(), task_generation: 2 } });
      await flushIntegrityRepairs();
      enqueueIntegrityRepair({ ...choice, issueId: "second", signature: "second-signature" });
      Object.assign(environment.tasks[0], { kind, generation, state: "superseded",
        deduplication_key: kind === "integrity_repair" ? "rep:first-signature" : "rep" });
      environment.currentEvidence.set("rep", evidence("rep", []));
      const confirmed = vi.fn(); window.addEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
      try {
        await flushIntegrityRepairs(); await flushIntegrityRepairs();
        expect(pendingIntegrityRepairs()[0]).toMatchObject({ ...choice, operationId: saved.operationId, phase: "stale", taskId: "graph-rep", taskGeneration: 2, attempts: 0 });
        expect(pendingIntegrityRepairs()[0].error).toContain("superseded");
        expect(pendingIntegrityRepairs()[0].error).toContain("Review");
        expect(pendingIntegrityRepairs()[0].retryTaskId).toBeUndefined();
        expect(pendingIntegrityRepairs()[1].phase).toBe("queued");
        await expect(retryIntegrityRepair(saved.operationId)).rejects.toThrow("Review this conflict");
        expect(environment.posts).toHaveLength(0);
        expect(confirmed).not.toHaveBeenCalled();
      } finally { window.removeEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed); }
    });
  }
}

it.each(["kind", "deduplication key", "generation"])("superseded task still enforces saved repair identity and generation (%s)", async mismatch => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice);
  environment.receipts.set(saved.operationId, { state: "complete", response: { ...submission(), task_generation: 2 } });
  await flushIntegrityRepairs();
  Object.assign(environment.tasks[0], { state: "superseded", ...(mismatch === "kind" ? { kind: "integrity_scan" }
    : mismatch === "deduplication key" ? { deduplication_key: "unrelated" } : { generation: 1 }) });
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "validating" });
  expect(pendingIntegrityRepairs()[0].error).toContain("unrelated task or generation");
  expect(environment.posts).toHaveLength(0);
});

it("a newer current task generation waits for matching graph publication", async () => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice);
  environment.receipts.set(saved.operationId, { state: "complete", response: { ...submission(), task_generation: 2 } });
  await flushIntegrityRepairs();
  environment.tasks[0].generation = 3;
  environment.currentEvidence.set("rep", evidence("rep", []));
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "validating" });
  environment.tasks[0].state = "complete";
  await flushIntegrityRepairs(); expect(pendingIntegrityRepairs()).toHaveLength(1);
  environment.repertoires[0].graph_generation = 3;
  await flushIntegrityRepairs(); expect(pendingIntegrityRepairs()).toEqual([]);
  expect(environment.posts).toHaveLength(0);
});

it.each(["receipt", "task"])("unexpected backend lifecycle states remain contract failures (%s)", async contract => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice);
  if (contract === "receipt") environment.receipts.set(saved.operationId, { operation_id: saved.operationId, state: "unexpected-state" });
  else { await flushIntegrityRepairs(); environment.tasks[0].state = "unexpected-state"; }
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: contract === "receipt" ? "queued" : "validating", attempts: 1 });
  expect(pendingIntegrityRepairs()[0].error).toContain(contract === "receipt" ? "Invalid repair receipt data" : "Invalid repair tasks data");
  expect(environment.posts).toHaveLength(contract === "receipt" ? 0 : 1);
});

it("repair queue persists the complete choice before any network request and rejects duplicate choices", () => {
  const environment = fixture();
  const saved = enqueueIntegrityRepair(choice);
  expect(environment.fetchMock).not.toHaveBeenCalled();
  expect(pendingIntegrityRepairs()).toEqual([saved]);
  expect(() => enqueueIntegrityRepair(choice)).toThrow("already has a saved choice");
});

it("repair queue serializes one repertoire through validation while independently servicing another", async () => {
  const environment = fixture();
  enqueueIntegrityRepair(choice);
  enqueueIntegrityRepair({ ...choice, issueId: "second", signature: "second-signature" });
  enqueueIntegrityRepair({ ...choice, repertoireId: "other" });
  await flushIntegrityRepairs();
  expect(environment.posts.map(post => post.url)).toHaveLength(2);
  await flushIntegrityRepairs();
  expect(environment.posts).toHaveLength(2);
  environment.tasks[0].state = "complete";
  environment.currentEvidence.set("rep", evidence("rep", [issue("second")]));
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs().filter(repair => repair.repertoireId === "rep")).toHaveLength(1);
  await flushIntegrityRepairs();
  expect(environment.posts.at(-1)?.url).toContain("/second/resolve");
});

it("stale later repair evidence pauses its repertoire without sending the old choice", async () => {
  const environment = fixture();
  enqueueIntegrityRepair(choice);
  enqueueIntegrityRepair({ ...choice, issueId: "second", signature: "second-signature" });
  enqueueIntegrityRepair({ ...choice, issueId: "third", signature: "third-signature" });
  await flushIntegrityRepairs();
  environment.tasks[0].state = "complete";
  environment.currentEvidence.set("rep", evidence("rep", [{ ...issue("second"), signature: "changed" }]));
  await flushIntegrityRepairs(); await flushIntegrityRepairs(); await flushIntegrityRepairs();
  expect(environment.posts).toHaveLength(1);
  expect(pendingIntegrityRepairs().map(repair => repair.phase)).toEqual(["stale", "queued"]);
  const reviewed = enqueueIntegrityRepair({ ...choice, issueId: "second", signature: "changed" });
  expect(pendingIntegrityRepairs()[0].operationId).toBe(reviewed.operationId);
});

it("a removed repertoire pauses queued repair choices for review without losing their operation identities", async () => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice);
  enqueueIntegrityRepair({ ...choice, issueId: "second", signature: "second-signature" });
  environment.currentEvidence.delete("rep");
  await flushIntegrityRepairs(); await flushIntegrityRepairs();
  expect(environment.posts).toHaveLength(0);
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "stale" });
  expect(pendingIntegrityRepairs()[0].error).toContain("repertoire was removed");
  expect(pendingIntegrityRepairs()[1].phase).toBe("queued");
});

it("lost repair response and reload reconcile the original receipt without submitting twice", async () => {
  const environment = fixture();
  const saved = enqueueIntegrityRepair(choice);
  environment.receipts.set(saved.operationId, { state: "complete", response: submission() });
  await flushIntegrityRepairs();
  expect(environment.posts).toHaveLength(0);
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "validating" });
});

it("a pending integrity repair retains its command ID until its receipt completes", async () => {
  const environment = fixture();
  const saved = enqueueIntegrityRepair(choice);
  environment.receipts.set(saved.operationId, { state: "pending" });
  await flushIntegrityRepairs(); await flushIntegrityRepairs();
  expect(environment.posts).toHaveLength(0);
  environment.receipts.set(saved.operationId, { state: "complete", response: submission() });
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, taskId: "graph-rep" });
});

it("repair completion waits for graph and idle integrity publication and keeps new conflicts visible", async () => {
  const environment = fixture();
  const confirmed = vi.fn(); window.addEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
  try {
    enqueueIntegrityRepair(choice); await flushIntegrityRepairs();
    environment.currentEvidence.set("rep", evidence("rep", []));
    await flushIntegrityRepairs(); expect(confirmed).not.toHaveBeenCalled();
    environment.tasks[0].state = "complete";
    environment.currentEvidence.set("rep", { ...evidence("rep", []), status: "unchecked", scan_status: "running" });
    await flushIntegrityRepairs(); expect(confirmed).not.toHaveBeenCalled();
    environment.currentEvidence.set("rep", evidence("rep", [issue("second")]));
    await flushIntegrityRepairs();
    expect(confirmed).toHaveBeenCalledOnce(); expect(pendingIntegrityRepairs()).toEqual([]);
  } finally { window.removeEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed); }
});

it("failed integrity validation remains actionable and never confirms a missing issue as clean", async () => {
  const environment = fixture(); enqueueIntegrityRepair(choice); await flushIntegrityRepairs();
  environment.tasks[0].state = "complete";
  environment.currentEvidence.set("rep", { ...evidence("rep", []), scan_status: "failed", last_scan_error: "Scan failed" });
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ phase: "failed", error: "Scan failed" });
});

it("repair evidence with a failed scan or unrelated repertoire cannot submit a saved choice", async () => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice);
  environment.currentEvidence.set("rep", evidence("unrelated"));
  await flushIntegrityRepairs();
  expect(environment.posts).toHaveLength(0);
  expect(pendingIntegrityRepairs()[0].error).toContain("unrelated repertoire");
  environment.currentEvidence.set("rep", { ...evidence(), scan_status: "failed", last_scan_error: "Scan failed" });
  await retryIntegrityRepair(saved.operationId);
  expect(environment.posts).toHaveLength(0);
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ phase: "failed", error: "Scan failed" });
});

it("blocked repair retry stays pollable until the original receipt advances", async () => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice);
  environment.receipts.set(saved.operationId, { state: "blocked", retry_cycle: 1, attempt_count: 5 });
  await flushIntegrityRepairs(); await retryIntegrityRepair(saved.operationId); await flushIntegrityRepairs();
  expect(environment.posts).toHaveLength(1); expect(environment.posts[0].url).toContain(`/operations/${saved.operationId}/retry`);
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ phase: "saving", retry: { deliveryAccepted: true } });
  environment.receipts.set(saved.operationId, { operation_id: saved.operationId, state: "queued", retry_cycle: 2, attempt_count: 5 });
  await flushIntegrityRepairs(); await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "saving", attempts: 0 });
  expect(pendingIntegrityRepairs()[0].error).toBeUndefined();
  expect(pendingIntegrityRepairs()[0].retry).toBeUndefined();
  expect(environment.posts).toHaveLength(1);
  environment.receipts.set(saved.operationId, { state: "complete", retry_cycle: 2, response: submission() });
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "validating" });
});

it("validation retry ignores the old failed task until its retry receipt commits", async () => {
  const environment = fixture(); const saved = enqueueIntegrityRepair(choice); await flushIntegrityRepairs();
  environment.tasks[0].state = "failed"; await flushIntegrityRepairs();
  await retryIntegrityRepair(saved.operationId); await flushIntegrityRepairs();
  const pending = pendingIntegrityRepairs()[0];
  expect(pending).toMatchObject({ phase: "validating", retry: { kind: "task", taskId: "graph-rep" } });
  expect(environment.posts.at(-1)?.key).toBe(pending.retryOperationId);
  const retryDeliveryCountBeforeQueuedReceipt = environment.posts.filter(post => post.url.includes("/retry")).length;
  environment.receipts.set(pending.retryOperationId!, { operation_id: pending.retryOperationId, state: "queued", attempt_count: 1 });
  await flushIntegrityRepairs(); await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: saved.operationId, phase: "validating",
    retryOperationId: pending.retryOperationId, retry: { kind: "task", taskId: "graph-rep" }, attempts: 0 });
  expect(pendingIntegrityRepairs()[0].error).toBeUndefined();
  expect(environment.posts.filter(post => post.url.includes("/retry"))).toHaveLength(retryDeliveryCountBeforeQueuedReceipt);
  environment.receipts.set(pending.retryOperationId!, { state: "complete", response: { id: "graph-rep" } });
  environment.tasks[0].state = "complete"; environment.currentEvidence.set("rep", evidence("rep", []));
  await flushIntegrityRepairs(); expect(pendingIntegrityRepairs()).toEqual([]);
});

it("repair queue preserves legacy command payload and previously shipped queue identities", () => {
  const old = { ...choice, operationId: "old-task", taskId: "graph-rep", taskGeneration: 2, phase: "validating" };
  localStorage.setItem("tempo-pending-integrity-repairs-v2", JSON.stringify([old]));
  localStorage.setItem("tempo-pending-integrity-repair-v1", JSON.stringify({ operationId: "legacy-command",
    fingerprint: JSON.stringify(["other", "first", JSON.stringify({ signature: "first-signature", selected_move_uci: "e2e4" })]) }));
  expect(pendingIntegrityRepairs()).toEqual([expect.objectContaining(old), expect.objectContaining({
    operationId: "legacy-command", repertoireId: "other", selectedMoveUci: "e2e4", phase: "saving" })]);
  expect(localStorage.getItem("tempo-pending-integrity-repair-v1")).toBeNull();
});

it("previously shipped repair receipts preserve task generation when recovering a migrated choice", async () => {
  const environment = fixture();
  localStorage.setItem("tempo-pending-integrity-repairs-v2", JSON.stringify([{ ...choice, operationId: "shipped-repair", phase: "saving" }]));
  environment.receipts.set("shipped-repair", { state: "complete", response: { ...submission(), task_generation: 2 } });
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ operationId: "shipped-repair", taskId: "graph-rep", taskGeneration: 2, phase: "validating" });
  expect(environment.posts).toHaveLength(0);
  // Completed tasks age out of the status list. A prior graph must not confirm the saved generation.
  environment.tasks.shift(); environment.repertoires[0].graph_generation = 1;
  environment.currentEvidence.set("rep", evidence("rep", []));
  await flushIntegrityRepairs(); expect(pendingIntegrityRepairs()).toHaveLength(1);
  environment.repertoires[0].graph_generation = 2;
  await flushIntegrityRepairs(); expect(pendingIntegrityRepairs()).toEqual([]);
});

it("repair journal migration preserves original data when storage writes fail", () => {
  const original = JSON.stringify([{ ...choice, operationId: "old", phase: "queued" }]);
  localStorage.setItem("tempo-pending-integrity-repairs-v2", original);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage full"); });
  expect(() => pendingIntegrityRepairs()).toThrow("Saved repair choices");
  expect(localStorage.getItem("tempo-pending-integrity-repairs-v2")).toBe(original);
});

it.each(["corrupt", "different choice", "different task"])("repair migration preserves the original journal when an existing upgraded record is %s", conflict => {
  const originalRepair = { ...choice, operationId: "migration-identity", phase: "validating", taskId: "original-task", taskGeneration: 2 };
  const original = JSON.stringify([originalRepair]);
  localStorage.setItem("tempo-pending-integrity-repairs-v2", original);
  const destination = conflict === "corrupt" ? "{" : JSON.stringify({ ...originalRepair,
    ...(conflict === "different choice" ? { selectedMoveUci: "d2d4" } : { taskId: "unrelated-task" }) });
  localStorage.setItem("tempo-pending-integrity-repairs-v3:migration-identity", destination);
  expect(() => pendingIntegrityRepairs()).toThrow("Saved repair choices");
  expect(localStorage.getItem("tempo-pending-integrity-repairs-v2")).toBe(original);
  expect(localStorage.getItem("tempo-pending-integrity-repairs-v3:migration-identity")).toBe(destination);
});

it("conflicting legacy repair journals preserve both originals for recovery", () => {
  const oldQueue = JSON.stringify([{ ...choice, operationId: "legacy-conflict", phase: "queued" }]);
  const oldCommand = JSON.stringify({ operationId: "legacy-conflict",
    fingerprint: JSON.stringify(["rep", "first", JSON.stringify({ signature: "first-signature", selected_move_uci: "d2d4" })]) });
  localStorage.setItem("tempo-pending-integrity-repairs-v2", oldQueue);
  localStorage.setItem("tempo-pending-integrity-repair-v1", oldCommand);
  expect(() => pendingIntegrityRepairs()).toThrow("Saved repair choices");
  expect(localStorage.getItem("tempo-pending-integrity-repairs-v2")).toBe(oldQueue);
  expect(localStorage.getItem("tempo-pending-integrity-repair-v1")).toBe(oldCommand);
});

it("repair queue rejects corrupt data without clearing saved choices", () => {
  localStorage.setItem("tempo-pending-integrity-repairs-v3:broken", "{");
  expect(() => enqueueIntegrityRepair(choice)).toThrow("Saved repair choices");
  expect(localStorage.getItem("tempo-pending-integrity-repairs-v3:broken")).toBe("{");
});

it("repair confirmation reads use background admission while explicit repair writes remain foreground", async () => {
  const environment = fixture(); enqueueIntegrityRepair(choice); await flushIntegrityRepairs();
  for (const [, init] of environment.fetchMock.mock.calls) expect(new Headers(init?.headers).get("X-Tempo-Work-Class"))
    .toBe(init?.method === "POST" ? null : "background");
});
