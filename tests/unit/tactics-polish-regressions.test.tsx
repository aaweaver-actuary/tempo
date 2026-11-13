import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import TacticsView from "../../app/views/tactics_view";
import { playChessMoveSound } from "../../app/lib/move-sound";

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
vi.mock("../../app/lib/move-sound", () => ({
  playMoveSound: vi.fn(),
  playChessMoveSound: vi.fn(),
}));
const catalog = {
  version: 1,
  groups: [{ id: "basic", name: "Basic motifs" }],
  themes: [{ id: "hangingPiece", name: "Hanging pieces", group: "basic" }],
  packs: [
    {
      id: "hangingPiece-easy-01",
      theme: "hangingPiece",
      group: "basic",
      difficulty: "easy",
      ordinal: 1,
      count: 25,
      minRating: 700,
      maxRating: 1100,
      asset: "data/tactics-packs/hangingPiece-easy-01.json",
      legacyDeckId: "hangingPiece-easy",
      active: false,
      clean: 0,
      introduced: 0,
      due: 0,
    },
  ],
};
const sourceFen = "q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17";
const records = Array.from({ length: 26 }, (_, i) => ({
  PuzzleId: `polish-${i + 1}`,
  DeckId: "hangingPiece-easy-01",
  DeckPosition: i + 1,
  FEN: sourceFen,
  Moves: "e8d7 a2e6 d7d8 f7f8",
  Rating: 900,
}));
const advanceTacticTimer = async () => {
  await act(async () => { await vi.advanceTimersByTimeAsync(800); });
  vi.useRealTimers();
};

afterEach(() => vi.useRealTimers());

it("mounts Tactics and activates a pack without requesting game insights", async () => {
  const requests: Array<{ url: string; method: string }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input, options) => {
    const url = String(input);
    requests.push({ url, method: options?.method ?? "GET" });
    if (url.includes("tactics-packs")) return Response.json(records.slice(0, 25));
    if (url.endsWith("/api/tactics/activation")) return Response.json(catalog);
    if (url.includes("tactics/catalog")) return Response.json(catalog);
    return Response.json({});
  }));
  render(<TacticsView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />);
  await screen.findByText("Puzzle 1 of 25");
  fireEvent.click(screen.getByRole("tab", { name: "Packs" }));
  fireEvent.click(screen.getByRole("button", { name: "Activate Hanging pieces easy pack 1" }));
  await waitFor(() => expect(requests.some(({ url, method }) => url.endsWith("/api/tactics/activation") && method === "PUT")).toBe(true));
  expect(requests.some(({ url }) => url.includes("/api/game-insights/"))).toBe(false);
});

it("legacy clean puzzle identities select the next unattempted deck position rather than the attempt counter", async () => {
  const cleanIds = records
    .slice(0, 24)
    .map((record) => `lichess-${record.PuzzleId}`);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input) =>
      Response.json(
        String(input).includes("tactics-packs")
          ? records.slice(0, 25)
          : String(input).includes("tactics/catalog")
            ? catalog
            : { "hangingPiece-easy-01": { index: 4, clean: 24, cleanIds } },
      ),
    ),
  );
  render(
    <TacticsView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />,
  );
  await waitFor(() => expect(screen.getByText("Puzzle 25 of 25")).toBeTruthy());
});

it("tactic Show Move, capture dialog, and Restart retain the guided attempt and classify the opponent reply sound", async () => {
  const submissions: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, options) => {
      if (String(input).includes("tactics-packs"))
        return Response.json(records.slice(0, 25));
      if (String(input).includes("tactics/catalog"))
        return Response.json(catalog);
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
  await waitFor(() => expect(screen.getByText("Puzzle 1 of 25")).toBeTruthy());
  const startingFen = screen.getByTestId("board").getAttribute("data-fen");
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: /Show move/ }));
  await advanceTacticTimer();
  expect(screen.getByText("Puzzle 1 of 25")).toBeTruthy();
  expect(screen.getByText("Follow the arrow")).toBeTruthy();
  const guidedFenBeforeCapture = screen.getByTestId("board").getAttribute("data-fen");
  fireEvent.click(screen.getByRole("button", { name: "Capture tactic" }));
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect((screen.getAllByText("a2e6")[0] as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Close window" }));
  expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(guidedFenBeforeCapture);
  expect(screen.getByText("Follow the arrow")).toBeTruthy();
  fireEvent.click(screen.getByText("a2e6"));
  expect(playChessMoveSound).toHaveBeenCalledWith(
    expect.objectContaining({ from: "d7", to: "d8" }),
    false,
    "opponent",
  );
  fireEvent.click(screen.getByRole("button", { name: /Restart/ }));
  expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(
    startingFen,
  );
  expect(screen.getByText("Follow the arrow")).toBeTruthy();
  fireEvent.click(screen.getByText("a2e6"));
  vi.useFakeTimers();
  fireEvent.click(screen.getByText("f7f8"));
  await act(async () => { await Promise.resolve(); });
  expect(submissions).toHaveLength(1);
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
  await advanceTacticTimer();
  await waitFor(() => expect(screen.getByText("Puzzle 2 of 25")).toBeTruthy());
  expect(screen.queryByText("Follow the arrow")).toBeNull();
  expect(screen.getByText("a2e6").hasAttribute("disabled")).toBe(false);
  expect(submissions).toHaveLength(1);
});

it.each(["missing", "foreign"])("%s tactic acknowledgment cannot advance the completed puzzle", async kind => {
  const attempts: string[] = [];
  const fetcher = vi.fn(async (input, options) => {
    const url = String(input);
    if (url.includes("tactics-packs")) return Response.json(records.slice(0, 25));
    if (url.includes("tactics/catalog")) return Response.json(catalog);
    if (url.endsWith("/api/tactics/attempt")) {
      attempts.push(JSON.parse(options.body).attempt_id);
      return Response.json(attempts.length === 1
        ? (kind === "foreign" ? { operation_id: "foreign", state: "queued" } : { state: "queued" })
        : { operation_id: `tactic-attempt:${attempts[0]}`, state: "queued" }, { status: 202 });
    }
    if (url.includes("/api/operations/")) return Response.json({ operation_id: `tactic-attempt:${attempts[0]}`, state: "complete", response: {} });
    return Response.json({});
  });
  vi.stubGlobal("fetch", fetcher);
  render(<TacticsView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />);
  await screen.findByText("Puzzle 1 of 25");
  fireEvent.click(screen.getByText("a2e6"));
  fireEvent.click(screen.getByText("f7f8"));
  await screen.findByText("Could not save this attempt. Retry to save it before continuing.");
  expect(screen.getByText("Puzzle 1 of 25")).toBeTruthy();
  expect(attempts).toHaveLength(1);
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/api/operations/"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
  await waitFor(() => expect(attempts).toHaveLength(2));
  expect(attempts[1]).toBe(attempts[0]);
  await screen.findByText("Puzzle 2 of 25");
  expect(fetcher.mock.calls.filter(([input]) => String(input).includes("/api/operations/"))).toHaveLength(1);
});
