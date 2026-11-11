import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";
import { advanceStudyTimers, settleStudyStorageTasks } from "./support/study-intent-fixtures";

const operationId = "queue.issue_head:v1:test";
const options = { preparePhoneQueue: false, replaySavedReviews: false };
const pending = () => Response.json({ operation_id: operationId, state: "queued" }, { status: 202, headers: { "Retry-After": "1" } });

beforeEach(() => { useTrainingStore.setState(useTrainingStore.getInitialState(), true); vi.useFakeTimers(); });
afterEach(async () => { await settleStudyStorageTasks(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it.each([
  { state: "complete", operation_id: operationId },
  { state: "complete", operation_id: "wrong", response: null },
  { state: "complete", response: null },
  { state: "complete", operation_id: operationId, response: undefined },
])("unconfirmed issuance receipt cannot hydrate a queue: %j", async receipt => {
  const request = vi.fn().mockResolvedValueOnce(pending()).mockResolvedValueOnce(Response.json(receipt));
  vi.stubGlobal("fetch", request);
  const result = fetchAndInitializeQueue(false, options).catch(error => error);
  await advanceStudyTimers(1500);
  expect(await result).toBeInstanceOf(Error);
  expect(request).toHaveBeenCalledTimes(2);
  expect(useTrainingStore.getState().queueStatus).toEqual({ kind: "unavailable" });
});

it("explicit null receipt requires a fresh queue and establishes authoritative empty", async () => {
  const request = vi.fn().mockResolvedValueOnce(pending())
    .mockResolvedValueOnce(Response.json({ operation_id: operationId, state: "complete", response: null }))
    .mockResolvedValueOnce(Response.json({ count: 0, cards: [] }));
  vi.stubGlobal("fetch", request);
  const result = fetchAndInitializeQueue(false, options);
  await advanceStudyTimers(1500);
  expect(await result).toBe("exhausted");
  expect(request).toHaveBeenCalledTimes(3);
  expect(useTrainingStore.getState().queueStatus).toEqual({ kind: "empty" });
});

it("malformed queue does not become an authoritative empty queue", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ cards: [], projection: { state: "ready" } })));
  const result = fetchAndInitializeQueue(false, options).catch(error => error);
  await advanceStudyTimers(100);
  expect(await result).toBeInstanceOf(Error);
  expect(useTrainingStore.getState().queueStatus).toEqual({ kind: "unavailable" });
});
