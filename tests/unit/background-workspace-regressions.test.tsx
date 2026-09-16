import { readFileSync } from "node:fs";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import { computeStudyTask } from "../../app/lib/study-computation";
import { loadTacticsDeck, preloadView, readWorkspaceData, invalidateWorkspaceData } from "../../app/lib/workspace-data";
import { useBackgroundStudy } from "../../app/hooks/use-background-study";
import type { StudyTask } from "../../app/lib/study-computation";
import type { PackagedPuzzle } from "../../app/types";
import { MoveComparisonTable } from "../../app/components/move-comparison-table";
import { asUciMove, asSanMove } from "../../app/types";

const records = JSON.parse(readFileSync("public/data/tactics-decks.json", "utf8")) as PackagedPuzzle[];

it("real hanging-piece packs contain 100 validated playable cards after their setup move", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(records)));
  const deck = await loadTacticsDeck("hangingPiece", "easy");
  expect(deck).toHaveLength(100);
  expect(new Set(deck.map(item => item.card.id)).size).toBe(100);
  for (const { card } of deck) { const board = new Chess(card.startingFen); for (const san of card.moves) expect(board.move(san)).toBeTruthy(); }
});

it("tab preloading prepares the first unfinished tactic stage and shares the validated deck request", async () => {
  const fetcher = vi.fn(async (url: string) => Response.json(url.includes("/progress") ? { "hangingPiece:easy": { clean: 100 } } : records));
  vi.stubGlobal("fetch", fetcher);
  await preloadView("tactics");
  const prepared = await loadTacticsDeck("hangingPiece", "medium");
  expect(prepared).toHaveLength(100);
  expect(fetcher.mock.calls.filter(([url]) => url.includes("tactics-decks"))).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([url]) => url.includes("/progress"))).toHaveLength(1);
});

it("failed asset requests report HTTP errors and remain retryable without sample fallback", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response("Forbidden", { status: 403 })).mockResolvedValueOnce(Response.json(records));
  vi.stubGlobal("fetch", fetcher);
  await expect(loadTacticsDeck("hangingPiece", "easy")).rejects.toThrow("HTTP 403");
  await expect(loadTacticsDeck("hangingPiece", "easy")).resolves.toHaveLength(100);
});

it("background diagnostics yield before computation and discard stale generations", async () => {
  const first: StudyTask = { kind: "lines", lines: [] };
  const second: StudyTask = { kind: "deck", records, deckId: "fork-easy" };
  function Probe({ task }: { task: StudyTask }) {
    const result = useBackgroundStudy<unknown[]>(task, []);
    return <p>{result.length} results</p>;
  }
  const view = render(<Probe task={first} />);
  expect(screen.getByText("0 results")).toBeTruthy();
  view.rerender(<Probe task={second} />);
  expect(screen.getByText("0 results")).toBeTruthy();
  await waitFor(() => expect(screen.getByText("100 results")).toBeTruthy());
  expect(computeStudyTask(first)).toEqual([]);
});

it("preloaded local records are invalidated after mutations rather than hiding new study data", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ count: 1 })).mockResolvedValueOnce(Response.json({ count: 2 }));
  vi.stubGlobal("fetch", fetcher);
  expect(await readWorkspaceData("http://localhost/api/repertoires")).toEqual({ count: 1 });
  invalidateWorkspaceData();
  expect(await readWorkspaceData("http://localhost/api/repertoires")).toEqual({ count: 2 });
});

it("Builder comparison keeps covered moves and displays all source details together", () => {
  const move = { uci: asUciMove("e2e4"), san: asSanMove("e4") };
  const onPlay = vi.fn();
  render(<MoveComparisonTable repertoire={[move]} engine={[{ ...move, score: "+0.30" }]} maia={[{ ...move, probability: .45 }]} lichess={[{ ...move, white: 60, draws: 20, black: 20 }]} masters={[{ ...move, white: 20, draws: 60, black: 20 }]} turn="white" onPlay={onPlay} onHover={vi.fn()} />);
  for (const heading of ["Stockfish", "Maia", "Lichess", "Masters"]) expect(screen.getByRole("columnheader", { name: heading })).toBeTruthy();
  expect(screen.getByText("+0.30")).toBeTruthy(); expect(screen.getByText("45%")).toBeTruthy();
  expect(screen.getByText(/70% score/)).toBeTruthy(); expect(screen.getByText(/50% score/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "e4" })); expect(onPlay).toHaveBeenCalledWith("e2e4");
});
