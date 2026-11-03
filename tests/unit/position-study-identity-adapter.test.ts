// @vitest-environment node
import { Chess } from "chess.js";
import { describe, expect, it } from "vitest";
import { computeEssentialStudyTask } from "../../app/lib/essential-study-computation";
import { decodeStudyResult } from "../../app/lib/study-result-decoder";
import { canonicalFenKey } from "../../app/utils/canonical-line";

// Reproduce the full Italian browser response: manifests retain FEN-spec EP
// squares, while issued position keys include only legally available EP.
function issuedCard(
  startFen = new Chess().fen(),
  moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "f3g5", "d7d5", "e4d5"],
) {
  const board = new Chess(startFen);
  const trainedColor = board.turn() === "w" ? "white" : "black";
  const learnerTurn = board.turn();
  let manifestFen = startFen;
  const decisions: { decision_id: string; decision_index: number; move_offset: number; fen: string; expected_uci: string }[] = [];
  const slots: { move_offset: number; position_key: string; expected_uci: string }[] = [];
  moves.forEach((move, move_offset) => {
    if (board.turn() === learnerTurn) {
      decisions.push({ decision_id: String(decisions.length + 1).repeat(64), decision_index: decisions.length,
        move_offset, fen: manifestFen, expected_uci: move });
      slots.push({ move_offset, position_key: canonicalFenKey(board.fen()), expected_uci: move });
    }
    const played = board.move(move);
    const fields = board.fen().split(" ");
    // The actual server manifest uses FEN-spec EP after every double pawn move,
    // including when chess.js has already discarded that irrelevant square.
    if (played.flags.includes("b")) fields[3] = `${move[0]}${(Number(move[1]) + Number(move[3])) / 2}`;
    manifestFen = fields.join(" ");
  });
  return {
    id: "ep-card", queue_entry_id: 42, revision: 3, start_fen: startFen, moves,
    content_type: "opening", repertoire_name: "Italian", repertoire_source: "synthetic", trained_color: trainedColor,
    opening_decision_manifest: {
      manifest_version: 1, policy_version: 1, manifest_id: "f".repeat(64), presentation_snapshot_id: 1,
      repertoire_id: "rep-ep", card_id: "ep-card", card_revision: 3, trained_color: trainedColor, decisions,
    },
    position_study_mode: "position",
    position_presentation: {
      version: 1, plan_id: "a".repeat(64), original_card_id: "ep-card", original_card_revision: 3,
      queue_entry_id: 42, attempt_id: "issued-ep-attempt", owner_epoch: "epoch-1", route_id: "authored-route",
      target_ply: 0, starting_fen: startFen, moves_uci: moves, decision_slots: slots,
      source_digest: "source-1", local_day: "2026-11-02", skipped_prefix_proof_ids: [],
    },
  };
}

function acrossWorkerBoundary(raw: unknown) {
  const task = { kind: "queue" as const, payload: { cards: [raw] } };
  return decodeStudyResult(task, structuredClone(computeEssentialStudyTask(task)))[0];
}

describe("position Study legal en-passant adapter identity", () => {
  it("full_italian_force_ep_manifest_matches_issued_legal_keys_across_worker_without_rewriting_authority", () => {
    const raw = issuedCard();
    const originalBytes = JSON.stringify(raw);
    expect(raw.opening_decision_manifest.decisions[1].fen).toContain(" e6 ");
    expect(raw.opening_decision_manifest.decisions[4].fen).toContain(" d6 ");
    expect(raw.position_presentation.decision_slots.every(slot => slot.position_key.endsWith(" -"))).toBe(true);

    const card = acrossWorkerBoundary(raw);
    expect(card.positionStudyHold).toBeUndefined();
    expect(card.positionPresentation).toEqual(raw.position_presentation);
    expect(card.openingDecisionManifest).toEqual(raw.opening_decision_manifest);
    expect(card).toMatchObject({ id: "queue-42", backendId: "ep-card", queueEntryId: 42, revision: 3,
      positionStudyMode: "position", startingFen: raw.position_presentation.starting_fen,
      moves: ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "Ng5", "d5", "exd5"], userMoveTarget: 5 });
    expect(Object.isFrozen(card.positionPresentation)).toBe(true);
    expect(JSON.stringify(raw)).toBe(originalBytes);
  });

  it.each([
    ["pinned", "k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "e1f1"],
    ["irrelevant", "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1", "e7e5"],
  ])("%s_ep_manifest_uses_legal_identity_and_preserves_original_fen", (_name, fen, move) => {
    const raw = issuedCard(fen, [move]);
    expect(raw.position_presentation.decision_slots[0].position_key).toBe(canonicalFenKey(new Chess(fen).fen()));
    expect(raw.position_presentation.decision_slots[0].position_key).not.toBe(canonicalFenKey(fen));
    const card = acrossWorkerBoundary(raw);
    expect(card.positionStudyHold).toBeUndefined();
    expect(card.positionPresentation).toEqual(raw.position_presentation);
    expect(card.startingFen).toBe(fen);
    expect(card.openingDecisionManifest?.decisions[0]).toEqual(raw.opening_decision_manifest.decisions[0]);
  });

  it("legally_available_ep_remains_in_the_authorized_position_key", () => {
    const raw = issuedCard("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", ["e5d6"]);
    const card = acrossWorkerBoundary(raw);
    expect(card.positionStudyHold).toBeUndefined();
    expect(card.positionPresentation?.decision_slots[0].position_key).toBe("4k3/8/8/3pP3/8/8/8/4K3 w - d6");
    expect(card.positionPresentation).toEqual(raw.position_presentation);
    expect(card.moves).toEqual(["exd6"]);
  });

  it.each([false, true])("removing_legal_ep_from_issued_key_is_held_even_when_manifest_matches=%s", changeManifest => {
    const raw = issuedCard("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", ["e1d1"]);
    raw.position_presentation.decision_slots[0].position_key = raw.position_presentation.decision_slots[0].position_key.replace(" d6", " -");
    if (changeManifest) raw.opening_decision_manifest.decisions[0].fen = raw.opening_decision_manifest.decisions[0].fen.replace(" d6 ", " - ");
    const card = acrossWorkerBoundary(raw);
    expect(card).toMatchObject({ backendId: "ep-card", queueEntryId: 42, positionStudyMode: "position",
      positionStudyHold: expect.stringContaining("unavailable"), moves: [], userMoveTarget: 0 });
    expect(card.positionPresentation).toBeUndefined();
    expect(card.openingDecisionManifest).toEqual(raw.opening_decision_manifest);
  });
});
