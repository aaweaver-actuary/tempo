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
