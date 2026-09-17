import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import TacticsView from "../../app/views/tactics_view";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: (props: {
    fen: string;
    locked: boolean;
    showHint: boolean;
    onMove: (from: string, to: string) => void;
  }) => (
    <div
      data-testid="board"
      data-fen={props.fen}
      data-hint={String(props.showHint)}
    >
      <button disabled={props.locked} onClick={() => props.onMove("a2", "e6")}>
        a2e6
      </button>
      <button disabled={props.locked} onClick={() => props.onMove("f7", "f8")}>
        f7f8
      </button>
    </div>
  ),
}));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn() }));
const sourceFen = "q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17";
const records = Array.from({ length: 26 }, (_, i) => ({
  PuzzleId: `polish-${i + 1}`,
  DeckId: "hangingPiece-easy",
  DeckPosition: i + 1,
  FEN: sourceFen,
  Moves: "e8d7 a2e6 d7d8 f7f8",
  Rating: 900,
}));
const pause = async () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 800));
  });

it("legacy clean puzzle identities select the next unattempted deck position rather than the attempt counter", async () => {
  const cleanIds = records
    .slice(0, 24)
    .map((record) => `lichess-${record.PuzzleId}`);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input) =>
      Response.json(
        String(input).includes("tactics-decks")
          ? records
          : { "hangingPiece:easy": { index: 4, clean: 24, cleanIds } },
      ),
    ),
  );
  render(
    <TacticsView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />,
  );
  await waitFor(() =>
    expect(screen.getByText("Puzzle 25 of 100")).toBeTruthy(),
  );
});

it("tactic Show Move and Restart retain one guided attempt then clear X and unlock the next puzzle", async () => {
  const submissions: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, options) => {
      if (String(input).includes("tactics-decks"))
        return Response.json(records);
      if (String(input).endsWith("/attempt")) {
        submissions.push(JSON.parse(options.body));
        return Response.json({});
      }
      return Response.json({});
    }),
  );
  render(
    <TacticsView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />,
  );
  await waitFor(() => expect(screen.getByText("Puzzle 1 of 100")).toBeTruthy());
  const startingFen = screen.getByTestId("board").getAttribute("data-fen");
  fireEvent.click(screen.getByRole("button", { name: /Show move/ }));
  await pause();
  expect(screen.getByText("Puzzle 1 of 100")).toBeTruthy();
  expect(screen.getByText("Follow the arrow")).toBeTruthy();
  fireEvent.click(screen.getByText("a2e6"));
  fireEvent.click(screen.getByRole("button", { name: /Restart/ }));
  expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(
    startingFen,
  );
  expect(screen.getByText("Follow the arrow")).toBeTruthy();
  fireEvent.click(screen.getByText("a2e6"));
  fireEvent.click(screen.getByText("f7f8"));
  await waitFor(() => expect(submissions).toHaveLength(1));
  expect(submissions[0]).toMatchObject({
    correct: false,
    clean: false,
    puzzle_id: "polish-1",
  });
  expect(
    new Chess(
      screen.getByTestId("board").getAttribute("data-fen")!,
    ).isCheckmate(),
  ).toBe(true);
  await pause();
  await waitFor(() => expect(screen.getByText("Puzzle 2 of 100")).toBeTruthy());
  expect(screen.queryByText("Follow the arrow")).toBeNull();
  expect(screen.getByText("a2e6").hasAttribute("disabled")).toBe(false);
  expect(submissions).toHaveLength(1);
});
