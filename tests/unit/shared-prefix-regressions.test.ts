import { describe, expect, it } from "vitest";

import { parsePgnImport } from "../../app/lib/pgn-import";

describe("hybrid canonical opening graph", () => {
  it("deduplicates identical complete prefixes globally", () => {
    const pgn = `[Event "Ruy Lopez"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 *

[Event "Ruy Lopez duplicate"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 *`;

    const parsed = parsePgnImport("shared.pgn", pgn, "white", 3);
    expect(parsed.cards).toHaveLength(2);
    expect(parsed.cards[0].moves).toEqual(["e4", "e5", "Nf3", "Nc6", "Bb5"]);
    expect(parsed.cards[0].userMoveTarget).toBe(3);
    expect(parsed.cards[1].moves).toEqual(["a6", "Ba4"]);
    expect(parsed.cards[1].userMoveTarget).toBe(1);
    expect(parsed.duplicateLines).toBe(2);
  });

  it("keeps route-specific full prefixes when branches diverge before the configured depth", () => {
    const pgn = `[Event "Ruy Lopez"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 *

[Event "Italian"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 *`;

    const parsed = parsePgnImport("branches.pgn", pgn, "white", 3);
    expect(parsed.cards.map((card) => card.moves)).toEqual([
      ["e4", "e5", "Nf3", "Nc6", "Bb5"],
      ["e4", "e5", "Nf3", "Nc6", "Bc4"],
    ]);
    expect(parsed.cards.every((card) => card.userMoveTarget === 3)).toBe(true);
  });

  it("black decision cards retain the opponent move as their cue", () => {
    const parsed = parsePgnImport(
      "black.pgn",
      `[Event "Black repertoire"]\n\n1. e4 c5 2. Nf3 d6 *`,
      "black",
      2,
    );

    expect(parsed.cards).toHaveLength(1);
    expect(parsed.cards[0].moves).toEqual(["e4", "c5", "Nf3", "d6"]);
    expect(parsed.cards[0].userMoveTarget).toBe(2);
  });

  it("materializes every move after the initial prefix as a one-decision descendant", () => {
    const parsed = parsePgnImport(
      "black.pgn",
      `[Event "Black repertoire"]\n\n1. e4 c6 2. d4 d5 3. e5 c5 4. c3 Nc6 *`,
      "black",
      2,
    );

    expect(parsed.cards.map((card) => [card.moves, card.userMoveTarget])).toEqual([
      [["e4", "c6", "d4", "d5"], 2],
      [["e5", "c5"], 1],
      [["c3", "Nc6"], 1],
    ]);
  });
});
