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

it("C21 mixed retained frozen work holds retired and unknown originals while supported work progresses", async () => {
  const { webcrypto } = await import("node:crypto");
  vi.stubGlobal("crypto", webcrypto);
  const store = await studyIntentStore();
  const make = (id: string, kind: string, endpoint: string, scope: string, body: Record<string, unknown>) => ({ key: `${scope}:${id}`, kind,
    attemptId: id, requestId: id, endpoint, revision: 1, createdAt: "original", payload: { scope, body, headers: { "Idempotency-Key": id } } });
  const move = make("mixed-move", "study-command", "/api/studies/mixed/exercises/move/attempts", "study:mixed:move:practice:1",
    { attempt_id: "mixed-move", revision: 1, context: "practice", answer: { type: "move_line", moves: ["e2e4"] } });
  const parent = make("mixed-explanation", "study-command", "/api/studies/mixed/exercises/explanation/attempts", "study:mixed:explanation:practice:1",
    { attempt_id: "mixed-explanation", revision: 1, context: "practice", answer: { type: "explanation", text: "answer", ready: true } });
  const child = { ...make("mixed-child", "study-command", `${parent.endpoint}/mixed-explanation/self-assess`, `${parent.payload.scope}:self-assess`, { rating: "correct" }), attemptId: parent.attemptId };
  const retired = [make("mixed-defense", "defense-command", "/api/defense/attempt", "retired:defense", { original: "defense" }),
    make("mixed-endgame", "endgame-command", "/api/endgames/attempt", "retired:endgame", { original: "endgame" })];
  const unknown = make("mixed-unknown", "study-command", "/api/studies/mixed/exercises/legacy/author", "legacy", { original: "unknown" });
  const tactic = make("mixed-tactic", "tactic-command", "/api/tactics/attempt", "tactic:deck:polish-1", { attempt_id: "mixed-tactic", puzzle_id: "polish-1", deck_id: "deck", correct: true, clean: true,
    source_fen: "q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17", moves: ["e8d7", "a2e6", "d7d8", "f7f8"] });
  const records = [move, parent, child, ...retired, unknown, tactic];
  for (const record of records) await store.put(record, { started: true });
  const posts: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); if (init?.method === "POST") { posts.push(url); return new Response(null, { status: 500 }); }
    const id = decodeURIComponent(url.split("/").at(-1)!);
    if (id === unknown.requestId) return new Response(null, { status: 404 });
    const pending = id === parent.requestId;
    const response = id === tactic.requestId ? { card_id: "7102eb92e00287f834ec6f5a2dd1afaaebfdafd3c649b975444a77ccff836d5a", mode: "normal", next_due: "2026-11-13" }
      : pending ? { attempt_id: parent.attemptId, assessment: { outcome: "needs_self_assessment", feedback: "compare" }, pending_self_assessment: true }
      : { attempt_id: id === child.requestId ? parent.attemptId : id, assessment: { outcome: "correct", feedback: "saved" }, rating: "correct", requested_rating: "correct", persisted: true, review: null };
    return Response.json({ operation_id: id, state: "complete", response });
  }));
  try {
    await recoverStudyRequests();
    for (const record of [move, parent, child, tactic]) expect(await store.get(record.key)).toMatchObject({ state: "committed", requestId: record.requestId, payload: record.payload });
    for (const record of [...retired, unknown]) expect(await store.get(record.key)).toMatchObject({ state: "pending", requestId: record.requestId, payload: record.payload });
    expect(posts).toEqual([]);
  } finally { vi.unstubAllGlobals(); }
});
