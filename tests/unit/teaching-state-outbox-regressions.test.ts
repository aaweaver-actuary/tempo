import { beforeEach, expect, it, vi } from "vitest";
import {
  enqueueTeachingState, flushTeachingStates, pendingTeachingStates,
} from "../../app/lib/teaching-state-outbox";

beforeEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

it("teaching save survives an ambiguous response and replays with the same command ID", async () => {
  const teachingState = { cardId: "opening-1", revision: 2, ply: 3 };
  await enqueueTeachingState(teachingState);
  await enqueueTeachingState(teachingState);
  expect(pendingTeachingStates()).toEqual([teachingState]);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ operation_id: "teaching:opening-1:2:3", state: "pending" }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ state: "pending" }))
    .mockResolvedValueOnce(Response.json({ state: "complete", response: { cardId: "opening-1", revision: 2, ply: 3, taughtAt: "saved" } }));
  vi.stubGlobal("fetch", fetcher);
  await expect(flushTeachingStates()).rejects.toThrow("still pending");
  expect(pendingTeachingStates()).toEqual([teachingState]);
  await flushTeachingStates();
  expect(pendingTeachingStates()).toEqual([]);
  expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBe("teaching:opening-1:2:3");
  expect(String(fetcher.mock.calls[2][0])).toContain("/api/operations/teaching%3Aopening-1%3A2%3A3");
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
});

it("failed teaching save remains queued for a later retry", async () => {
  const state = { cardId: "opening-2", revision: 1, ply: 0 };
  await enqueueTeachingState(state);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ detail: "Database unavailable" }, { status: 503 })));
  await expect(flushTeachingStates()).rejects.toThrow("Database unavailable");
  expect(pendingTeachingStates()).toEqual([state]);
});

it("teaching receipt for another fact cannot settle the retained original", async () => {
  const state = { cardId: "original", revision: 2, ply: 0 };
  await enqueueTeachingState(state);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ ...state, cardId: "other", taughtAt: "saved" })));
  await expect(flushTeachingStates()).rejects.toThrow("original teaching fact");
  expect(pendingTeachingStates()).toEqual([state]);
});

it.each(["missing", "foreign"])("%s teaching acknowledgment preserves the original fact and never polls another operation", async kind => {
  const state = { cardId: "original-ack", revision: 3, ply: 2 };
  await enqueueTeachingState(state);
  const fetcher = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("teaching:original-ack:3:2");
    expect(init?.body).toBe(JSON.stringify({ revision: 3, ply: 2 }));
    return Response.json(kind === "foreign" ? { operation_id: "foreign", state: "queued" } : { state: "queued" }, { status: 202 });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(flushTeachingStates()).rejects.toMatchObject({ name: "PendingOperationError", operationId: "teaching:original-ack:3:2" });
  expect(pendingTeachingStates()).toEqual([state]);
  expect(fetcher).toHaveBeenCalledOnce();
});

it('FE23 teaching delivery imports and constructs without storage or session registration', async () => {
  vi.resetModules(); const touch = vi.fn(() => { throw new Error('composition must remain inert'); });
  vi.doMock('../../database/browser/study-intent-storage', touch); vi.doMock('../../app/lib/study-session-runtime', touch);
  try {
    const { createTeachingDelivery } = await import('../../app/lib/teaching-delivery');
    const factory = createTeachingDelivery({} as Parameters<typeof createTeachingDelivery>[0], touch, {} as Parameters<typeof createTeachingDelivery>[2]);
    expect(typeof factory.deliver).toBe('function'); expect(typeof factory.checkReceipt).toBe('function'); expect(touch).not.toHaveBeenCalled();
  } finally { vi.doUnmock('../../database/browser/study-intent-storage'); vi.doUnmock('../../app/lib/study-session-runtime'); vi.resetModules(); }
});
