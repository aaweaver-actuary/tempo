import { afterEach, expect, it, vi } from "vitest";
import { PendingOperationError } from "../../app/lib/operation-status";
import { savePositionAnnotation } from "../../app/utils/position-annotations";
import type { PositionAnnotation } from "../../app/types";

const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const note = {
  repertoireId: "rep", fenKey: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -",
  comment: "Plan", arrows: [], squares: [], updatedAt: "2026-09-27T00:00:00Z",
} as unknown as PositionAnnotation;

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending note keeps its operation ID and never enters the confirmed local copy", async () => {
  const submittedKeys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: note,
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    submittedKeys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(savePositionAnnotation(note, startingFen)).rejects.toBeInstanceOf(
    PendingOperationError,
  );
  expect(localStorage.getItem("tempo-position-annotations")).toBeNull();
  const saved = await savePositionAnnotation(note, startingFen);
  expect(saved.comment).toBe("Plan");
  expect(submittedKeys).toHaveLength(2);
  expect(submittedKeys[0]).toBe(submittedKeys[1]);
  expect(localStorage.getItem("tempo-pending-position-annotation-v1")).toBeNull();
});

it("a failed note save leaves no confirmed local annotation", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(
    { detail: "Save worker unavailable" }, { status: 503 },
  )));
  await expect(savePositionAnnotation(note, startingFen)).rejects.toThrow();
  expect(localStorage.getItem("tempo-position-annotations")).toBeNull();
});

it.each(["malformed", "foreign"])("a %s note receipt preserves the original pending bytes", async kind => {
  const original = JSON.stringify({ operationId: "original", body: "original-body", repertoireId: "rep" });
  localStorage.setItem("tempo-pending-position-annotation-v1", original);
  const fetcher = vi.fn(async () => Response.json(kind === "malformed" ? { state: "complete", response: {} } : {
    state: "complete", operation_id: "foreign", response: note,
  }));
  vi.stubGlobal("fetch", fetcher);
  await expect(savePositionAnnotation({ ...note, comment: "changed" }, startingFen)).rejects.toThrow();
  expect(localStorage.getItem("tempo-pending-position-annotation-v1")).toBe(original);
  expect(fetcher).toHaveBeenCalledTimes(1);
});


it.each(["missing", "foreign"])("%s initial acknowledgment preserves original command bytes and retries the same key", async kind => {
  const storageKey = "tempo-pending-position-annotation-v1";
  const writes: { key: string | null; body: BodyInit | null | undefined }[] = [];
  const urls: string[] = [];
  let originalId = "";
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); urls.push(url);
    if (urls.length > 1) expect(localStorage.getItem(storageKey)).toBe(saved);
    if (url.includes("/api/operations/")) {
      expect(decodeURIComponent(url)).toContain(`/api/operations/${originalId}`);
      return Response.json(writes.length < 2 ? { state: "pending" } : { operation_id: originalId, state: "complete", response: note });
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
  await expect(savePositionAnnotation(note, startingFen)).rejects.toBeInstanceOf(PendingOperationError);
  const saved = localStorage.getItem("tempo-pending-position-annotation-v1");
  expect(saved).not.toBeNull();
  expect(JSON.parse(saved!).operationId).toBe(originalId);
  expect(urls).toHaveLength(1);
  await savePositionAnnotation(note, startingFen);
  expect(writes).toHaveLength(2);
  expect(writes[1]).toEqual(writes[0]);
  expect(urls.some(url => url.includes("foreign"))).toBe(false);
  expect(localStorage.getItem("tempo-pending-position-annotation-v1")).toBeNull();
});
