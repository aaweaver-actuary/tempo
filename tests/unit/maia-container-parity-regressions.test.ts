import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { expect, it, vi } from "vitest";
import { runMaiaInference } from "../../app/lib/maia-inference";
import { asFenString } from "../../app/types";

it("container Maia probabilities match browser inference for both colors", async () => {
  const assetRoot = pathToFileURL(`${resolve("public")}/`).href;
  const originalFetch = globalThis.fetch;
  vi.stubGlobal("navigator", { hardwareConcurrency: 1 });
  vi.stubGlobal("crossOriginIsolated", false);
  vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.startsWith("file://")) {
      const bytes = await readFile(new URL(url));
      return new Response(bytes, { status: 200 });
    }
    return originalFetch(input, init);
  });
  try {
    for (const fen of [
      asFenString("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
      asFenString("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"),
    ]) {
      const browserMoves = await runMaiaInference(fen, 1500, assetRoot);
      const output = execFileSync("node", ["scripts/maia-coverage-worker.mjs"], {
        env: { ...process.env, TEMPO_MAIA_SMOKE: "1", TEMPO_MAIA_FIXTURE_FEN: fen }, encoding: "utf8",
      });
      const containerMoves = JSON.parse(output.trim().split("\n").at(-1)!) as { move_uci: string; probability: number }[];
      expect(containerMoves.map((move) => move.move_uci)).toEqual(browserMoves.slice(0, 5).map((move) => move.uci));
      containerMoves.forEach((move, index) => {
        expect(move.probability).toBeCloseTo(browserMoves[index].probability!, 5);
      });
    }
  } finally {
    vi.unstubAllGlobals();
  }
});
