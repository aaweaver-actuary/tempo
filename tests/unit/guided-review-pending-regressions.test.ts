import { afterEach, expect, it, vi } from "vitest";
import { startGuidedReviewCommand, submitGuidedReviewCommand } from "../../app/lib/guided-review-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending guided review start reuses its operation ID before showing a session", async () => {
  const sentKeys: string[] = [];
  let receiptReads = 0;
  const session = { id: "session-one", game_id: "game-one", status: "active",
    current_index: 0, total: 1, current: null, attempts: [] };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 2 ? { state: "pending" } :
        { state: "complete", response: session });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    sentKeys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(startGuidedReviewCommand("game-one")).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-guided-review-start-v1:game-one")).toBe(sentKeys[0]);
  expect(await startGuidedReviewCommand("game-one")).toEqual(session);
  expect(sentKeys).toHaveLength(1);
});

it("a pending guided correction blocks another move and confirms the saved attempt", async () => {
  const sentKeys: string[] = [];
  let receiptReads = 0;
  const result = { correct: true, revealed: { finding_id: "finding-one" }, session: {
    id: "session-one", game_id: "game-one", status: "complete",
    current_index: 1, total: 1, current: null, attempts: [],
  } };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } :
        { state: "complete", response: result });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    sentKeys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(submitGuidedReviewCommand("session-one", 0, "e2e4"))
    .rejects.toBeInstanceOf(PendingOperationError);
  await expect(submitGuidedReviewCommand("session-one", 0, "d2d4"))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(sentKeys).toHaveLength(1);
  expect(await submitGuidedReviewCommand("session-one", 0, "e2e4")).toEqual(result);
  expect(localStorage.getItem("tempo-pending-guided-review-attempt-v1:session-one:0")).toBeNull();
});
