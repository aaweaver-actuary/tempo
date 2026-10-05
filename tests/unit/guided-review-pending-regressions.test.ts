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
  await expect(submitGuidedReviewCommand("session-one", 0, "e2e4", "finding-one"))
    .rejects.toBeInstanceOf(PendingOperationError);
  await expect(submitGuidedReviewCommand("session-one", 0, "d2d4", "finding-one"))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(sentKeys).toHaveLength(1);
  expect(await submitGuidedReviewCommand("session-one", 0, "e2e4", "finding-one")).toEqual(result);
  expect(localStorage.getItem("tempo-pending-guided-review-attempt-v1:session-one:0")).toBeNull();
});

it("guided correction validates finding identity after completed findings disappear", async () => {
  const result = { correct: true, revealed: { finding_id: "finding-three" }, session: {
    id: "session-one", game_id: "game-one", status: "complete",
    current_index: 2, total: 2, current: null, attempts: [],
  } };
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    expect(JSON.parse(String(init?.body))).toEqual({ move_uci: "e2e4", finding_id: "finding-three" });
    return Response.json(result);
  });
  vi.stubGlobal("fetch", fetchMock);
  expect(await submitGuidedReviewCommand("session-one", 2, "e2e4", "finding-three")).toEqual(result);
});

it("a stale guided target rejection clears its pending operation without resubmission", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Guided review changed" }, { status: 409 })));
  await expect(submitGuidedReviewCommand("session-one", 0, "e2e4", "finding-one"))
    .rejects.toThrow("Guided review changed");
  expect(localStorage.getItem("tempo-pending-guided-review-attempt-v2:session-one:finding-one")).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("a committed stale guided target rejection is normalized from a recovered receipt", async () => {
  localStorage.setItem("tempo-pending-guided-review-attempt-v2:session-one:finding-one",
    JSON.stringify({ operationId: "saved-one", moveUci: "e2e4", findingId: "finding-one" }));
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state: "complete", response: {
    guided_review_error: { status_code: 409, detail: "Guided review changed" },
  } })));
  await expect(submitGuidedReviewCommand("session-one", 0, "e2e4", "finding-one"))
    .rejects.toThrow("Guided review changed");
  expect(localStorage.getItem("tempo-pending-guided-review-attempt-v2:session-one:finding-one")).toBeNull();
});

it("legacy unresolved move-only guided attempts cannot be reassigned to another finding", async () => {
  localStorage.setItem("tempo-pending-guided-review-attempt-v1:session-one:0",
    JSON.stringify({ operationId: "legacy-one", moveUci: "e2e4" }));
  const fetchMock = vi.fn(async () => Response.json({ state: "pending" }));
  vi.stubGlobal("fetch", fetchMock);
  await expect(submitGuidedReviewCommand("session-one", 0, "e2e4", "finding-two"))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(String(fetchMock.mock.calls[0])).toContain("/api/operations/legacy-one");
  expect(localStorage.getItem("tempo-pending-guided-review-attempt-v1:session-one:0")).not.toBeNull();
});
