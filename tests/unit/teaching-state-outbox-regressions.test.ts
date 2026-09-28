import { beforeEach, expect, it, vi } from "vitest";
import {
  enqueueTeachingState, flushTeachingStates, pendingTeachingStates,
} from "../../app/lib/teaching-state-outbox";

beforeEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

it("teaching save survives an ambiguous response and replays with the same command ID", async () => {
  const teachingState = { cardId: "opening-1", revision: 2, ply: 3 };
  enqueueTeachingState(teachingState);
  enqueueTeachingState(teachingState);
  expect(pendingTeachingStates()).toEqual([teachingState]);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ operation_id: "teaching:opening-1:2:3", state: "pending" }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ state: "pending" }))
    .mockResolvedValueOnce(Response.json({ cardId: "opening-1", revision: 2, ply: 3, taughtAt: "saved" }));
  vi.stubGlobal("fetch", fetcher);
  await expect(flushTeachingStates()).rejects.toThrow("still pending");
  expect(pendingTeachingStates()).toEqual([teachingState]);
  await flushTeachingStates();
  expect(pendingTeachingStates()).toEqual([]);
  expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBe("teaching:opening-1:2:3");
  expect(fetcher.mock.calls[2][1].headers["Idempotency-Key"]).toBe("teaching:opening-1:2:3");
});

it("failed teaching save remains queued for a later retry", async () => {
  const state = { cardId: "opening-2", revision: 1, ply: 0 };
  enqueueTeachingState(state);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ detail: "Database unavailable" }, { status: 503 })));
  await expect(flushTeachingStates()).rejects.toThrow("Database unavailable");
  expect(pendingTeachingStates()).toEqual([state]);
});
