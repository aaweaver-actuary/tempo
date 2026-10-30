import { afterEach, expect, it, vi } from "vitest";
import { studyIntentStore } from "../../database/browser/study-intent-storage";
import { recoverStudyRequests } from "../../app/lib/study-command-client";

afterEach(() => vi.unstubAllGlobals());

it("retired endgame intents settle without delivery and preserve unrelated pending reviews", async () => {
  const store = await studyIntentStore();
  const envelope = { key: "retired", kind: "endgame-command", attemptId: "old-attempt", requestId: "original-operation",
    endpoint: "/api/endgames/templates/old/attempt", revision: 1, createdAt: "2026-10-29T00:00:00Z",
    payload: { body: {}, headers: {}, scope: "endgame:old" } };
  await store.put(envelope);
  await store.put({ ...envelope, key: "review", kind: "review", requestId: "review-operation",
    endpoint: "/api/cards/kept/review", payload: { backendId: "kept", outcome: "correct" } });
  const before = await store.get("review");
  const fetcher = vi.fn(() => { throw new Error("Retirement must never send a request"); });
  vi.stubGlobal("fetch", fetcher);
  await recoverStudyRequests();
  expect(await store.get("retired")).toMatchObject({ state: "discarded", requestId: "original-operation", receipt: { type: "retired" } });
  expect(await store.get("review")).toEqual(before);
  await recoverStudyRequests();
  expect(fetcher).not.toHaveBeenCalled();
});
