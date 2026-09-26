import { beforeEach, expect, it, vi } from "vitest";
import {
  enqueueTrainingFailure,
  flushTrainingFailures,
  pendingTrainingFailures,
} from "../../app/lib/training-failure-outbox";

beforeEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

it("intentional failed attempt survives reload and replays once for its queue entry", async () => {
  enqueueTrainingFailure(1974);
  enqueueTrainingFailure(1974);
  expect(pendingTrainingFailures()).toEqual([1974]);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ detail: "busy" }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ attempt_failed: true }));
  vi.stubGlobal("fetch", fetcher);
  await expect(flushTrainingFailures()).rejects.toThrow("busy");
  expect(pendingTrainingFailures()).toEqual([1974]);
  await flushTrainingFailures();
  expect(pendingTrainingFailures()).toEqual([]);
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
    expect.stringContaining("/api/queue/entries/1974/fail"),
    expect.stringContaining("/api/queue/entries/1974/fail"),
  ]);
});

it("stale failed-attempt marker cannot fail a different queued entry", async () => {
  enqueueTrainingFailure(1974);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 })));
  await expect(flushTrainingFailures()).rejects.toThrow("no longer active");
  expect(pendingTrainingFailures()).toEqual([]);
});
