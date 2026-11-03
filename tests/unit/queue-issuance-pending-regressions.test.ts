import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import { canonicalFenKey } from "../../app/utils/canonical-line";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";
import { clearNotificationHistory } from "../../app/lib/notifications";
import { clearDebugErrors } from "../../app/lib/debug-reporting";
import { asCardId, asFenString, asQueueEntryId, asSanMove, type PracticeCard } from "../../app/types";
import { advanceStudyTimers, settleStudyStorageTasks } from "./support/study-intent-fixtures";
vi.mock("../../app/lib/offline-shell", () => ({ waitForOfflineShell: vi.fn().mockResolvedValue(undefined) }));
const operationId = "queue.issue_head:v1:" + "a".repeat(64) + ":000000000000";
const pending = (state = "executing") => Response.json({ operation_id: operationId, state },
  { status: 202, headers: { Location: `/api/operations/${operationId}`, "Retry-After": "1" } });
const receipt = (state: string, extra = {}) => Response.json({ operation_id: operationId, state, ...extra });
const options = { preparePhoneQueue: false, replaySavedReviews: false };
const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const activeCard: PracticeCard = {
  id: asCardId("desktop-active"), backendId: asCardId("desktop-active"),
  queueEntryId: asQueueEntryId(801), kind: "opening", title: "Desktop active", subtitle: "",
  orientation: "white", revision: 1, startingFen: asFenString(startingFen),
  moves: [asSanMove("e4"), asSanMove("e5"), asSanMove("Nf3")], userMoveTarget: 2,
};

// A real wire plan and evidence are passed through the queue adapter and store.
function issuedPositionCard() {
  const moves = ["e2e4", "e7e5", "g1f3", "b8c6"];
  const board = new Chess();
  const decisions: { move_offset: number; position_key: string; expected_uci: string }[] = [];
  const manifestDecisions: { decision_id: string; decision_index: number; move_offset: number; fen: string; expected_uci: string }[] = [];
  moves.forEach((move, offset) => {
    if (board.turn() === "w") {
      decisions.push({ move_offset: offset, position_key: canonicalFenKey(board.fen()), expected_uci: move });
      manifestDecisions.push({ decision_id: String(decisions.length).repeat(64), decision_index: decisions.length - 1,
        move_offset: offset, fen: board.fen(), expected_uci: move });
    }
    board.move(move);
  });
  return {
    id: "italian", queue_entry_id: 42, revision: 3, start_fen: startingFen, moves,
    content_type: "opening", repertoire_name: "Italian", repertoire_source: "synthetic", trained_color: "white",
    opening_decision_manifest: { manifest_version: 1, policy_version: 1, manifest_id: "f".repeat(64), presentation_snapshot_id: 1,
      repertoire_id: "rep-italian", card_id: "italian", card_revision: 3, trained_color: "white", decisions: manifestDecisions },
    position_study_mode: "position", position_study_hold: null,
    position_presentation: { version: 1, plan_id: "a".repeat(64), original_card_id: "italian", original_card_revision: 3,
      queue_entry_id: 42, attempt_id: "issued-attempt-42", owner_epoch: "epoch-1", route_id: "authored-route",
      target_ply: 0, starting_fen: startingFen, moves_uci: moves, decision_slots: decisions,
      source_digest: "source-1", local_day: "2026-11-02", skipped_prefix_proof_ids: [] },
  };
}
const positionQueue = (card: unknown) => Response.json({ count: 1, cards: [card] });
const busyPositionCard = () => ({ ...issuedPositionCard(), position_presentation: null, position_study_hold: "position_card_busy" });


beforeEach(() => {
  clearDebugErrors(); clearNotificationHistory();
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  vi.useFakeTimers();
});
afterEach(() => { vi.unstubAllGlobals(); });

it("pending issuance follows only the original operation then installs the fresh authoritative plan", async () => {
  const plan = issuedPositionCard();
  const request = vi.fn().mockResolvedValueOnce(pending()).mockResolvedValueOnce(receipt("executing"))
    .mockResolvedValueOnce(receipt("complete", { response: { cards: [{ id: "stale-command-result" }] } }))
    .mockResolvedValueOnce(positionQueue(plan));
  vi.stubGlobal("fetch", request);
  const loading = fetchAndInitializeQueue(false, options);
  await advanceStudyTimers(3000); await loading;
  expect(request.mock.calls.map(call => String(call[0]).includes("/api/operations/"))).toEqual([false, true, true, false]);
  expect(request.mock.calls.slice(1, 3).every(call => String(call[0]).endsWith(encodeURIComponent(operationId)))).toBe(true);
  expect(useTrainingStore.getState().getCard().positionPresentation).toEqual(plan.position_presentation);
  expect(useTrainingStore.getState().attempt.attemptId).toBe(plan.position_presentation.attempt_id);
});

it("unknown issuance has a default 15 second acquisition deadline and preserves the active attempt", async () => {
  useTrainingStore.getState().hydrateLocalQueue([activeCard], true, 1);
  const attempt = useTrainingStore.getState().attempt;
  const request = vi.fn().mockResolvedValueOnce(pending("unknown")).mockImplementation(async () => receipt("unknown"));
  vi.stubGlobal("fetch", request);
  const settled = fetchAndInitializeQueue(false, options).catch(error => error);
  await advanceStudyTimers(16000);
  expect(await settled).toMatchObject({ message: expect.stringContaining("timed out") });
  expect(request.mock.calls.filter(call => String(call[0]).includes("/api/queue/window"))).toHaveLength(1);
  expect(request.mock.calls.length).toBeLessThanOrEqual(16);
  expect(useTrainingStore.getState().getCard().id).toBe(activeCard.id);
  expect(useTrainingStore.getState().attempt).toEqual(attempt);
});

it("aborting pending observation retains the active attempt and sends no server cancellation", async () => {
  useTrainingStore.getState().hydrateLocalQueue([activeCard], true, 1);
  const attempt = useTrainingStore.getState().attempt;
  const request = vi.fn().mockResolvedValueOnce(pending()).mockImplementation(async () => receipt("executing"));
  vi.stubGlobal("fetch", request);
  const controller = new AbortController();
  const settled = fetchAndInitializeQueue(false, { ...options, signal: controller.signal }).catch(error => error);
  await advanceStudyTimers(1100); controller.abort();
  await settled;
  expect(request).toHaveBeenCalledTimes(2);
  expect(request.mock.calls.every(call => call[1]?.method === undefined)).toBe(true);
  expect(useTrainingStore.getState().attempt).toEqual(attempt);
  expect(useTrainingStore.getState().getCard().id).toBe(activeCard.id);
});

it.each(["position_card_busy", "position_attempt_busy", "position_source_graph_busy", "position_owner_busy", "position_original_admission_unavailable"])("a completed %s hold leaves operation polling and permits bounded fresh head acquisition", async hold => {
  const request = vi.fn().mockResolvedValueOnce(pending())
    .mockResolvedValueOnce(receipt("complete", { response: { issued: false, position_study_hold: hold } }))
    .mockResolvedValueOnce(positionQueue({ ...busyPositionCard(), position_study_hold: hold })).mockResolvedValueOnce(positionQueue(issuedPositionCard()));
  vi.stubGlobal("fetch", request);
  const loading = fetchAndInitializeQueue(false, options);
  await advanceStudyTimers(2500); await loading;
  expect(request).toHaveBeenCalledTimes(4);
  expect(request.mock.calls.filter(call => String(call[0]).includes("/api/operations/"))).toHaveLength(1);
  expect(request.mock.calls.slice(2).every(call => call[1]?.headers === undefined)).toBe(true);
  expect(useTrainingStore.getState().attempt.attemptId).toBe("issued-attempt-42");
});

it.each(["failed", "blocked"])("terminal %s issuance preserves the active attempt with its actual error", async state => {
  useTrainingStore.getState().hydrateLocalQueue([activeCard], true, 1);
  const attempt = useTrainingStore.getState().attempt;
  const request = vi.fn().mockResolvedValueOnce(pending())
    .mockResolvedValueOnce(receipt(state, { error: { message: "source refused" }, last_error: { message: "source refused" } }));
  vi.stubGlobal("fetch", request);
  const settled = fetchAndInitializeQueue(false, options).catch(error => error);
  await advanceStudyTimers(1500);
  expect(await settled).toMatchObject({ message: expect.stringContaining("source refused") });
  expect(request).toHaveBeenCalledTimes(2);
  expect(useTrainingStore.getState().attempt).toEqual(attempt);
});

it.each([{ state: "executing" }, { operation_id: "../another-service", state: "executing" }])(
  "rejects malformed pending responses without dispatching a new operation", async body => {
    const request = vi.fn().mockResolvedValue(Response.json(body, { status: 202 }));
    vi.stubGlobal("fetch", request);
    const settled = fetchAndInitializeQueue(false, options).catch(error => error);
    await settleStudyStorageTasks();
    expect(await settled).toMatchObject({ message: expect.stringContaining("invalid pending") });
    expect(request).toHaveBeenCalledTimes(1);
  },
);

it("rejects a status receipt for a different operation", async () => {
  const request = vi.fn().mockResolvedValueOnce(pending())
    .mockResolvedValueOnce(Response.json({ operation_id: "other", state: "complete" }));
  vi.stubGlobal("fetch", request);
  const settled = fetchAndInitializeQueue(false, options).catch(error => error);
  await advanceStudyTimers(1500);
  expect(await settled).toMatchObject({ message: expect.stringContaining("original issuance") });
  expect(request).toHaveBeenCalledTimes(2);
});

it("the default deadline also cancels a nonsettling operation status request", async () => {
  useTrainingStore.getState().hydrateLocalQueue([activeCard], true, 1);
  const attempt = useTrainingStore.getState().attempt;
  const request = vi.fn().mockResolvedValueOnce(pending()).mockImplementation(() => new Promise<Response>(() => {}));
  vi.stubGlobal("fetch", request);
  const settled = fetchAndInitializeQueue(false, options).catch(error => error);
  await advanceStudyTimers(16000);
  expect(await settled).toMatchObject({ message: expect.stringContaining("timed out") });
  expect(request).toHaveBeenCalledTimes(2);
  expect(request.mock.calls[1][1]?.signal.aborted).toBe(true);
  expect(useTrainingStore.getState().attempt).toEqual(attempt);
});
