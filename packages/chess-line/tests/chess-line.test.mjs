import assert from "node:assert/strict";
import { test } from "node:test";
import { canonicalFenKey, canonicalizeMoves, sanForUci } from "@tempo/chess-line";
const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

test("null and malformed moves remain raw diagnostics while valid moves are canonical", () => {
  assert.deepEqual(canonicalizeMoves(startingFen, ["e4", "e7e5", "0000", "Nf3"]).moves, ["e2e4", "e7e5"]);
  assert.deepEqual(canonicalizeMoves(startingFen, ["e4", "garbage"]).diagnostics.map(({ kind, move }) => ({ kind, move })), [{ kind: "invalid", move: "garbage" }]);
  assert.equal(canonicalizeMoves("bad FEN", []).diagnostics[0].kind, "invalid");
});
test("canonical chess-line package preserves SAN UCI promotion and FEN identity", () => {
  assert.deepEqual(canonicalizeMoves(startingFen, [" E2E4 ", "e5", "Nf3"]).moves, ["e2e4", "e7e5", "g1f3"]);
  assert.equal(canonicalFenKey("  " + startingFen + "  "), startingFen.split(" ").slice(0, 4).join(" "));
  assert.equal(sanForUci(startingFen, "e2e4"), "e4");
  assert.equal(sanForUci(startingFen, "e2e5"), undefined);
  assert.deepEqual(canonicalizeMoves("7k/P7/8/8/8/8/8/7K w - - 0 1", ["a7a8q"]).moves, ["a7a8q"]);
});
test("canonical chess-line package stops at null illegal and invalid tokens", () => {
  for (const token of ["0000", "--", "Z0"]) assert.equal(canonicalizeMoves(startingFen, [token, "e4"]).diagnostics[0].kind, "null");
  assert.equal(canonicalizeMoves(startingFen, ["e2e5"]).diagnostics[0].kind, "illegal");
  assert.deepEqual(canonicalizeMoves(startingFen, ["e4", "e5", "e1g1"]).moves, ["e2e4", "e7e5"]);
});
test("installed chess-line package rejects private and unavailable exports", async () => {
  await assert.rejects(import("@tempo/chess-line/dist/index.js"), { code: "ERR_PACKAGE_PATH_NOT_EXPORTED" });
  const artifact = await import("@tempo/chess-line");
  assert.deepEqual(Object.keys(artifact).sort(), ["canonicalFenKey", "canonicalizeMoves", "sanForUci"]);
});
