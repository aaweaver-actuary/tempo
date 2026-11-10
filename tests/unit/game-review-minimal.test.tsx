import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { GameReviewView } from "../../app/features/game-review/GameReviewView";

vi.mock("@tempo/board-ui", () => ({ Board: (props: { fen: string; orientation: string }) => <div data-testid="board" data-fen={props.fen} data-orientation={props.orientation} /> }));
vi.mock("../../app/lib/study-connection", () => ({ LEARNING_URL: "", learningFetch: (...args: Parameters<typeof fetch>) => fetch(...args) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("replays a saved game with the saved player's orientation", async () => {
  const initial = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  const next = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/repertoires") return Response.json({ repertoires: [{ id: "r1", name: "Main" }] });
    if (url.endsWith("/games")) return Response.json({ games: [{ id: "g1", provider: "lichess", played_at: "2026-01-01", result: "1-0", color: "black" }] });
    return Response.json({ color: "black", positions: [initial, next], moves: ["e2e4"], comparison: { kind: "departure", ply: 1, played_uci: "e2e4", expected_uci: ["d2d4"] } });
  }));
  render(<GameReviewView />);
  fireEvent.click(await screen.findByRole("button", { name: /lichess/ }));
  await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-orientation")).toBe("black"));
  expect(screen.getByText(/First departure at ply 1/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(next);
});
