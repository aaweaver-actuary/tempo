import { afterEach, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { splitTacticMoves, tacticCardIdentity } from "../../app/lib/tactic-card-identity";
import baseline from "../../packages/chess-core/tests/baseline.json";

afterEach(() => vi.unstubAllGlobals());

it("matches the shared Python persisted card identity fixtures", async () => {
  vi.stubGlobal("crypto", webcrypto);
  for (const entry of baseline) expect(await tacticCardIdentity(entry.fen, entry.moves)).toBe(entry.cardId);
});

it.each([
  ["A\u0085w\u001c-\u2000- 0 1", ["\u0085e4\u001c", "\ufeffc5\ufeff"], "c9949e8b89d17a45360eee1533641f81b572a7626cd2fe993a9a496517657a74"],
  ["A\ufeffw - - 0 1", [" e4 "], "55054119bd5ed0a34ff2715bae76fc36f917eb2bfc81775375158ca6ad572e13"],
  ["", [], "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b"],
])("matches Python whitespace for %j", async (fen, moves, expected) => {
  vi.stubGlobal("crypto", webcrypto);
  expect(await tacticCardIdentity(fen, moves)).toBe(expected);
});

it("fails closed when WebCrypto is unavailable", async () => {
  vi.stubGlobal("crypto", {});
  await expect(tacticCardIdentity("fen", ["e2e4"])).rejects.toThrow("unavailable");
});

it("matches the producer's post-setup FEN and UCI solution for a packaged puzzle", async () => {
  vi.stubGlobal("crypto", webcrypto);
  const { Chess } = await import("chess.js");
  const board = new Chess("q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17");
  board.move({ from: "e8", to: "d7" });
  expect(await tacticCardIdentity(board.fen(), ["a2e6", "d7d8", "f7f8"]))
    .toBe("7102eb92e00287f834ec6f5a2dd1afaaebfdafd3c649b975444a77ccff836d5a");
});


it("matches the producer Python split for NEL, C0 separators and non-whitespace FEFF", () => {
  expect(splitTacticMoves("\u0085e2e4\u001ce7e5\u2000g1f3\u0085")).toEqual(["e2e4", "e7e5", "g1f3"]);
  expect(splitTacticMoves("e2e4\ufeffe7e5 g1f3")).toEqual(["e2e4\ufeffe7e5", "g1f3"]);
});
