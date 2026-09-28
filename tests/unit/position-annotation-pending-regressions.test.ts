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
