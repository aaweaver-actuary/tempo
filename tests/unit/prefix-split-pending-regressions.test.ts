import { afterEach, expect, it, vi } from "vitest";
import { acceptPrefixSplitCommand, pendingPrefixSplitStatus, rejectPrefixSplitCommand } from "../../app/lib/prefix-split-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending prefix split reuses its command ID and blocks a conflicting rejection", async () => {
  const commandKeys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json({ state: receiptReads < 3 ? "pending" : "complete", response: { rejected_after_review_id: 7 } });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    commandKeys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(rejectPrefixSplitCommand("card-1", 3)).rejects.toBeInstanceOf(PendingOperationError);
  await expect(acceptPrefixSplitCommand("card-1", 3)).rejects.toBeInstanceOf(PendingOperationError);
  expect(commandKeys).toHaveLength(1);
  expect((await rejectPrefixSplitCommand("card-1", 3)).rejected_after_review_id).toBe(7);
  expect(commandKeys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-prefix-split-v1")).toBeNull();
});

const accepted = {
  source_card_id: "combined", source_revision: 1, applied: true, idempotent: false,
  parent: { card_id: "short", starting_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", moves: ["e2e4"], tested_player_moves: 1 },
  continuation: { card_id: "tail", starting_fen: "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", moves: ["g1f3"], tested_player_moves: 1 },
};

it("accepted deferred split retains its original operation and intent through reload until applied", async () => {
  const waiting = { status: "waiting_review", intent_id: "intent-1", card_id: "combined", source_revision: 1, queue_entry_id: 17, reason: null };
  let applied = false;
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); requests.push(`${init?.method ?? "GET"} ${url}`);
    if (init?.method === "POST") return Response.json(waiting);
    return Response.json({ ...waiting, status: applied ? "applied" : "waiting_review", result: applied ? accepted : null });
  }));
  expect(await acceptPrefixSplitCommand("combined", 1, 17)).toEqual(waiting);
  const stored = localStorage.getItem("tempo-pending-prefix-split-v1")!;
  expect(JSON.parse(stored)).toMatchObject({ cardId: "combined", revision: 1, queueEntryId: 17, intentId: "intent-1" });
  expect(await pendingPrefixSplitStatus()).toEqual(waiting);
  expect(localStorage.getItem("tempo-pending-prefix-split-v1")).toBe(stored);
  applied = true;
  expect(await pendingPrefixSplitStatus()).toEqual(accepted);
  expect(localStorage.getItem("tempo-pending-prefix-split-v1")).toBeNull();
  expect(requests.filter(request => request.startsWith("POST "))).toHaveLength(1);
});

it("lost acceptance acknowledgment recovers the waiting intent from the original receipt without reposting", async () => {
  const original = JSON.stringify({ operationId: "original-op", decision: "accept", cardId: "combined", revision: 1, queueEntryId: 17 });
  localStorage.setItem("tempo-pending-prefix-split-v1", original);
  const waiting = { status: "waiting_review", intent_id: "intent-1", card_id: "combined", source_revision: 1, queue_entry_id: 17, reason: null };
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(init?.method).not.toBe("POST");
    expect(String(input)).toContain("/api/operations/original-op");
    return Response.json({ operation_id: "original-op", state: "complete", response: waiting });
  });
  vi.stubGlobal("fetch", fetcher);
  expect(await acceptPrefixSplitCommand("combined", 1, 17)).toEqual(waiting);
  expect(JSON.parse(localStorage.getItem("tempo-pending-prefix-split-v1")!)).toEqual({ ...JSON.parse(original), intentId: "intent-1" });
  expect(fetcher).toHaveBeenCalledOnce();
});

it("deferred split status cannot clear an original intent for a foreign queue attempt", async () => {
  const original = JSON.stringify({ operationId: "original", decision: "accept", cardId: "combined", revision: 1, queueEntryId: 17, intentId: "intent-1" });
  localStorage.setItem("tempo-pending-prefix-split-v1", original);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ status: "applied", intent_id: "intent-1", card_id: "combined",
    source_revision: 1, queue_entry_id: 18, reason: null, result: accepted })));
  await expect(pendingPrefixSplitStatus()).rejects.toThrow("original attempt");
  expect(localStorage.getItem("tempo-pending-prefix-split-v1")).toBe(original);
});

it("a delayed applied status cannot clear a newer pending split", async () => {
  const original = JSON.stringify({ operationId: "original", decision: "accept", cardId: "combined", revision: 1, queueEntryId: 17, intentId: "intent-1" });
  const newer = JSON.stringify({ operationId: "newer", decision: "reject", cardId: "other", revision: 2 });
  localStorage.setItem("tempo-pending-prefix-split-v1", original);
  let resolve!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(done => { resolve = done; })));
  const request = pendingPrefixSplitStatus();
  localStorage.setItem("tempo-pending-prefix-split-v1", newer);
  resolve(Response.json({ status: "applied", intent_id: "intent-1", card_id: "combined", source_revision: 1,
    queue_entry_id: 17, reason: null, result: accepted }));
  expect(await request).toEqual(accepted);
  expect(localStorage.getItem("tempo-pending-prefix-split-v1")).toBe(newer);
});

it("a delayed waiting acknowledgment cannot replace a newer pending split", async () => {
  const newer = JSON.stringify({ operationId: "newer", decision: "reject", cardId: "other", revision: 2 });
  let resolve!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(done => { resolve = done; })));
  const request = acceptPrefixSplitCommand("combined", 1, 17);
  localStorage.setItem("tempo-pending-prefix-split-v1", newer);
  resolve(Response.json({ status: "waiting_review", intent_id: "intent-1", card_id: "combined", source_revision: 1, queue_entry_id: 17, reason: null }));
  await expect(request).rejects.toThrow("pending split changed");
  expect(localStorage.getItem("tempo-pending-prefix-split-v1")).toBe(newer);
});

it("manual acceptance retains its displayed attempt identity across pending retries", async () => {
  const bodies: unknown[] = [];
  let reads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      reads += 1;
      return Response.json({ state: reads < 3 ? "pending" : "complete", response: accepted });
    }
    bodies.push(JSON.parse(String(init?.body)));
    return Response.json({ operation_id: new Headers(init?.headers).get("Idempotency-Key"), state: "pending" }, { status: 202 });
  }));
  await expect(acceptPrefixSplitCommand("combined", 1, 17)).rejects.toBeInstanceOf(PendingOperationError);
  await expect(acceptPrefixSplitCommand("combined", 1, 18)).rejects.toBeInstanceOf(PendingOperationError);
  expect(bodies).toEqual([{ expected_revision: 1, queue_entry_id: 17 }]);
  expect(await acceptPrefixSplitCommand("combined", 1, 17)).toMatchObject({ applied: true });
  expect(bodies).toHaveLength(1);
});

it("legacy split callers keep their original request payload", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    expect(JSON.parse(String(init?.body))).toEqual({ expected_revision: 1 });
    return Response.json(accepted);
  }));
  expect(await acceptPrefixSplitCommand("combined", 1)).toMatchObject({ applied: true });
});

it.each([
  ["malformed", { state: "complete" }],
  ["foreign", { state: "complete", operation_id: "other", response: { rejected_after_review_id: 7 } }],
])("%s receipt preserves the saved operation", async (_label, receipt) => {
  const original = JSON.stringify({ operationId: "original", cardId: "card-1", revision: 3, decision: "reject" });
  localStorage.setItem("tempo-pending-prefix-split-v1", original);
  const fetcher = vi.fn(async () => Response.json(receipt));
  vi.stubGlobal("fetch", fetcher);
  await expect(acceptPrefixSplitCommand("card-1", 3)).rejects.toThrow();
  expect(localStorage.getItem("tempo-pending-prefix-split-v1")).toBe(original);
  expect(fetcher).toHaveBeenCalledTimes(1);
});


it.each(["missing", "foreign"])("%s initial acknowledgment preserves original command bytes and retries the same key", async kind => {
  const storageKey = "tempo-pending-prefix-split-v1";
  const writes: { key: string | null; body: BodyInit | null | undefined }[] = [];
  const urls: string[] = [];
  let originalId = "";
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); urls.push(url);
    if (urls.length > 1) expect(localStorage.getItem(storageKey)).toBe(saved);
    if (url.includes("/api/operations/")) {
      expect(decodeURIComponent(url)).toContain(`/api/operations/${originalId}`);
      return Response.json(writes.length < 2 ? { state: "pending" } : { operation_id: originalId, state: "complete", response: accepted });
    }
    const key = new Headers(init?.headers).get("Idempotency-Key");
    writes.push({ key, body: init?.body });
    if (writes.length === 1) {
      originalId = key!;
      return Response.json(kind === "foreign" ? { operation_id: "foreign", state: "queued" } : { state: "queued" }, { status: 202 });
    }
    return Response.json({ operation_id: key, state: "queued" }, { status: 202 });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(acceptPrefixSplitCommand("combined", 1, 17)).rejects.toBeInstanceOf(PendingOperationError);
  const saved = localStorage.getItem("tempo-pending-prefix-split-v1");
  expect(saved).not.toBeNull();
  expect(JSON.parse(saved!).operationId).toBe(originalId);
  expect(urls).toHaveLength(1);
  await acceptPrefixSplitCommand("combined", 1, 17);
  expect(writes).toHaveLength(2);
  expect(writes[1]).toEqual(writes[0]);
  expect(urls.some(url => url.includes("foreign"))).toBe(false);
  expect(localStorage.getItem("tempo-pending-prefix-split-v1")).toBeNull();
});

it("reload recovers a lost acceptance receipt across a successor card and later clears only that intent", async () => {
  const original = { operationId: "original-op", decision: "accept", cardId: "combined", revision: 1, queueEntryId: 17 };
  localStorage.setItem("tempo-pending-prefix-split-v1", JSON.stringify(original));
  const waiting = { status: "waiting_review", intent_id: "intent-1", card_id: "combined", source_revision: 1, queue_entry_id: 17, reason: null };
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(init?.method).not.toBe("POST");
    const url = String(input); calls.push(url);
    if (url.includes("/api/operations/")) return Response.json({ operation_id: "original-op", state: "complete", response: waiting });
    return Response.json({ ...waiting, status: "applied", result: accepted });
  }));
  expect(await pendingPrefixSplitStatus()).toEqual(waiting);
  expect(JSON.parse(localStorage.getItem("tempo-pending-prefix-split-v1")!)).toEqual({ ...original, intentId: "intent-1" });
  expect(await pendingPrefixSplitStatus()).toEqual(accepted);
  expect(localStorage.getItem("tempo-pending-prefix-split-v1")).toBeNull();
  expect(calls[0]).toContain("/api/operations/original-op");
  expect(calls[1]).toContain("/api/cards/combined/prefix-split/intents/intent-1");
});
