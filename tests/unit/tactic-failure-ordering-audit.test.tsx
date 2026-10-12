import { act, render, waitFor } from "@testing-library/react";
import { Chess, type Square } from "chess.js";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Home from "../../app/views/home_view";
import { useTrainingStore } from "../../app/state/training-store";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { pendingReviews, flushPendingReviews } from "../../app/lib/review-outbox";
import { flushTrainingFailures, pendingTrainingFailureContexts } from "../../app/lib/training-failure-outbox";
import * as local from "../../app/utils/local";

vi.mock("../../app/components/board/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn(),
  moveSoundEnabled: () => false, prepareMoveSounds: vi.fn(), cancelMoveSounds: vi.fn() }));

const tacticFen = "q5nr/1ppknQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 w - - 1 18";
const tacticMoves = ["a2e6", "d7d8", "f7f8"];
const nextCard = { id: "next-tactic", queue_entry_id: 9202, revision: 1, start_fen: new Chess().fen(),
  moves: ["e2e4"], content_type: "tactic", repertoire_name: "Next tactic", repertoire_source: "test" };

function mockDailyTactic(firstFailure: (options?: RequestInit) => Promise<Response>) {
  vi.spyOn(local, "usesLocalApi").mockReturnValue(true);
  const tactic = { id: "study-only-tactic", queue_entry_id: 9201, revision: 1, start_fen: tacticFen,
    moves: tacticMoves, content_type: "tactic", repertoire_id: "__tactics__", repertoire_name: "Tactics",
    repertoire_source: "test", first_correct_at: "2000-01-01T00:00:00Z" };
  let failedPersisted = false;
  let reviewed = false;
  let competing = false;
  const failureRequests: { body: unknown; key: string }[] = [];
  const reviewRequests: { outcome: string; guided: boolean }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/settings")) return Response.json({ study_only_mode: true });
    if (url.includes("/api/queue/window")) return Response.json({ cards: reviewed || competing ? [nextCard]
      : [{ ...tactic, attempt_failed: failedPersisted }, nextCard], count: reviewed ? 1 : 2 });
    if (url.endsWith("/fail")) {
      failureRequests.push({ body: JSON.parse(String(options?.body)), key: new Headers(options?.headers).get("Idempotency-Key")! });
      if (failureRequests.length === 1) return firstFailure(options);
      failedPersisted = true;
      return Response.json({ attempt_failed: true });
    }
    if (url.endsWith("/review")) {
      reviewRequests.push(JSON.parse(String(options?.body)));
      reviewed = true;
      return Response.json({ persisted: true });
    }
    if (url.includes("/api/operations/")) return new Response(null, { status: 404 });
    if (url.includes("/move-membership?")) return Response.json({ detail: "No opening sources for tactics" }, { status: 503 });
    // Normal mode also starts with unavailable bulk opening evidence.
    if (url.includes("/repertoire/lines")) return new Promise<Response>(() => {});
    return Response.json({ providers: [], states: [], repertoires: [], discoveries: [], annotations: [] });
  }));
  return { tactic, failureRequests, reviewRequests, advanceCompeting: () => { competing = true; } };
}

async function readyDailyTactic() {
  const view = render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().getCard().backendId).toBe("study-only-tactic"));
  await waitFor(() => expect(useBoardShellStore.getState().board.onMove).toBeTypeOf("function"));
  return view;
}

async function play(moveUci: string) {
  await act(async () => useBoardShellStore.getState().board.onMove?.(moveUci.slice(0, 2) as Square, moveUci.slice(2, 4) as Square));
}

async function finishTactic() {
  vi.useFakeTimers();
  await play("a2e6");
  await act(async () => vi.advanceTimersByTimeAsync(420));
  await play("f7f8");
}

beforeEach(() => vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} }));
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

function deferredFailure() {
  let resolve!: (response: Response) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<Response>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

it("tactic_completion_while_failure_pending_remains_guided_again", async () => {
  const held = deferredFailure();
  const harness = mockDailyTactic(() => held.promise);
  await readyDailyTactic();
  await play("a3b4");
  const context = pendingTrainingFailureContexts()[0];
  expect(context).toMatchObject({ queueEntryId: 9201, backendId: "study-only-tactic", expectedRevision: 1 });
  expect(useTrainingStore.getState()).toMatchObject({ currentFenString: tacticFen, step: 0, isAttemptFailed: true });
  await finishTactic();
  expect(pendingReviews()[0]).toMatchObject({ outcome: "again", guided: true, queueEntryId: 9201, expectedRevision: 1 });
  await act(async () => vi.advanceTimersByTimeAsync(750));
  expect(harness.reviewRequests).toEqual([expect.objectContaining({ outcome: "again", guided: true })]);
  held.resolve(Response.json({ attempt_failed: true }));
  await act(async () => flushTrainingFailures());
});

it.each(["rejected", "lost-response", "timeout"])("tactic_failure_rejection_and_retry_preserve_attempt_identity (%s)", async mode => {
  const held = deferredFailure();
  const harness = mockDailyTactic(options => {
    options?.signal?.addEventListener("abort", () => held.reject(new DOMException("aborted", "AbortError")), { once: true });
    return held.promise;
  });
  await readyDailyTactic();
  if (mode === "timeout") vi.useFakeTimers();
  await play("a3b4");
  const failedFlush = flushTrainingFailures();
  const rejection = expect(failedFlush).rejects.toThrow(mode === "timeout" ? "timed out" : mode === "rejected" ? "audit unavailable" : "committed response lost");
  await act(async () => {
    if (mode === "timeout") await vi.advanceTimersByTimeAsync(15001);
    else if (mode === "rejected") held.resolve(Response.json({ detail: "audit unavailable" }, { status: 503 }));
    else held.reject(new TypeError("committed response lost"));
    await rejection;
  });
  vi.useRealTimers();
  expect(pendingTrainingFailureContexts()[0]).toMatchObject({ queueEntryId: 9201, backendId: "study-only-tactic", expectedRevision: 1 });
  await act(async () => flushTrainingFailures());
  expect(harness.failureRequests).toHaveLength(2);
  expect(harness.failureRequests[1]).toEqual(harness.failureRequests[0]);
  expect(pendingTrainingFailureContexts()).toEqual([]);
  expect(useTrainingStore.getState().isAttemptFailed).toBe(true);
});

it("tactic_failure_lost_response_replay_is_idempotent", async () => {
  const held = deferredFailure();
  const harness = mockDailyTactic(() => held.promise);
  await readyDailyTactic();
  await play("a3b4");
  const failedFlush = flushTrainingFailures();
  const rejection = expect(failedFlush).rejects.toThrow("committed response lost");
  await act(async () => { held.reject(new TypeError("committed response lost")); await rejection; });
  await act(async () => flushTrainingFailures());
  expect(harness.failureRequests[1].key).toBe(harness.failureRequests[0].key);
  await finishTactic();
  await act(async () => vi.advanceTimersByTimeAsync(750));
  await act(async () => flushPendingReviews());
  expect(harness.reviewRequests).toEqual([expect.objectContaining({ outcome: "again", guided: true })]);
});

it("tactic_reload_before_failure_commit_preserves_guided_state", async () => {
  const held = deferredFailure();
  mockDailyTactic(() => held.promise);
  const view = await readyDailyTactic();
  await play("a3b4");
  view.unmount();
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  await readyDailyTactic();
  expect(useTrainingStore.getState()).toMatchObject({ isAttemptFailed: true, currentFenString: tacticFen, showHint: true });
  expect(useTrainingStore.getState().getCard()).toMatchObject({ queueEntryId: 9201, backendId: "study-only-tactic", revision: 1 });
  await finishTactic();
  expect(pendingReviews()[0]).toMatchObject({ outcome: "again", guided: true });
  held.resolve(Response.json({ attempt_failed: true }));
  await act(async () => flushTrainingFailures());
});

it("tactic_reordered_failure_and_review_do_not_duplicate_results", async () => {
  const held = deferredFailure();
  const harness = mockDailyTactic(() => held.promise);
  await readyDailyTactic();
  await play("a3b4");
  await finishTactic();
  await act(async () => vi.advanceTimersByTimeAsync(750));
  expect(harness.failureRequests).toHaveLength(2);
  expect(harness.failureRequests[1].key).not.toBe(harness.failureRequests[0].key);
  expect(harness.reviewRequests).toHaveLength(1);
  held.resolve(Response.json({ attempt_failed: true }));
  await act(async () => flushTrainingFailures());
  await act(async () => flushPendingReviews());
  expect(harness.reviewRequests).toHaveLength(1);
  expect(pendingTrainingFailureContexts()).toEqual([]);
});

it("tactic_competing_queue_advance_cannot_transfer_failure", async () => {
  const held = deferredFailure();
  const harness = mockDailyTactic(() => held.promise);
  const view = await readyDailyTactic();
  await play("a3b4");
  harness.advanceCompeting();
  view.unmount();
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().getCard().backendId).toBe("next-tactic"));
  expect(useTrainingStore.getState().isAttemptFailed).toBe(false);
  expect(pendingTrainingFailureContexts()[0].backendId).toBe("study-only-tactic");
  held.resolve(Response.json({ detail: "old attempt unavailable", code: "queue_attempt_unprovable" }, { status: 409 }));
  await act(async () => { await flushTrainingFailures().catch(() => undefined); });
  expect(useTrainingStore.getState().isAttemptFailed).toBe(false);
});
