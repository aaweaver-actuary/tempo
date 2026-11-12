import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AnalyzeOnLichessButton from "@/app/components/buttons/AnalyzeOnLichessButton";
import { STANDARD_FEN } from "../../app/const";
import { canonicalizeMoves } from "../../app/utils/canonical-line";
import { mapQueueCardToPracticeCard, mapPackagedPuzzleToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";

describe("validated domain boundaries", () => {
  it("application aliases resolve in the regular frontend suite", () => {
    render(<AnalyzeOnLichessButton moves={[]} fen={STANDARD_FEN} onClick={() => undefined} />);
    expect(screen.getByRole("link").getAttribute("href")).toContain("lichess.org/analysis");
  });

  it("null and malformed moves remain raw diagnostics while valid moves are canonical", () => {
    expect(canonicalizeMoves(STANDARD_FEN, ["e4", "e7e5", "0000", "Nf3"])).toMatchObject({
      moves: ["e2e4", "e7e5"], diagnostics: [{ ply: 2, move: "0000", kind: "null" }],
    });
    expect(canonicalizeMoves(STANDARD_FEN, ["e4", "garbage"])).toMatchObject({
      moves: ["e2e4"], diagnostics: [{ move: "garbage", kind: "invalid" }],
    });
    expect(canonicalizeMoves("bad FEN", []).diagnostics[0].kind).toBe("invalid");
  });

  it("queue mapping preserves identity and guided state and rejects illegal persisted lines", () => {
    const record = { id: "card-id", queue_entry_id: 42, cycle: 2, attempt_state: "reinforcement" as const, attempt_failed: true, start_fen: STANDARD_FEN, moves: ["d2d4", "g8f6"], content_type: "opening" as const, repertoire_name: "Gambits", repertoire_source: "PGN" as const, trained_color: "black" as const, revision: 3, repertoire_id: "repertoire-id" };
    expect(mapQueueCardToPracticeCard(record)).toMatchObject({ backendId: "card-id", queueEntryId: 42, queueCycle: 2, queueAttemptState: "reinforcement", attemptFailed: true, moves: ["d4", "Nf6"], orientation: "black", revision: 3, repertoireId: "repertoire-id" });
    expect(() => mapQueueCardToPracticeCard({ ...record, moves: ["d2d4", "0000"] })).toThrow(/null move/);
    expect(() => mapQueueCardToPracticeCard({ ...record, start_fen: "bad FEN" })).toThrow(/FEN/);
    expect(mapPackagedPuzzleToPracticeCard({ DeckId: "deck", DeckPosition: 1, PuzzleId: "bad", FEN: "bad FEN", Moves: "e2e4", Rating: 1000 })).toBeNull();
  });

  it("real-game priority reason crosses the strict queue schema into a practice card", () => {
    const mapped = mapQueueCardToPracticeCard({
      id: "game-priority-card",
      queue_entry_id: 43,
      start_fen: STANDARD_FEN,
      moves: ["e2e4"],
      content_type: "opening",
      repertoire_name: "Main",
      repertoire_source: "PGN",
      gameplay_priority_reason: "Priority review · missed in a recent game",
      has_study_review: 1,
      study_review_count: 3,
    });
    expect(mapped.priorityReason).toBe("Priority review · missed in a recent game");
    expect(mapped.hasPriorStudyReview).toBe(true);
    expect(mapped.priorStudyReviewCount).toBe(3);
    expect(mapQueueCardToPracticeCard({
      id: "new-game-priority-card", queue_entry_id: 45, start_fen: STANDARD_FEN,
      moves: ["e2e4"], content_type: "opening", repertoire_name: "Main",
      repertoire_source: "PGN", gameplay_priority_reason: "Priority review · missed in a recent game",
      has_study_review: 0,
    }).hasPriorStudyReview).toBe(false);
    expect(() => mapQueueCardToPracticeCard({
      id: "game-priority-card",
      queue_entry_id: 43,
      start_fen: STANDARD_FEN,
      moves: ["e2e4"],
      content_type: "opening",
      repertoire_name: "Main",
      repertoire_source: "PGN",
      gameplay_priority_reason: { unexpected: true },
    })).toThrow();
  });

  it("canonical route provenance remains a validated boolean in queue transport", () => {
    const record = {
      id: "authored-card", queue_entry_id: 46, start_fen: STANDARD_FEN,
      moves: ["e2e4"], content_type: "opening", repertoire_name: "Italian",
      repertoire_source: "PGN",
    };
    for (const provenance of [false, true, 0, 1])
      expect(mapQueueCardToPracticeCard({ ...record, canonical_route_source: provenance }).backendId).toBe("authored-card");
    expect(() => mapQueueCardToPracticeCard({ ...record, canonical_route_source: 2 })).toThrow();
    expect(() => mapQueueCardToPracticeCard({ ...record, canonical_route_source: "true" })).toThrow();
  });

  it("issue 13 approved defensive queue card retains its candidate and minimal move-free prompt", () => {
    const mapped = mapQueueCardToPracticeCard({
      id: "defense-card", queue_entry_id: 44,
      start_fen: "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1",
      moves: [], content_type: "defense", source_ref: "candidate-identity",
      repertoire_name: "Defensive tactics", repertoire_source: "Your analyzed games",
      trained_color: "white", revision: 2,
    });
    expect(mapped).toMatchObject({
      kind: "defense", backendId: "defense-card", queueEntryId: 44,
      defenseCandidateId: "candidate-identity", moves: [], userMoveTarget: 1,
      orientation: "white", revision: 2,
    });
    expect(mapped.sourceUrl).toBeUndefined();
  });
});
