import { expect, it, vi } from "vitest";

const engine = vi.hoisted(() => ({
  calls: [] as Array<{
    fen: string;
    resolve: (moves: unknown[]) => void;
    reject: (error: Error) => void;
  }>,
}));

vi.mock("../../app/lib/analysis-engines", () => {
  class StockfishCancelledError extends Error {}
  return {
    StockfishCancelledError,
    analyzeWithStockfish: vi.fn(
      (fen: string, _depth: number, signal?: AbortSignal) =>
        new Promise((resolve, reject) => {
          const call = { fen, resolve, reject };
          engine.calls.push(call);
          signal?.addEventListener(
            "abort",
            () => reject(new StockfishCancelledError()),
            { once: true },
          );
        }),
    ),
  };
});

it("background game analysis yields to interactive engine work and resumes once", async () => {
  const { requestBackgroundAnalysis, requestInteractiveAnalysis } = await import(
    "../../app/lib/engine-broker"
  );
  const backgroundFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  const interactiveFen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
  const background = requestBackgroundAnalysis(backgroundFen, 14);
  await vi.waitFor(() => expect(engine.calls).toHaveLength(1));
  const interactive = requestInteractiveAnalysis(interactiveFen, 10);
  await vi.waitFor(() => expect(engine.calls).toHaveLength(2));
  expect(engine.calls[1].fen).toBe(interactiveFen);
  engine.calls[1].resolve([]);
  await expect(interactive).resolves.toEqual([]);
  await vi.waitFor(() => expect(engine.calls).toHaveLength(3));
  expect(engine.calls[2].fen).toBe(backgroundFen);
  engine.calls[2].resolve([]);
  await expect(background).resolves.toEqual([]);
});
