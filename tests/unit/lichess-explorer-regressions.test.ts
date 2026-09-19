import { expect, it, vi } from "vitest";
import {
  loadExplorer,
  readCachedExplorer,
} from "../../app/lib/lichess-explorer";

it("lichess explorer uses public requests and cached results survive builder remount", async () => {
  localStorage.clear();
  const payload = {
    white: 10,
    draws: 5,
    black: 5,
    moves: [{ uci: "e2e4", san: "e4", white: 6, draws: 2, black: 2 }],
  };
  const fetcher = vi.fn(async () => Response.json(payload));
  vi.stubGlobal("fetch", fetcher);
  const fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  await loadExplorer(fen, "rapid", "1600");
  expect(readCachedExplorer(fen, "rapid", "1600")?.human).toEqual(payload);
  expect(fetcher).toHaveBeenCalledTimes(2);
  for (const [, options] of fetcher.mock.calls as unknown as Array<
    [RequestInfo | URL, RequestInit | undefined]
  >)
    expect(options).toBeUndefined();
});
