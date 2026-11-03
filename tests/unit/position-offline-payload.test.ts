import { describe, expect, it } from "vitest";
import {
  canReplayPositionStudyOffline,
  decodePositionOfflinePlan,
  getPositionStudyOfflineHold,
  roundTripPositionOfflinePlan,
  sealPositionOfflinePlan,
} from "../../app/domain/position-offline-plan";

const plan = () => ({
  version: 1,
  plan_id: "a".repeat(64),
  original_card_id: "card-17",
  original_card_revision: 3,
  queue_entry_id: 44,
  attempt_id: "attempt-99",
  owner_epoch: "epoch-4",
  route_id: "route-1",
  target_ply: 6,
  starting_fen: "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
  moves_uci: ["c2c3", "c7c5"],
  decision_slots: [{ move_offset: 0, position_key: "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq -", expected_uci: "c2c3" }],
  source_digest: "source-identity-1",
  local_day: "2026-11-02",
  skipped_prefix_proof_ids: ["proof-1"],
});

describe("sealed offline position payload", () => {
  it("roundtrips to stable immutable bytes with original queue, attempt, route and exact suffix", () => {
    const raw = { version: 1, position_study_mode: "position", position_presentation: plan() };
    const first = roundTripPositionOfflinePlan(raw);
    const duplicate = roundTripPositionOfflinePlan(first);
    expect(JSON.stringify(duplicate)).toBe(JSON.stringify(first));
    expect(first.position_presentation.queue_entry_id).toBe(44);
    expect(first.position_presentation.original_card_id).toBe("card-17");
    expect(first.position_presentation.original_card_revision).toBe(3);
    expect(first.position_presentation.attempt_id).toBe("attempt-99");
    expect(first.position_presentation.moves_uci).toEqual(["c2c3", "c7c5"]);
    expect(Object.isFrozen(first)).toBe(true);
  });

  it("rejects changed FEN, decision identity, card identity, and unknown envelope versions", () => {
    const sealed = sealPositionOfflinePlan(plan());
    expect(() => decodePositionOfflinePlan({ ...sealed, version: 2 })).toThrow();
    expect(getPositionStudyOfflineHold({ positionStudyMode: "position", sealedPositionPlan: sealed,
      positionPresentation: { ...plan(), starting_fen: "8/8/8/8/8/8/8/K6k w - - 0 1" } }))
      .toBe("position_plan_identity_mismatch");
    expect(getPositionStudyOfflineHold({ positionStudyMode: "position", sealedPositionPlan: sealed,
      positionPresentation: { ...plan(), decision_slots: [{ ...plan().decision_slots[0], expected_uci: "e2e4" }] } }))
      .toBe("position_plan_invalid");
    expect(getPositionStudyOfflineHold({ positionStudyMode: "position", sealedPositionPlan: sealed,
      originalCardId: "different-card", originalQueueEntryId: 44, originalCardRevision: 3,
      expectedAttemptId: "attempt-99" })).toBe("position_plan_identity_mismatch");
    expect(getPositionStudyOfflineHold({ positionStudyMode: "position", sealedPositionPlan: sealed,
      originalCardId: "card-17", originalQueueEntryId: 44, originalCardRevision: 3,
      expectedAttemptId: "different-attempt" })).toBe("position_plan_identity_mismatch");
  });

  it("keeps legacy payloads on their existing offline semantics and holds position mode online-only", () => {
    expect(canReplayPositionStudyOffline({})).toBe(true);
    expect(getPositionStudyOfflineHold({ positionStudyMode: "position" })).toBe("position_plan_missing");
    const sealed = sealPositionOfflinePlan(plan());
    expect(canReplayPositionStudyOffline({ positionStudyMode: "position", sealedPositionPlan: sealed,
      positionPresentation: plan(), originalCardId: "card-17", originalQueueEntryId: 44,
      originalCardRevision: 3, expectedAttemptId: "attempt-99" })).toBe(false);
    expect(getPositionStudyOfflineHold({ positionStudyMode: "position", positionStudyHold: "expired" }))
      .toBe("position_plan_owner_unproven");
  });
});
