import { expect, it, vi } from "vitest";
import { studyIntentStore } from "../../database/browser/study-intent-storage";
import { recoverStudyRequests } from "../../app/lib/study-command-client";

it("C21 original pending parent releases only its own validated self-assessment once", async () => {
  const store = await studyIntentStore();
  const scope = "study:c21:exercise:practice:1", attempt = "c21-original", parentId = "c21-parent", childId = "c21-child";
  const endpoint = "/api/studies/c21/exercises/exercise/attempts";
  const parent = { key: `${scope}:${parentId}`, kind: "study-command", attemptId: attempt, requestId: parentId, endpoint, revision: 1, createdAt: "original",
    payload: { scope, headers: { "Idempotency-Key": parentId }, body: { attempt_id: attempt, revision: 1, context: "practice", answer: { type: "explanation", text: "answer", ready: true } } } };
  const child = { key: `${scope}:self-assess:${childId}`, kind: "study-command", attemptId: attempt, requestId: childId, endpoint: `${endpoint}/${attempt}/self-assess`, revision: 1, createdAt: "original",
    payload: { scope: `${scope}:self-assess`, headers: { "Idempotency-Key": childId }, body: { rating: "correct" } } };
  await store.put(parent, { started: true }); await store.put(child, { started: true });
  let phase = 0; const reads: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(init?.method).not.toBe("POST");
    const id = decodeURIComponent(String(input).split("/").at(-1)!); reads.push(id);
    if (id === parentId) return Response.json(phase === 0 ? { operation_id: "foreign", state: "pending" } : { operation_id: parentId, state: "complete", response: {
      attempt_id: attempt, assessment: { outcome: "needs_self_assessment", feedback: "compare" }, pending_self_assessment: true } });
    return Response.json({ operation_id: childId, state: "complete", response: { attempt_id: phase === 1 ? "foreign" : attempt,
      assessment: { outcome: "correct", feedback: "saved" }, rating: "correct", requested_rating: "correct", persisted: true, review: null } });
  }));
  try {
    await recoverStudyRequests();
    expect(reads).not.toContain(childId);
    expect(await store.get(parent.key)).toMatchObject({ state: "pending", payload: parent.payload });
    expect(await store.get(child.key)).toMatchObject({ state: "pending", payload: child.payload });
    phase = 1; await recoverStudyRequests();
    expect(await store.get(parent.key)).toMatchObject({ state: "committed", receipt: { attempt_id: attempt, pending_self_assessment: true }, payload: parent.payload });
    expect(await store.get(child.key)).toMatchObject({ state: "pending", payload: child.payload });
    phase = 2; await recoverStudyRequests();
    expect(await store.get(child.key)).toMatchObject({ state: "committed", receipt: { attempt_id: attempt, persisted: true }, payload: child.payload });
    const count = reads.length; await recoverStudyRequests(); expect(reads).toHaveLength(count);
  } finally { vi.unstubAllGlobals(); }
});
