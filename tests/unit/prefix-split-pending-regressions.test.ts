import { afterEach, expect, it, vi } from "vitest";
import { acceptPrefixSplitCommand, rejectPrefixSplitCommand } from "../../app/lib/prefix-split-command";
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
  expect((await acceptPrefixSplitCommand("combined", 1, 17)).applied).toBe(true);
  expect(bodies).toHaveLength(1);
});

it("legacy split callers keep their original request payload", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    expect(JSON.parse(String(init?.body))).toEqual({ expected_revision: 1 });
    return Response.json(accepted);
  }));
  expect((await acceptPrefixSplitCommand("combined", 1)).applied).toBe(true);
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
