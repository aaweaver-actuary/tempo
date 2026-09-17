import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import TacticsView from "../../app/views/tactics_view";
import Home from "../../app/views/home_view";
import { advanceTacticProgress } from "../../app/lib/tactics-progress";
import { useTrainingStore } from "../../app/state/training-store";

vi.mock("../../app/components/chessboard", () => ({ Chessboard: (props: { fen: string; showHint: boolean; locked: boolean; onMove: (from: string, to: string) => void }) => <div data-testid="board" data-fen={props.fen} data-hint={String(props.showHint)}>{[["a3","b4"],["a2","e6"],["f7","f8"],["e7","e5"],["b8","c6"],["g8","f6"],["e7","e6"],["e2","e4"],["g1","f3"]].map(([from,to]) => <button key={from+to} disabled={props.locked} onClick={() => props.onMove(from,to)}>{from+to}</button>)}</div> }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), moveSoundEnabled: () => false }));
vi.mock("../../app/lib/analysis-engines", () => ({ analyzeWithStockfish: vi.fn(async () => []), analyzeWithMaia: vi.fn(async () => []) }));

const sourceFen = "q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17";
const startingFen = "q5nr/1ppknQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 w - - 1 18";
const records = [1,2].map((number) => ({ PuzzleId: `mate-${number}`, DeckId: "hangingPiece-easy", DeckPosition: number, FEN: sourceFen, Moves: "e8d7 a2e6 d7d8 f7f8", Rating: 900 }));
records.push({ PuzzleId: "fork-1", DeckId: "fork-easy", DeckPosition: 1, FEN: new Chess().fen(), Moves: "e2e4 e7e5 g1f3 b8c6", Rating: 900 });

function mockTactics() {
  const attempts: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input, options) => {
    const url = String(input);
    if (url.includes("tactics-decks")) return Response.json(records);
    if (url.endsWith("/api/tactics/progress")) return Response.json({});
    if (url.endsWith("/api/tactics/attempt")) { attempts.push(JSON.parse(options.body)); return Response.json({}); }
    return Response.json({});
  }));
  return attempts;
}
async function readyTactics() {
  render(<TacticsView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />);
  await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(startingFen));
}
async function pause(ms = 751) { await act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)); }); }

describe("reported study regressions", () => {
  it("tomorrow and later opening reviews remain unassisted even when teaching storage is empty", async () => {
    vi.stubGlobal("fetch", vi.fn(async input => String(input).endsWith("/api/queue/today") ? Response.json({ cards: [{ id: "previously-clean", queue_entry_id: 80, start_fen: new Chess().fen(), moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening", repertoire_name: "Prep", repertoire_source: "PGN", first_correct_at: "2026-09-15T12:00:00Z" }] }) : Response.json(String(input).endsWith("/teaching") ? {states:[]} : String(input).endsWith("/sync-status") ? {providers:[]} : {lines:[]})));
    render(<Home />);
    await waitFor(() => expect(screen.getByTestId("board")).toBeTruthy());
    await pause(150);
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    fireEvent.click(screen.getByText("e2e4")); await pause(430);
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
  });
  it("Show move during initial teaching records failure and keeps required guidance", async () => {
    const failures: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input, options) => {
      const url = String(input);
      if (url.endsWith("/api/queue/today")) return Response.json({ cards: [{ id: "help-card", queue_entry_id: 60, start_fen: new Chess().fen(), moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening", repertoire_name: "Prep", repertoire_source: "PGN" }] });
      if (options?.method === "POST" && url.endsWith("/fail")) failures.push(url);
      return Response.json(String(input).endsWith("/teaching") ? {states:[]} : String(input).endsWith("/sync-status") ? {providers:[]} : {lines:[]});
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("true"));
    fireEvent.click(screen.getByRole("button", { name: /Show move/ }));
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("true");
    expect(screen.getByRole("button", { name: "Finish on the board" }).hasAttribute("disabled")).toBe(true);
    await waitFor(() => expect(failures).toContain("http://127.0.0.1:8000/api/queue/entries/60/fail"));
  });
  it("self-reported first clean solves receive reinforcement without teaching later plies", async () => {
    let queue = [{ id: "opening-card", queue_entry_id: 1, start_fen: new Chess().fen(), moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening", repertoire_name: "Prep", repertoire_source: "PGN", attempt_state: "clean" }];
    vi.stubGlobal("fetch", vi.fn(async (input) => {
      if (String(input).endsWith("/api/queue/today")) return Response.json({ cards: queue });
      if (String(input).endsWith("/review")) queue = [{ ...queue[0], queue_entry_id: 2, attempt_state: "reinforcement" }];
      return Response.json(String(input).endsWith("/teaching") ? {states:[]} : String(input).endsWith("/sync-status") ? {providers:[]} : {lines:[]});
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("true"));
    fireEvent.click(screen.getByRole("button", { name: "Correct" }));
    await waitFor(() => expect(screen.getByText("Reinforcement")).toBeTruthy());
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    fireEvent.click(screen.getByText("e2e4")); await pause(430);
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
  });
  it("Black training mounts without selector loops and plays from the current position", async () => {
    let cards = [{ id: "black-card", queue_entry_id: 99, start_fen: new Chess().fen(), moves: ["d2d4", "g8f6", "c2c4", "e7e6"], content_type: "opening", repertoire_name: "Black prep", repertoire_source: "PGN", trained_color: "black" }];
    const reviews: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input, options) => {
      if (String(input).endsWith("/api/queue/today")) return Response.json({ cards });
      if (String(input).endsWith("/review")) { reviews.push(JSON.parse(options.body)); cards = []; }
      return Response.json(String(input).endsWith("/teaching") ? {states:[]} : String(input).endsWith("/sync-status") ? {providers:[]} : {lines:[]});
    }));
    render(<Home />);
    const board = new Chess(); board.move("d4");
    await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(board.fen()));
    fireEvent.click(screen.getByText("g8f6")); board.move("Nf6"); board.move("c4");
    await pause(430);
    expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(board.fen());
    fireEvent.click(screen.getByText("e7e6")); board.move("e6");
    expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(board.fen());
    await pause(250); expect(reviews).toHaveLength(0);
    await pause(); expect(reviews).toHaveLength(1);
  });
  it("tactic failure remains interactive until the full guided solution is complete", async () => {
    const attempts = mockTactics();
    await readyTactics();
    fireEvent.click(screen.getByText("a3b4"));
    expect(screen.getByText("Follow the arrow")).toBeTruthy();
    expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(startingFen);
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("true");
    await pause();
    expect(screen.getByText("Puzzle 1 of 100")).toBeTruthy();
    expect(attempts).toHaveLength(0);
    fireEvent.click(screen.getByText("a2e6"));
    fireEvent.click(screen.getByText("f7f8"));
    await waitFor(() => expect(attempts).toHaveLength(1));
    expect(attempts[0]).toMatchObject({ clean: false, correct: false });
    expect(new Chess(screen.getByTestId("board").getAttribute("data-fen")!).isCheckmate()).toBe(true);
    await pause();
    await waitFor(() => expect(screen.getByText("Puzzle 2 of 100")).toBeTruthy());
  });
  it("tactic setup is applied and the final mate remains during feedback", async () => {
    mockTactics(); await readyTactics();
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    fireEvent.click(screen.getByText("a2e6"));
    fireEvent.click(screen.getByText("f7f8"));
    expect(new Chess(screen.getByTestId("board").getAttribute("data-fen")!).isCheckmate()).toBe(true);
    await pause(250);
    expect(new Chess(screen.getByTestId("board").getAttribute("data-fen")!).isCheckmate()).toBe(true);
    await pause();
    await waitFor(() => expect(screen.getByText("Puzzle 2 of 100")).toBeTruthy());
  });
  it("motif progress is independent and stale completion cannot advance another deck", async () => {
    mockTactics(); await readyTactics();
    fireEvent.click(screen.getByText("a2e6")); fireEvent.click(screen.getByText("f7f8"));
    fireEvent.click(screen.getByText("Forks"));
    await waitFor(() => expect(screen.getAllByText("black to play").length).toBeGreaterThan(0));
    await pause();
    expect(screen.getByText("Puzzle 1 of 100")).toBeTruthy();
    fireEvent.click(screen.getByText("e7e5")); fireEvent.click(screen.getByText("b8c6"));
    await pause();
    expect(JSON.parse(localStorage.getItem("tempo-tactics-progress-v2")!)["fork:easy"].index).toBe(1);
  });
  it("clean progress counts distinct puzzle IDs", () => {
    const first = advanceTacticProgress({}, "fork:easy", true, "same");
    const second = advanceTacticProgress(first, "fork:easy", true, "same");
    expect(second["fork:easy"].clean).toBe(1);
    expect(second["fork:easy"].index).toBe(1);
  });
  it("unseen tactic review has no automatic teaching arrow and retains the final mate before reinforcement", async () => {
    let queue = [{ id: "mate-card", queue_entry_id: 1, start_fen: startingFen, moves: ["a2e6","d7d8","f7f8"], content_type: "tactic", repertoire_name: "Tactics", repertoire_source: "Lichess", cycle: 0 }];
    const reviews: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input, options) => {
      if (String(input).endsWith("/api/queue/today")) return Response.json({ cards: queue });
      if (String(input).endsWith("/review")) {
        reviews.push(JSON.parse(options.body));
        queue = reviews.length === 1 ? [{ ...queue[0], queue_entry_id: 2, cycle: 1 }] : [];
        return Response.json({});
      }
      return Response.json({ providers: [], states: [] });
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(startingFen));
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    fireEvent.click(screen.getByText("a2e6")); await pause(430);
    fireEvent.click(screen.getByText("f7f8"));
    await pause(250);
    expect(new Chess(screen.getByTestId("board").getAttribute("data-fen")!).isCheckmate()).toBe(true);
    expect(reviews).toHaveLength(0);
    await pause();
    expect(reviews).toHaveLength(1);
    await waitFor(() => expect(useTrainingStore.getState().attempt.phase).toBe("playerTurn"));
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    fireEvent.click(screen.getByText("a2e6")); await pause(430); fireEvent.click(screen.getByText("f7f8")); await pause();
    expect(reviews).toHaveLength(2);
    await waitFor(() => expect(screen.getByText(/You['’]re done for today/)).toBeTruthy());
    expect(screen.queryByText(/First clean solve/)).toBeNull();
  });
});
