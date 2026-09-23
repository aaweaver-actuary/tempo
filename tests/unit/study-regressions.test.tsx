import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import TacticsView from "../../app/views/tactics_view";
import Home from "../../app/views/home_view";
import { advanceTacticProgress } from "../../app/lib/tactics-progress";
import { useTrainingStore } from "../../app/state/training-store";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: (props: {
    fen: string;
    showHint: boolean;
    locked: boolean;
    onMove: (from: string, to: string) => void;
  }) => (
    <div
      data-testid="board"
      data-fen={props.fen}
      data-hint={String(props.showHint)}
    >
      {[
        ["a3", "b4"],
        ["a2", "e6"],
        ["f7", "f8"],
        ["e7", "e5"],
        ["b8", "c6"],
        ["g8", "f6"],
        ["e7", "e6"],
        ["e2", "e4"],
        ["g1", "f3"],
      ].map(([from, to]) => (
        <button
          key={from + to}
          disabled={props.locked}
          onClick={() => props.onMove(from, to)}
        >
          {from + to}
        </button>
      ))}
    </div>
  ),
}));
vi.mock("../../app/lib/move-sound", () => ({
  playMoveSound: vi.fn(),
  playChessMoveSound: vi.fn(),
  moveSoundEnabled: () => false,
}));
vi.mock("../../app/lib/analysis-engines", () => ({
  analyzeWithStockfish: vi.fn(async () => []),
  analyzeWithMaia: vi.fn(async () => []),
}));

const tacticsCatalog = {
  version: 1,
  groups: [{ id: "basic", name: "Basic motifs" }],
  themes: [
    { id: "hangingPiece", name: "Hanging pieces", group: "basic" },
    { id: "fork", name: "Forks", group: "basic" },
  ],
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
    {
      id: "fork-easy-01",
      theme: "fork",
      group: "basic",
      difficulty: "easy",
      ordinal: 1,
      count: 25,
      minRating: 700,
      maxRating: 1100,
      asset: "data/tactics-packs/fork-easy-01.json",
      legacyDeckId: "fork-easy",
      active: false,
      clean: 0,
      introduced: 0,
      due: 0,
    },
  ],
};
const sourceFen = "q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17";
const startingFen = "q5nr/1ppknQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 w - - 1 18";
const records = [1, 2].map((number) => ({
  PuzzleId: `mate-${number}`,
  DeckId: "hangingPiece-easy-01",
  DeckPosition: number,
  FEN: sourceFen,
  Moves: "e8d7 a2e6 d7d8 f7f8",
  Rating: 900,
}));
records.push({
  PuzzleId: "fork-1",
  DeckId: "fork-easy-01",
  DeckPosition: 1,
  FEN: new Chess().fen(),
  Moves: "e2e4 e7e5 g1f3 b8c6",
  Rating: 900,
});

function mockTactics(progressPayload: unknown = {}) {
  const attempts: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, options) => {
      const url = String(input);
      if (url.includes("tactics-packs/hanging"))
        return Response.json(
          records.filter((record) => record.DeckId.startsWith("hanging")),
        );
      if (url.includes("tactics-packs/fork"))
        return Response.json(
          records.filter((record) => record.DeckId.startsWith("fork")),
        );
      if (url.includes("tactics/catalog")) return Response.json(tacticsCatalog);
      if (url.endsWith("/api/tactics/progress")) return Response.json(progressPayload);
      if (url.endsWith("/api/tactics/attempt")) {
        attempts.push(JSON.parse(options.body));
        return Response.json({});
      }
      return Response.json({});
    }),
  );
  return attempts;
}
async function readyTactics() {
  render(
    <TacticsView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />,
  );
  await waitFor(() =>
    expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(
      startingFen,
    ),
  );
}
async function pause(ms = 751) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

describe("reported study regressions", () => {
  it("failed review save retains the completed card for retry; successful review is not reported as failed when queue refresh fails", async () => {
    const queueCard = {
      id: "retry-review",
      queue_entry_id: 901,
      start_fen: new Chess().fen(),
      moves: ["e2e4"],
      content_type: "opening",
      repertoire_name: "Retry prep",
      repertoire_source: "PGN",
      attempt_state: "clean",
    };
    let reviewRequests = 0;
    let reviewSaved = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input) => {
        const url = String(input);
        if (url.endsWith("/api/queue/today")) {
          if (reviewSaved)
            return Response.json(
              { code: "database_busy", retryable: true, detail: "Database busy" },
              { status: 503 },
            );
          return Response.json({ cards: [queueCard] });
        }
        if (url.endsWith("/review")) {
          reviewRequests += 1;
          if (reviewRequests === 1)
            return Response.json(
              {
                code: "database_busy",
                retryable: true,
                detail: "The local database is busy with background work.",
              },
              { status: 503 },
            );
          reviewSaved = true;
          return Response.json({
            queue_entry_id: 901,
            persisted: true,
            idempotent: false,
          });
        }
        return Response.json(
          url.endsWith("/teaching")
            ? { states: [] }
            : url.endsWith("/sync-status")
              ? { providers: [] }
              : { lines: [] },
        );
      }),
    );
    render(<Home />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Correct" })).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Correct" }));
    await waitFor(
      () => expect(screen.getByRole("button", { name: "Retry save" })).toBeTruthy(),
      { timeout: 2_000 },
    );
    expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(
      queueCard.start_fen,
    );
    expect(screen.getByText(/The local database could not save this result/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Retry loading the queue" }),
      ).toBeTruthy(),
    );
    expect(screen.queryByText(/could not save this result/)).toBeNull();
    expect(reviewRequests).toBe(2);
  });

  it("tomorrow and later opening reviews remain unassisted even when teaching storage is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input) =>
        String(input).endsWith("/api/queue/today")
          ? Response.json({
              cards: [
                {
                  id: "previously-clean",
                  queue_entry_id: 80,
                  start_fen: new Chess().fen(),
                  moves: ["e2e4", "e7e5", "g1f3"],
                  content_type: "opening",
                  repertoire_name: "Prep",
                  repertoire_source: "PGN",
                  first_correct_at: "2026-09-15T12:00:00Z",
                },
              ],
            })
          : Response.json(
              String(input).endsWith("/teaching")
                ? { states: [] }
                : String(input).endsWith("/sync-status")
                  ? { providers: [] }
                  : { lines: [] },
            ),
      ),
    );
    render(<Home />);
    await waitFor(() => expect(screen.getByTestId("board")).toBeTruthy());
    await pause(150);
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    fireEvent.click(screen.getByText("e2e4"));
    await pause(430);
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
  });
  it("Show move during initial teaching records failure and keeps required guidance", async () => {
    const failures: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input, options) => {
        const url = String(input);
        if (url.endsWith("/api/queue/today"))
          return Response.json({
            cards: [
              {
                id: "help-card",
                queue_entry_id: 60,
                start_fen: new Chess().fen(),
                moves: ["e2e4", "e7e5", "g1f3"],
                content_type: "opening",
                repertoire_name: "Prep",
                repertoire_source: "PGN",
              },
            ],
          });
        if (options?.method === "POST" && url.endsWith("/fail"))
          failures.push(url);
        return Response.json(
          String(input).endsWith("/teaching")
            ? { states: [] }
            : String(input).endsWith("/sync-status")
              ? { providers: [] }
              : { lines: [] },
        );
      }),
    );
    render(<Home />);
    await waitFor(() =>
      expect(screen.getByTestId("board").getAttribute("data-hint")).toBe(
        "true",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /Show move/ }));
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("true");
    expect(
      screen
        .getByRole("button", { name: "Finish on the board" })
        .hasAttribute("disabled"),
    ).toBe(true);
    await waitFor(() =>
      expect(failures).toContain(
        "http://127.0.0.1:8000/api/queue/entries/60/fail",
      ),
    );
  });
  it("self-reported first clean solves receive reinforcement without teaching later plies", async () => {
    let queue = [
      {
        id: "opening-card",
        queue_entry_id: 1,
        start_fen: new Chess().fen(),
        moves: ["e2e4", "e7e5", "g1f3"],
        content_type: "opening",
        repertoire_name: "Prep",
        repertoire_source: "PGN",
        attempt_state: "clean",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input) => {
        if (String(input).endsWith("/api/queue/today"))
          return Response.json({ cards: queue });
        if (String(input).endsWith("/review"))
          queue = [
            { ...queue[0], queue_entry_id: 2, attempt_state: "reinforcement" },
          ];
        return Response.json(
          String(input).endsWith("/teaching")
            ? { states: [] }
            : String(input).endsWith("/sync-status")
              ? { providers: [] }
              : { lines: [] },
        );
      }),
    );
    render(<Home />);
    await waitFor(() =>
      expect(screen.getByTestId("board").getAttribute("data-hint")).toBe(
        "true",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Correct" }));
    await waitFor(() => expect(screen.getByText("Reinforcement")).toBeTruthy());
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    fireEvent.click(screen.getByText("e2e4"));
    await pause(430);
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
  });
  it("Black training mounts without selector loops and plays from the current position", async () => {
    let cards = [
      {
        id: "black-card",
        queue_entry_id: 99,
        start_fen: new Chess().fen(),
        moves: ["d2d4", "g8f6", "c2c4", "e7e6"],
        content_type: "opening",
        repertoire_name: "Black prep",
        repertoire_source: "PGN",
        trained_color: "black",
      },
    ];
    const reviews: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input, options) => {
        if (String(input).endsWith("/api/queue/today"))
          return Response.json({ cards });
        if (String(input).endsWith("/review")) {
          reviews.push(JSON.parse(options.body));
          cards = [];
        }
        return Response.json(
          String(input).endsWith("/teaching")
            ? { states: [] }
            : String(input).endsWith("/sync-status")
              ? { providers: [] }
              : { lines: [] },
        );
      }),
    );
    render(<Home />);
    const board = new Chess();
    board.move("d4");
    await waitFor(() =>
      expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(
        board.fen(),
      ),
    );
    fireEvent.click(screen.getByText("g8f6"));
    board.move("Nf6");
    board.move("c4");
    await pause(430);
    expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(
      board.fen(),
    );
    fireEvent.click(screen.getByText("e7e6"));
    board.move("e6");
    expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(
      board.fen(),
    );
    await pause(250);
    expect(reviews).toHaveLength(0);
    await pause();
    expect(reviews).toHaveLength(1);
  });
  it("tactic failure remains interactive until the full guided solution is complete", async () => {
    const attempts = mockTactics();
    await readyTactics();
    fireEvent.click(screen.getByText("a3b4"));
    expect(screen.getByText("Follow the arrow")).toBeTruthy();
    expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(
      startingFen,
    );
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("true");
    await pause();
    expect(screen.getByText("Puzzle 1 of 25")).toBeTruthy();
    expect(attempts).toHaveLength(0);
    fireEvent.click(screen.getByText("a2e6"));
    fireEvent.click(screen.getByText("f7f8"));
    await waitFor(() => expect(attempts).toHaveLength(1));
    expect(attempts[0]).toMatchObject({ clean: false, correct: false });
    expect(
      new Chess(
        screen.getByTestId("board").getAttribute("data-fen")!,
      ).isCheckmate(),
    ).toBe(true);
    await pause();
    await waitFor(() =>
      expect(screen.getByText("Puzzle 2 of 25")).toBeTruthy(),
    );
  });
  it("tactic setup is applied and the final mate remains during feedback", async () => {
    mockTactics();
    await readyTactics();
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    fireEvent.click(screen.getByText("a2e6"));
    fireEvent.click(screen.getByText("f7f8"));
    expect(
      new Chess(
        screen.getByTestId("board").getAttribute("data-fen")!,
      ).isCheckmate(),
    ).toBe(true);
    await pause(250);
    expect(
      new Chess(
        screen.getByTestId("board").getAttribute("data-fen")!,
      ).isCheckmate(),
    ).toBe(true);
    await pause();
    await waitFor(() =>
      expect(screen.getByText("Puzzle 2 of 25")).toBeTruthy(),
    );
  });
  it("motif progress is independent and stale completion cannot advance another deck", async () => {
    mockTactics();
    await readyTactics();
    fireEvent.click(screen.getByText("a2e6"));
    fireEvent.click(screen.getByText("f7f8"));
    fireEvent.click(screen.getByRole("tab", { name: "Packs" }));
    fireEvent.click(screen.getByText("Forks"));
    fireEvent.click(
      screen.getAllByRole("button", { name: /Easy · Pack 1/ }).at(-1)!,
    );
    await waitFor(() =>
      expect(screen.getAllByText("black to play").length).toBeGreaterThan(0),
    );
    await pause();
    expect(screen.getByText("Puzzle 1 of 25")).toBeTruthy();
    fireEvent.click(screen.getByText("e7e5"));
    await waitFor(() =>
      expect(screen.getByTestId("board").getAttribute("data-fen")).toContain(
        "5N2",
      ),
    );
    fireEvent.click(screen.getByText("b8c6"));
    await waitFor(() =>
      expect(
        JSON.parse(localStorage.getItem("tempo-tactics-progress-v2") ?? "{}")[
          "fork-easy-01"
        ]?.index,
      ).toBe(1),
    );
  });
  it("completed selected pack keeps the catalog available for choosing another pack", async () => {
    localStorage.setItem("tempo-tactic-selected-pack-v1", "hangingPiece-easy-01");
    mockTactics({
      "hangingPiece-easy-01": {
        clean: 2,
        index: 2,
        cleanIds: ["lichess-mate-1", "lichess-mate-2"],
        discoveredIds: ["lichess-mate-1", "lichess-mate-2"],
      },
    });
    render(
      <TacticsView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />,
    );
    await waitFor(() =>
      expect(screen.getByText(/This pack is complete/)).toBeTruthy(),
    );
    expect(screen.getByText("Forks")).toBeTruthy();
  });
  it("clean progress counts distinct puzzle IDs", () => {
    const first = advanceTacticProgress({}, "fork:easy", true, "same");
    const second = advanceTacticProgress(first, "fork:easy", true, "same");
    expect(second["fork:easy"].clean).toBe(1);
    expect(second["fork:easy"].index).toBe(1);
  });
  it("unseen tactic review has no automatic teaching arrow and retains the final mate before reinforcement", async () => {
    let queue = [
      {
        id: "mate-card",
        queue_entry_id: 1,
        start_fen: startingFen,
        moves: ["a2e6", "d7d8", "f7f8"],
        content_type: "tactic",
        repertoire_name: "Tactics",
        repertoire_source: "Lichess",
        cycle: 0,
      },
    ];
    const reviews: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input, options) => {
        if (String(input).endsWith("/api/queue/today"))
          return Response.json({ cards: queue });
        if (String(input).endsWith("/review")) {
          reviews.push(JSON.parse(options.body));
          queue =
            reviews.length === 1
              ? [{ ...queue[0], queue_entry_id: 2, cycle: 1 }]
              : [];
          return Response.json({});
        }
        return Response.json({ providers: [], states: [] });
      }),
    );
    render(<Home />);
    await waitFor(() =>
      expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(
        startingFen,
      ),
    );
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    fireEvent.click(screen.getByText("a2e6"));
    await pause(430);
    fireEvent.click(screen.getByText("f7f8"));
    await pause(250);
    expect(
      new Chess(
        screen.getByTestId("board").getAttribute("data-fen")!,
      ).isCheckmate(),
    ).toBe(true);
    expect(reviews).toHaveLength(0);
    await pause();
    expect(reviews).toHaveLength(1);
    await waitFor(() =>
      expect(useTrainingStore.getState().attempt.phase).toBe("playerTurn"),
    );
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    fireEvent.click(screen.getByText("a2e6"));
    await pause(430);
    fireEvent.click(screen.getByText("f7f8"));
    await pause();
    expect(reviews).toHaveLength(2);
    await waitFor(() =>
      expect(screen.getByText(/You['’]re done for today/)).toBeTruthy(),
    );
    expect(screen.queryByText(/First clean solve/)).toBeNull();
  });
});
