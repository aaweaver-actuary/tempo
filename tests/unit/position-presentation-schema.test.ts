import { describe, expect, it } from "vitest";
import type { PracticeCard } from "../../app/domain/cards";
import { adaptPositionPresentation } from "../../app/domain/adapters/position-presentation-adapter";
import {
  decodePositionPresentation,
  positionPresentationSchema,
} from "../../app/domain/position-presentation";

const startFen = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4";
const whiteKey = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq -";
const blackKey = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq -";
const rawPlan = () => ({
  version: 1,
  plan_id: "a".repeat(64),
  original_card_id: "backend-card-17",
  original_card_revision: 3,
  queue_entry_id: 44,
  attempt_id: "attempt-99",
  owner_epoch: "epoch-4",
  route_id: "italian-g",
  target_ply: 6,
  starting_fen: startFen,
  moves_uci: ["c2c3"],
  decision_slots: [{ move_offset: 0, position_key: whiteKey, expected_uci: "c2c3" }],
  source_digest: "source-identity-1",
  local_day: "2026-11-02",
  skipped_prefix_proof_ids: ["proof-1", "proof-2"],
});
const originalCard = (overrides: Partial<PracticeCard> = {}): PracticeCard => ({
  id: "queue-44" as PracticeCard["id"],
  backendId: "backend-card-17" as PracticeCard["backendId"],
  queueEntryId: 44 as PracticeCard["queueEntryId"],
  revision: 3,
  kind: "opening",
  title: "Italian",
  subtitle: "Opening",
  startingFen: startFen as PracticeCard["startingFen"],
  moves: ["e4" as PracticeCard["moves"][number]],
  userMoveTarget: 1,
  ...overrides,
});

describe("position presentation wire decoder", () => {
  it("decodes the strict issued envelope and freezes nested display values", () => {
    const decoded = decodePositionPresentation(rawPlan());
    expect(decoded.original_card_id).toBe("backend-card-17");
    expect(decoded.queue_entry_id).toBe(44);
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(Object.isFrozen(decoded.moves_uci)).toBe(true);
    expect(Object.isFrozen(decoded.decision_slots[0])).toBe(true);
    expect(Object.isFrozen(decoded.skipped_prefix_proof_ids)).toBe(true);
  });

  it("rejects unknown fields, malformed digest, empty line, and invalid slot moves", () => {
    expect(positionPresentationSchema.safeParse({ ...rawPlan(), due: true }).success).toBe(false);
    expect(positionPresentationSchema.safeParse({ ...rawPlan(), plan_id: "digest" }).success).toBe(false);
    expect(positionPresentationSchema.safeParse({ ...rawPlan(), moves_uci: [] }).success).toBe(false);
    expect(
      positionPresentationSchema.safeParse({
        ...rawPlan(),
        decision_slots: [{ move_offset: 0, position_key: whiteKey, expected_uci: "e2e4" }],
      }).success,
    ).toBe(false);
  });

  it("accepts the maximum 20 learner decisions and rejects more than 20 or 40 moves", () => {
    const slots = Array.from({ length: 20 }, (_, move_offset) => ({
      move_offset,
      position_key: whiteKey,
      expected_uci: "c2c3",
    }));
    const moves = Array.from({ length: 20 }, () => "c2c3");
    expect(positionPresentationSchema.safeParse({ ...rawPlan(), moves_uci: moves, decision_slots: slots }).success).toBe(true);
    expect(positionPresentationSchema.safeParse({ ...rawPlan(), moves_uci: [...moves, ...moves, "c2c3"], decision_slots: slots }).success).toBe(false);
    expect(positionPresentationSchema.safeParse({ ...rawPlan(), moves_uci: moves, decision_slots: [...slots, { ...slots[0], move_offset: 20 }] }).success).toBe(false);
  });

  it("requires decision offsets to be unique and ascending", () => {
    const moves = ["c2c3", "d2d3"];
    const slots = [
      { move_offset: 1, position_key: whiteKey, expected_uci: "d2d3" },
      { move_offset: 0, position_key: whiteKey, expected_uci: "c2c3" },
    ];
    expect(positionPresentationSchema.safeParse({ ...rawPlan(), moves_uci: moves, decision_slots: slots }).success).toBe(false);
    expect(positionPresentationSchema.safeParse({ ...rawPlan(), moves_uci: ["c2c3", "c2c3"], decision_slots: [slots[0], { ...slots[0], move_offset: 1 }] }).success).toBe(false);
  });
});

describe("position presentation display adapter", () => {
  it("binds the issued plan to its original card and exposes opponent-first cue", () => {
    const plan = rawPlan();
    plan.moves_uci = ["e2e4", "c7c5"];
    plan.decision_slots = [{ move_offset: 1, position_key: blackKey, expected_uci: "c7c5" }];
    const result = adaptPositionPresentation(plan, originalCard());
    expect(result.orientation).toBe("black");
    expect(result.learnerColor).toBe("black");
    expect(result.opponentCueMoves).toEqual(["e2e4"]);
    expect(Object.isFrozen(result.opponentCueMoves)).toBe(true);
  });

  it("rejects a backend card or queue identity mismatch", () => {
    expect(() => adaptPositionPresentation(rawPlan(), originalCard({ backendId: "other" as PracticeCard["backendId"] })))
      .toThrow("position_presentation_original_card_mismatch");
    expect(() => adaptPositionPresentation(rawPlan(), originalCard({ queueEntryId: 45 as PracticeCard["queueEntryId"] })))
      .toThrow("position_presentation_original_card_mismatch");
  });
});
