import { afterEach, expect, it, vi } from "vitest";
import { reviseCardCommand } from "../../app/lib/card-revision-command";
import { PendingOperationError } from "../../app/lib/operation-status";

const revision = {
  cardId: "original", startingFen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  moves: ["d2d4"], historyMode: "preserve" as const, expectedRevision: 3,
};

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending card edit reuses its command ID and blocks a changed revision", async () => {
  const keys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: {
          card_id: "edited", replaced: true, history_mode: "preserve", revision: 7,
        },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    keys.push(key);
    expect(JSON.parse(String(init?.body))).toMatchObject({ expected_revision: 3 });
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(reviseCardCommand(revision)).rejects.toBeInstanceOf(PendingOperationError);
  await expect(reviseCardCommand({ ...revision, expectedRevision: 4 }))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(keys).toHaveLength(1);
  expect(await reviseCardCommand(revision)).toMatchObject({ card_id: "edited", revision: 7 });
  expect(keys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-card-revision-v1")).toBeNull();
});

it.each([
  ["malformed completion", { state: "complete", response: {} }],
  ["foreign completion", { state: "complete", operation_id: "foreign", response: { card_id: "edited", replaced: true, history_mode: "preserve", revision: 7 } }],
])("%s retains the exact earlier operation when a changed request arrives", async (_label, receipt) => {
  const original = JSON.stringify({ operationId: "original-operation", request: JSON.stringify(revision) });
  localStorage.setItem("tempo-pending-card-revision-v1", original);
  const fetcher = vi.fn(async () => Response.json(receipt));
  vi.stubGlobal("fetch", fetcher);
  await expect(reviseCardCommand({ ...revision, expectedRevision: 4 })).rejects.toThrow();
  expect(localStorage.getItem("tempo-pending-card-revision-v1")).toBe(original);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("keeps changed work blocked while pending and clears a confirmed terminal failure", async () => {
  const original = JSON.stringify({ operationId: "original-operation", request: JSON.stringify(revision) });
  localStorage.setItem("tempo-pending-card-revision-v1", original);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ state: "pending" }))
    .mockResolvedValueOnce(Response.json({ state: "failed", error: { message: "terminal" } })));
  await expect(reviseCardCommand({ ...revision, expectedRevision: 4 })).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-card-revision-v1")).toBe(original);
  await expect(reviseCardCommand(revision)).rejects.toThrow("terminal");
  expect(localStorage.getItem("tempo-pending-card-revision-v1")).toBeNull();
});


it.each(["missing", "foreign"])("%s initial acknowledgment preserves original command bytes and retries the same key", async kind => {
  const storageKey = "tempo-pending-card-revision-v1";
  const writes: { key: string | null; body: BodyInit | null | undefined }[] = [];
  const urls: string[] = [];
  let originalId = "";
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); urls.push(url);
    if (urls.length > 1) expect(localStorage.getItem(storageKey)).toBe(saved);
    if (url.includes("/api/operations/")) {
      expect(decodeURIComponent(url)).toContain(`/api/operations/${originalId}`);
      return Response.json(writes.length < 2 ? { state: "pending" } : { operation_id: originalId, state: "complete", response: { card_id: 'edited', replaced: true, history_mode: 'preserve', revision: 7 } });
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
  await expect(reviseCardCommand(revision)).rejects.toBeInstanceOf(PendingOperationError);
  const saved = localStorage.getItem("tempo-pending-card-revision-v1");
  expect(saved).not.toBeNull();
  expect(JSON.parse(saved!).operationId).toBe(originalId);
  expect(urls).toHaveLength(1);
  await reviseCardCommand(revision);
  expect(writes).toHaveLength(2);
  expect(writes[1]).toEqual(writes[0]);
  expect(urls.some(url => url.includes("foreign"))).toBe(false);
  expect(localStorage.getItem("tempo-pending-card-revision-v1")).toBeNull();
});
