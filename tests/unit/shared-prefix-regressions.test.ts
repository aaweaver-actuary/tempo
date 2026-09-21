import { describe, expect, it } from "vitest";

import { parsePgnImport } from "../../app/lib/pgn-import";

describe("canonical opening decision cards", () => {
  it("shared opening prefixes appear once and branches begin at divergence", () => {
    const pgn = `[Event "Ruy Lopez"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 *

[Event "Italian"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 *`;

    const parsed = parsePgnImport("shared.pgn", pgn, "white", 3);
    expect(parsed.cards).toHaveLength(4);
    expect(parsed.cards.filter((card) => card.moves.join(" ") === "e4")).toHaveLength(1);
    expect(parsed.cards.filter((card) => card.moves.join(" ") === "e5 Nf3")).toHaveLength(1);
    expect(
      parsed.cards.map((card) => card.moves.at(-1)).filter((move) => move === "Bb5" || move === "Bc4"),
    ).toHaveLength(2);
    expect(parsed.cards.every((card) => card.userMoveTarget === 1)).toBe(true);
  });

  it("black decision cards retain the opponent move as their cue", () => {
    const parsed = parsePgnImport(
      "black.pgn",
      `[Event "Black repertoire"]\n\n1. e4 c5 2. Nf3 d6 *`,
      "black",
      2,
    );

    expect(parsed.cards.map((card) => card.moves)).toEqual([
      ["e4", "c5"],
      ["Nf3", "d6"],
    ]);
  });
});
