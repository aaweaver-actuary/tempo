import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AnalyzeOnLichessButton from "@/app/components/buttons/AnalyzeOnLichessButton";
import { STANDARD_FEN } from "../../app/const";
import { canonicalizeMoves } from "../../app/utils/canonical-line";
import { mapQueueCardToPracticeCard, mapPackagedPuzzleToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";
import { adaptEngineMoves, adaptExplorerMoves } from "../../app/domain/adapters/analysis-adapters";

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

  it("engine candidates validate legal UCI and PV while preserving evaluations", () => {
    const candidates = adaptEngineMoves(STANDARD_FEN, [
      { uci: " E2E4 ", san: "incorrect", cp: 30, score: "+0.30", probability: 0.6, pv: ["e2e4", "e7e5", "0000"] },
      { uci: "e2e5", san: "e5" }, { uci: "0000", san: "--" },
    ]);
    expect(candidates).toEqual([{ uci: "e2e4", san: "e4", cp: 30, score: "+0.30", probability: 0.6, pv: ["e2e4", "e7e5"] }]);
    expect(adaptExplorerMoves(STANDARD_FEN, [{ uci: "e2e4", san: "bad", white: 4, draws: 3, black: 2 }])[0]).toMatchObject({ san: "e4", white: 4 });
    expect(adaptExplorerMoves(STANDARD_FEN, [{ uci: "e2e4", white: -1, draws: 0, black: 0 }])).toEqual([]);
  });

  it("queue mapping preserves identity and guided state and rejects illegal persisted lines", () => {
    const record = { id: "card-id", queue_entry_id: 42, cycle: 2, attempt_state: "reinforcement" as const, attempt_failed: true, start_fen: STANDARD_FEN, moves: ["d2d4", "g8f6"], content_type: "opening" as const, repertoire_name: "Gambits", repertoire_source: "PGN" as const, trained_color: "black" as const, revision: 3, repertoire_id: "repertoire-id" };
    expect(mapQueueCardToPracticeCard(record)).toMatchObject({ backendId: "card-id", queueEntryId: 42, queueCycle: 2, queueAttemptState: "reinforcement", attemptFailed: true, moves: ["d4", "Nf6"], orientation: "black", revision: 3, repertoireId: "repertoire-id" });
    expect(() => mapQueueCardToPracticeCard({ ...record, moves: ["d2d4", "0000"] })).toThrow(/null move/);
    expect(() => mapQueueCardToPracticeCard({ ...record, start_fen: "bad FEN" })).toThrow(/FEN/);
    expect(mapPackagedPuzzleToPracticeCard({ DeckId: "deck", DeckPosition: 1, PuzzleId: "bad", FEN: "bad FEN", Moves: "e2e4", Rating: 1000 })).toBeNull();
  });
});
