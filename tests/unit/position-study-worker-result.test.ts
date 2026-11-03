// @vitest-environment node
import { Chess } from "chess.js";
import { describe, expect, it } from "vitest";
import { computeEssentialStudyTask } from "../../app/lib/essential-study-computation";
import { decodeStudyResult } from "../../app/lib/study-result-decoder";
import { canonicalFenKey } from "../../app/utils/canonical-line";

// Synthetic legal Italian route: the worker must display the server-issued
// suffix while retaining the original card, queue entry, revision and attempt.
function issuedCard(entry = 42, start = 2, color = "white") {
  const moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5"];
  const board = new Chess();
  const originalFen = board.fen();
  moves.slice(0, start).forEach(move => board.move(move));
  const startingFen = board.fen();
  const decisions: { move_offset: number; position_key: string; expected_uci: string }[] = [];
  const manifestDecisions: { decision_id: string; decision_index: number; move_offset: number; fen: string; expected_uci: string }[] = [];
  moves.slice(start).forEach((move, move_offset) => {
    if (board.turn() === (color === "black" ? "b" : "w")) {
      decisions.push({ move_offset, position_key: canonicalFenKey(board.fen()), expected_uci: move });
      manifestDecisions.push({ decision_id: String(decisions.length).repeat(64), decision_index: decisions.length - 1,
        move_offset, fen: board.fen(), expected_uci: move });
    }
    board.move(move);
  });
  return {
    id: "italian", queue_entry_id: entry, revision: 3, start_fen: originalFen, moves,
    content_type: "opening", repertoire_name: "Italian", repertoire_source: "synthetic", trained_color: color,
    opening_decision_manifest: {
      manifest_version: 1, policy_version: 1, manifest_id: "f".repeat(64), presentation_snapshot_id: 1,
      repertoire_id: "rep-italian", card_id: "italian", card_revision: 3, trained_color: color, decisions: manifestDecisions,
    },
    position_study_mode: "position",
    position_presentation: {
      version: 1, plan_id: "a".repeat(64), original_card_id: "italian", original_card_revision: 3,
      queue_entry_id: entry, attempt_id: `issued-attempt-${entry}`, owner_epoch: "epoch-1", route_id: "authored-route",
      target_ply: start, starting_fen: startingFen, moves_uci: moves.slice(start), decision_slots: decisions,
      source_digest: "source-1", local_day: "2026-11-02", skipped_prefix_proof_ids: start ? ["clean-proof"] : [],
    },
  };
}

const queueTask = (cards: unknown[]) => ({ kind: "queue" as const, payload: { cards } });

function computeQueueCards(task: ReturnType<typeof queueTask>) {
  const result = computeEssentialStudyTask(task);
  if (!Array.isArray(result)) throw new Error("Queue computation must return an array of cards");
  return result;
}

describe("position Study worker result boundary", () => {
  it("actual_position_queue_adapter_output_survives_worker_clone_with_immutable_issued_plan_and_original_identities", () => {
    const raw = [issuedCard(42, 4), issuedCard(43, 2, "black")];
    const task = queueTask(raw);
    const computed = computeQueueCards(task);
    const cloned = structuredClone(computed);
    expect(Object.isFrozen(cloned[0].positionPresentation)).toBe(false);
    const decoded = decodeStudyResult(task, cloned);

    expect(decoded).toEqual(computed);
    expect(decoded.map(card => card.moves)).toEqual([["Bc4", "Bc5"], ["Nf3", "Nc6", "Bc4", "Bc5"]]);
    decoded.forEach((card, index) => {
      expect(card).toMatchObject({ id: `queue-${raw[index].queue_entry_id}`, backendId: "italian",
        queueEntryId: raw[index].queue_entry_id, revision: 3, positionStudyMode: "position",
        startingFen: raw[index].position_presentation.starting_fen,
        orientation: index === 0 ? "white" : "black" });
      const plan = card.positionPresentation!;
      expect(plan).toEqual(raw[index].position_presentation);
      expect(Object.isFrozen(plan)).toBe(true);
      expect(Object.isFrozen(plan.moves_uci)).toBe(true);
      expect(Object.isFrozen(plan.decision_slots)).toBe(true);
      expect(plan.decision_slots.every(Object.isFrozen)).toBe(true);
      expect(Object.isFrozen(plan.skipped_prefix_proof_ids)).toBe(true);
      expect(Reflect.set(plan, "attempt_id", "rewritten")).toBe(false);
      expect(plan.attempt_id).toBe(`issued-attempt-${raw[index].queue_entry_id}`);
    });
  });

  it("held_position_head_survives_worker_clone_without_promoting_next_entry_or_restoring_legacy_moves", () => {
    const held = { ...issuedCard(), position_study_hold: "position_source_unavailable" };
    const task = queueTask([held, issuedCard(43)]);
    const computed = computeQueueCards(task);
    const decoded = decodeStudyResult(task, structuredClone(computed));
    expect(decoded).toEqual(computed);
    expect(decoded.map(card => card.queueEntryId)).toEqual([42, 43]);
    expect(decoded[0]).toMatchObject({ backendId: "italian", revision: 3, positionStudyMode: "position",
      positionStudyHold: "position_source_unavailable", moves: [], userMoveTarget: 0 });
    expect(decoded[0].positionPresentation).toBeUndefined();
    expect(decoded[1].positionPresentation?.attempt_id).toBe("issued-attempt-43");
  });

  it("invalid_issued_plan_is_held_by_actual_adapter_and_remains_held_across_worker_boundary", () => {
    const raw = issuedCard();
    raw.position_presentation.original_card_id = "different-card";
    const task = queueTask([raw]);
    const computed = computeQueueCards(task);
    const [decoded] = decodeStudyResult(task, structuredClone(computed));
    expect(decoded).toMatchObject({ backendId: "italian", queueEntryId: 42, positionStudyMode: "position",
      positionStudyHold: expect.stringContaining("unavailable"), moves: [], userMoveTarget: 0 });
    expect(decoded.positionPresentation).toBeUndefined();
  });

  it("worker_decoder_rejects_malformed_position_plans_and_unexpected_card_or_plan_fields", () => {
    const task = queueTask([issuedCard()]);
    const card = computeQueueCards(task)[0];
    const plan = card.positionPresentation!;
    for (const malformed of [
      { ...plan, plan_id: "invalid" },
      { ...plan, extra_authority: true },
      { ...plan, moves_uci: ["not-uci"] },
      { ...plan, decision_slots: [{ ...plan.decision_slots[0], move_offset: 99 }] },
      { ...plan, decision_slots: [{ ...plan.decision_slots[0], expected_uci: "a2a3" }] },
      { ...plan, decision_slots: [{ ...plan.decision_slots[0], unexpected: true }] },
    ]) {
      expect(() => decodeStudyResult(task, [{ ...card, positionPresentation: malformed }])).toThrow();
    }
    expect(() => decodeStudyResult(task, [{ ...card, unexpected: true }])).toThrow();
    expect(() => decodeStudyResult(task, [{ ...card, positionStudyMode: "legacy" }])).toThrow();
    expect(() => decodeStudyResult(task, [{ ...card, positionStudyHold: 42 }])).toThrow();
  });

  it("legacy_queue_cards_keep_existing_worker_decoding_behavior", () => {
    const task = queueTask([{ id: "legacy", queue_entry_id: 7, start_fen: new Chess().fen(), moves: ["e2e4"],
      content_type: "opening", repertoire_name: "Legacy", repertoire_source: "synthetic" }]);
    const computed = computeQueueCards(task);
    const [decoded] = decodeStudyResult(task, structuredClone(computed));
    expect(decoded).toEqual(computed[0]);
    expect(decoded.moves).toEqual(["e4"]);
    expect(decoded.positionStudyMode).toBeUndefined();
    expect(decoded.positionPresentation).toBeUndefined();
    expect(decoded.positionStudyHold).toBeUndefined();
  });

  it("position_worker_result_requires_consistent_mode_plan_hold_and_original_card_binding", () => {
    const task = queueTask([issuedCard()]);
    const card = computeQueueCards(task)[0];
    for (const inconsistent of [
      { ...card, positionStudyMode: undefined },
      { ...card, positionStudyHold: "unavailable" },
      { ...card, positionPresentation: undefined },
      { ...card, positionPresentation: undefined, positionStudyHold: "" },
      { ...card, positionStudyMode: undefined, positionPresentation: undefined, positionStudyHold: "unavailable" },
      { ...card, backendId: "different-card" },
      { ...card, queueEntryId: 99 },
      { ...card, revision: 4 },
    ]) {
      expect(() => decodeStudyResult(task, [inconsistent])).toThrow();
    }
  });
});
