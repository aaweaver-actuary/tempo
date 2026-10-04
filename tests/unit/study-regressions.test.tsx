import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import TacticsView from "../../app/views/tactics_view";
import Home from "../../app/views/home_view";
import { advanceTacticProgress } from "../../app/lib/tactics-progress";
import { useTrainingStore } from "../../app/state/training-store";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { pendingReviews } from "../../app/lib/review-outbox";

vi.mock("../../app/components/board/chessboard", () => ({
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
  prepareMoveSounds: vi.fn(),
  cancelMoveSounds: vi.fn(),
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
    if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(ms);
    else await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

afterEach(() => vi.useRealTimers());

describe("reported study regressions", () => {
  it("completed tactic advances while the previous review save is still pending", async () => {
    const first = {
      id: "first-overlap", queue_entry_id: 901, start_fen: new Chess().fen(),
      moves: ["e2e4"], content_type: "opening", repertoire_name: "First",
      repertoire_source: "PGN", cycle: 0,
    };
    const tactic = {
      id: "mate-overlap", queue_entry_id: 902, start_fen: startingFen,
      moves: ["a2e6", "d7d8", "f7f8"], content_type: "tactic",
      repertoire_name: "Tactics", repertoire_source: "Lichess", cycle: 0,
    };
    const third = {
      id: "third-overlap", queue_entry_id: 903, start_fen: new Chess().fen(),
      moves: ["d2d4"], content_type: "opening", repertoire_name: "Third",
      repertoire_source: "PGN", cycle: 0,
    };
    let finishFirstReview: ((response: Response) => void) | undefined;
    const savedEntries: number[] = [];
    vi.stubGlobal("fetch", vi.fn((input, options) => {
      const url = String(input);
      if (url.includes("/api/queue/window"))
        return Promise.resolve(Response.json({ cards: [first, tactic, third], count: 3 }));
      if (url.endsWith("/review")) {
        const queueEntryId = JSON.parse(options.body).queue_entry_id as number;
        savedEntries.push(queueEntryId);
        if (queueEntryId === 901)
          return new Promise<Response>((resolve) => { finishFirstReview = resolve; });
        return Promise.resolve(Response.json({ persisted: true }));
      }
      return Promise.resolve(Response.json({ providers: [], states: [], lines: [] }));
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Correct" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Correct" }));
    await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(startingFen));
    vi.useFakeTimers();
    fireEvent.click(screen.getByText("a2e6"));
    await pause(430);
    fireEvent.click(screen.getByText("f7f8"));
    expect(pendingReviews().map((review) => review.queueEntryId)).toEqual([901, 902]);
    await pause(751);
    vi.useRealTimers();
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(903);
    finishFirstReview?.(Response.json({ persisted: true }));
    await waitFor(() => expect(pendingReviews()).toHaveLength(0));
    expect(savedEntries).toEqual([901, 902]);
  });
  it("failed earlier save blocks grading after a completed tactic until ordered retry succeeds", async () => {
    const first = {
      id: "first-retry-overlap", queue_entry_id: 911, start_fen: new Chess().fen(),
      moves: ["e2e4"], content_type: "opening", repertoire_name: "First",
      repertoire_source: "PGN", cycle: 0,
    };
    const tactic = {
      id: "mate-retry-overlap", queue_entry_id: 912, start_fen: startingFen,
      moves: ["a2e6", "d7d8", "f7f8"], content_type: "tactic",
      repertoire_name: "Tactics", repertoire_source: "Lichess", cycle: 0,
    };
    const third = {
      id: "third-retry-overlap", queue_entry_id: 913, start_fen: new Chess().fen(),
      moves: ["d2d4"], content_type: "opening", repertoire_name: "Third",
      repertoire_source: "PGN", cycle: 0,
    };
    let finishFirstReview: ((response: Response) => void) | undefined;
    const savedEntries: number[] = [];
    vi.stubGlobal("fetch", vi.fn((input, options) => {
      const url = String(input);
      if (url.includes("/api/queue/window"))
        return Promise.resolve(Response.json({ cards: [first, tactic, third], count: 3 }));
      if (url.endsWith("/review")) {
        const queueEntryId = JSON.parse(options.body).queue_entry_id as number;
        savedEntries.push(queueEntryId);
        if (queueEntryId === 911 && savedEntries.length === 1)
          return new Promise<Response>((resolve) => { finishFirstReview = resolve; });
        return Promise.resolve(Response.json({ persisted: true }));
      }
      return Promise.resolve(Response.json({ providers: [], states: [], lines: [] }));
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Correct" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Correct" }));
    await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(startingFen));
    vi.useFakeTimers();
    fireEvent.click(screen.getByText("a2e6"));
    await pause(430);
    fireEvent.click(screen.getByText("f7f8"));
    await pause(751);
    vi.useRealTimers();
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(913);
    finishFirstReview?.(Response.json({ detail: "Database busy" }, { status: 503 }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry save" })).toBeTruthy());
    expect(screen.getByText("e2e4").closest("button")?.disabled).toBe(true);
    expect(pendingReviews().map((review) => review.queueEntryId)).toEqual([911, 912]);
    fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
    await waitFor(() => expect(pendingReviews()).toHaveLength(0));
    expect(savedEntries).toEqual([911, 911, 912]);
  });
  it("completed tactic survives queue reconciliation before its feedback timer grades it", async () => {
    const tactic = {
      id: "mate-refresh", queue_entry_id: 811, start_fen: startingFen,
      moves: ["a2e6", "d7d8", "f7f8"], content_type: "tactic",
      repertoire_name: "Tactics", repertoire_source: "Lichess", cycle: 0,
    };
    const next = {
      id: "next-opening", queue_entry_id: 812, start_fen: new Chess().fen(),
      moves: ["e2e4"], content_type: "opening", repertoire_name: "Next",
      repertoire_source: "PGN", cycle: 0,
    };
    let queue = [tactic, next];
    const reviews: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input, options) => {
      const url = String(input);
      if (url.includes("/api/queue/window")) return Response.json({ cards: queue, count: queue.length });
      if (url.endsWith("/review")) {
        reviews.push(JSON.parse(options.body));
        queue = [next];
        return Response.json({ persisted: true });
      }
      return Response.json({ providers: [], states: [], lines: [] });
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(startingFen));
    fireEvent.click(screen.getByText("a2e6"));
    await pause(430);
    fireEvent.click(screen.getByText("f7f8"));
    const finalFen = screen.getByTestId("board").getAttribute("data-fen");
    const completedToken = useTrainingStore.getState().attempt;
    expect(new Chess(finalFen!).isCheckmate()).toBe(true);
    expect(pendingReviews()).toHaveLength(1);
    await act(async () => { await fetchAndInitializeQueue(); });
    expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(finalFen);
    expect(useTrainingStore.getState().attempt).toEqual(completedToken);
    await pause(751);
    await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(812));
    expect(reviews).toHaveLength(1);
    expect(pendingReviews()).toHaveLength(0);
  });

  it("reload during completed tactic feedback replays its durable result once", async () => {
    const tactic = {
      id: "mate-reload", queue_entry_id: 821, start_fen: startingFen,
      moves: ["a2e6", "d7d8", "f7f8"], content_type: "tactic",
      repertoire_name: "Tactics", repertoire_source: "Lichess", cycle: 0,
    };
    let queue = [tactic];
    let reviewCount = 0;
    vi.stubGlobal("fetch", vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/api/queue/window")) return Response.json({ cards: queue, count: queue.length });
      if (url.endsWith("/review")) {
        reviewCount += 1;
        queue = [];
        return Response.json({ persisted: true });
      }
      return Response.json({ providers: [], states: [], lines: [] });
    }));
    const mounted = render(<Home />);
    await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(startingFen));
    vi.useFakeTimers();
    fireEvent.click(screen.getByText("a2e6"));
    await pause(430);
    fireEvent.click(screen.getByText("f7f8"));
    expect(pendingReviews()).toHaveLength(1);
    mounted.unmount();
    vi.useRealTimers();
    render(<Home />);
    await waitFor(() => expect(reviewCount).toBe(1));
    expect(pendingReviews()).toHaveLength(0);
    vi.useFakeTimers();
    await pause(751);
    vi.useRealTimers();
    expect(reviewCount).toBe(1);
  });

  it("failed tactic review save keeps the next card visible but blocks grading until retry", async () => {
    const tactic = {
      id: "mate-save-failure", queue_entry_id: 831, start_fen: startingFen,
      moves: ["a2e6", "d7d8", "f7f8"], content_type: "tactic",
      repertoire_name: "Tactics", repertoire_source: "Lichess", cycle: 0,
    };
    const next = {
      id: "next-after-failure", queue_entry_id: 832, start_fen: new Chess().fen(),
      moves: ["e2e4"], content_type: "opening", repertoire_name: "Next",
      repertoire_source: "PGN", cycle: 0,
    };
    let reviews = 0;
    vi.stubGlobal("fetch", vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/api/queue/window")) return Response.json({ cards: [tactic, next], count: 2 });
      if (url.endsWith("/review")) {
        reviews += 1;
        return reviews === 1
          ? Response.json({ detail: "Database busy" }, { status: 503 })
          : Response.json({ persisted: true });
      }
      return Response.json({ providers: [], states: [], lines: [] });
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(startingFen));
    vi.useFakeTimers();
    fireEvent.click(screen.getByText("a2e6"));
    await pause(430);
    fireEvent.click(screen.getByText("f7f8"));
    await pause(751);
    vi.useRealTimers();
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry save" })).toBeTruthy());
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(832);
    expect(screen.getByText("e2e4").closest("button")?.disabled).toBe(true);
    expect(pendingReviews()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
    await waitFor(() => expect(pendingReviews()).toHaveLength(0));
    expect(reviews).toBe(2);
  });

  it("failed next-card read leaves a completed tactic on its final board for retry", async () => {
    const tactic = {
      id: "mate-queue-failure", queue_entry_id: 841, start_fen: startingFen,
      moves: ["a2e6", "d7d8", "f7f8"], content_type: "tactic",
      repertoire_name: "Tactics", repertoire_source: "Lichess", cycle: 0,
    };
    let saved = false;
    vi.stubGlobal("fetch", vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/api/queue/window"))
        return saved
          ? Response.json({ detail: "Queue read failed" }, { status: 500 })
          : Response.json({ cards: [tactic], count: 1 });
      if (url.endsWith("/review")) {
        saved = true;
        return Response.json({ persisted: true });
      }
      return Response.json({ providers: [], states: [], lines: [] });
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(startingFen));
    vi.useFakeTimers();
    fireEvent.click(screen.getByText("a2e6"));
    await pause(430);
    fireEvent.click(screen.getByText("f7f8"));
    const finalFen = screen.getByTestId("board").getAttribute("data-fen");
    await pause(751);
    vi.useRealTimers();
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry loading the queue" })).toBeTruthy());
    expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(finalFen);
    expect(pendingReviews()).toHaveLength(0);
  });
  it("shows the prefetched next training card while the prior review request is still pending", async () => {
    const queueCard = (entryId: number, title: string) => ({
      id: `card-${entryId}`, queue_entry_id: entryId, start_fen: new Chess().fen(),
      moves: ["e2e4"], content_type: "opening", repertoire_name: title,
      repertoire_source: "PGN", attempt_state: "clean",
    });
    let finishReview: ((response: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn((input) => {
      const url = String(input);
      if (url.includes("/api/queue/window"))
        return Promise.resolve(Response.json({ cards: [queueCard(901, "First prep"), queueCard(902, "Next prep")], count: 2 }));
      if (url.endsWith("/review"))
        return new Promise<Response>((resolve) => { finishReview = resolve; });
      return Promise.resolve(Response.json(url.endsWith("/teaching") ? { states: [] } :
        url.endsWith("/sync-status") ? { providers: [] } : { lines: [] }));
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Correct" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Correct" }));
    await waitFor(() => expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(902));
    expect(finishReview).toBeTypeOf("function");
    finishReview?.(Response.json({ persisted: true }));
  });
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
        if (url.includes("/api/queue/window")) {
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
        String(input).includes("/api/queue/window")
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
  it("previously studied game miss without a clean pass tests recall without arrows", async () => {
    const teachingPosts: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input, options) => {
      const url = String(input);
      if (url.includes("/api/queue/window")) return Response.json({ cards: [{
        id: "game-miss", queue_entry_id: 81, start_fen: new Chess().fen(),
        moves: ["e2e4"], content_type: "opening", repertoire_name: "Prep",
        repertoire_source: "PGN", first_correct_at: null, has_study_review: 1,
        gameplay_priority_reason: "Priority review · missed in a recent game",
      }] });
      if (url.endsWith("/teaching") && options?.method === "POST") teachingPosts.push(url);
      return Response.json(url.endsWith("/teaching") ? { states: [] } :
        url.endsWith("/sync-status") ? { providers: [] } : { lines: [] });
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Priority review · missed in a recent game")).toBeTruthy());
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    fireEvent.click(screen.getByText("e2e4"));
    expect(pendingReviews()[0]).toMatchObject({ outcome: "correct", guided: false });
    expect(teachingPosts).toEqual([]);
  });
  it("unseen game-miss priority introduction teaches once and returns for unassisted recall", async () => {
    let firstAttemptComplete = false;
    const reviewRequests: Array<{ outcome: string; guided: boolean }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input, options) => {
      const url = String(input);
      if (url.includes("/api/queue/window")) return Response.json({ cards: [{
        id: "new-opening", queue_entry_id: firstAttemptComplete ? 83 : 82,
        start_fen: new Chess().fen(), moves: ["e2e4"], content_type: "opening",
        repertoire_name: "Prep", repertoire_source: "PGN",
        has_study_review: firstAttemptComplete ? 1 : 0,
        gameplay_priority_reason: firstAttemptComplete ? null : "Priority review · missed in a recent game",
        attempt_state: firstAttemptComplete ? "guided" : "clean",
      }] });
      if (url.endsWith("/review")) {
        reviewRequests.push(JSON.parse(String(options?.body)));
        firstAttemptComplete = true;
        return Response.json({ persisted: true });
      }
      return Response.json(url.endsWith("/teaching") ? { states: [] } :
        url.endsWith("/sync-status") ? { providers: [] } : { lines: [] });
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("true"));
    expect(screen.getByText("Priority review · missed in a recent game")).toBeTruthy();
    fireEvent.click(screen.getByText("e2e4"));
    await waitFor(() => expect(reviewRequests).toHaveLength(1), { timeout: 2_000 });
    expect(reviewRequests[0]).toMatchObject({ guided: true });
    await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(83));
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
  });
  it("Show move during initial teaching records failure and keeps required guidance", async () => {
    const failures: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input, options) => {
        const url = String(input);
        if (url.includes("/api/queue/window"))
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
  it("reopening a saved guided card without input sends no failure and shows no red X", async () => {
    const failedRequests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input, options) => {
      const url = String(input);
      if (url.includes("/api/queue/window"))
        return Response.json({ cards: [{
          id: "resumed-card", queue_entry_id: 1974, start_fen: new Chess().fen(),
          moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening",
          repertoire_name: "Resumed", repertoire_source: "PGN", attempt_failed: true,
        }], count: 1 });
      if (options?.method === "POST" && url.endsWith("/fail")) failedRequests.push(url);
      return Response.json(url.endsWith("/teaching") ? { states: [] } : { lines: [] });
    }));
    render(<Home />);
    await waitFor(() => expect(screen.getByText("Resumed")).toBeTruthy());
    expect(screen.queryByText("×")).toBeNull();
    expect(failedRequests).toEqual([]);
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
        if (String(input).includes("/api/queue/window"))
          return Response.json({ cards: queue });
        if (String(input).endsWith("/review")) {
          queue = [
            { ...queue[0], queue_entry_id: 2, attempt_state: "reinforcement" },
          ];
          return Response.json({ persisted: true });
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
        if (String(input).includes("/api/queue/window"))
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
    vi.useFakeTimers();
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
    vi.useRealTimers();
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
    vi.useFakeTimers();
    await pause();
    vi.useRealTimers();
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
    vi.useFakeTimers();
    await pause();
    vi.useRealTimers();
    await waitFor(() =>
      expect(screen.getByText("Puzzle 2 of 25")).toBeTruthy(),
    );
  });
  it("tactic setup is applied and the final mate remains during feedback", async () => {
    mockTactics();
    await readyTactics();
    vi.useFakeTimers();
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
    vi.useRealTimers();
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
    vi.useFakeTimers();
    await pause();
    vi.useRealTimers();
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
        if (String(input).includes("/api/queue/window"))
          return Response.json({ cards: queue });
        if (String(input).endsWith("/review")) {
          reviews.push(JSON.parse(options.body));
          queue =
            reviews.length === 1
              ? [{ ...queue[0], queue_entry_id: 2, cycle: 1 }]
              : [];
          return Response.json({ persisted: true });
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
    vi.useFakeTimers();
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
    vi.useRealTimers();
    await waitFor(() =>
      expect(useTrainingStore.getState().attempt.phase).toBe("playerTurn"),
    );
    expect(screen.getByTestId("board").getAttribute("data-hint")).toBe("false");
    vi.useFakeTimers();
    fireEvent.click(screen.getByText("a2e6"));
    await pause(430);
    fireEvent.click(screen.getByText("f7f8"));
    await pause();
    expect(reviews).toHaveLength(2);
    vi.useRealTimers();
    await waitFor(() =>
      expect(screen.getByText(/You['’]re done for today/)).toBeTruthy(),
    );
    expect(screen.queryByText(/First clean solve/)).toBeNull();
  });
});

it("repair completion preserves the active attempt focus and pending opponent reply", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/queue/window")) return Response.json({ count: 1, cards: [{
      id: "repair-continuity", queue_entry_id: 870, start_fen: new Chess().fen(),
      moves: ["e2e4", "e7e5", "g1f3"], trained_color: "white", content_type: "opening",
      repertoire_name: "Repair continuity", repertoire_source: "fixture.pgn",
    }] });
    if (url.endsWith("/repertoires")) return Response.json({ repertoires: [{ id: "rep", name: "Repair repertoire",
      source_name: "fixture.pgn", line_count: 1, card_count: 1, due_count: 1,
      integrity_status: "needs_repair", integrity_issue_count: 1, blocked_due_count: 1 }] });
    return Response.json({ providers: [], states: [], lines: [] });
  }));
  render(<Home />);
  await screen.findByRole("heading", { name: "Repair continuity" });
  await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(new Chess().fen()));
  vi.useFakeTimers();
  const moveButton = screen.getByText("e2e4"); moveButton.focus(); fireEvent.click(moveButton);
  const before = useTrainingStore.getState();
  await act(async () => { window.dispatchEvent(new CustomEvent("tempo-integrity-repair-confirmed", { detail: { repertoireId: "rep" } })); });
  const after = useTrainingStore.getState();
  expect(after.attempt).toEqual(before.attempt); expect(after.step).toBe(before.step);
  expect(after.currentFenString).toBe(before.currentFenString); expect(document.activeElement).toBe(moveButton);
  expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(430); });
  expect(useTrainingStore.getState().step).toBe(2);
});

it("late integrity count refresh cannot close an explicitly opened repair dialog or erase its choice", async () => {
  const delayedCounts: ((response: Response) => void)[] = [];
  let countReads = 0;
  const repertoires = { repertoires: [{ id: "rep", name: "Repair repertoire", source_name: "fixture.pgn",
    line_count: 1, card_count: 1, due_count: 1, integrity_status: "needs_repair", integrity_issue_count: 1, blocked_due_count: 1 }] };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/queue/window")) return Response.json({ count: 1, cards: [{
      id: "repair-count-race", queue_entry_id: 871, start_fen: new Chess().fen(), moves: ["e2e4", "e7e5"],
      trained_color: "white", content_type: "opening", repertoire_name: "Active attempt", repertoire_source: "fixture.pgn",
    }] });
    if (url.endsWith("/repertoires")) {
      if (++countReads === 1) return Response.json(repertoires);
      return new Promise<Response>(resolve => delayedCounts.push(resolve));
    }
    if (url.endsWith("/integrity")) return Response.json({ repertoire_id: "rep", status: "needs_repair", issue_count: 1,
      first_issue_id: "count-issue", scan_status: "idle", scan_generation: "scan:1", scan_progress: { completed: 1, total: 1 },
      last_scan_error: null, issues: [{ id: "count-issue", kind: "multiple_responses", signature: "count-signature",
        fen: new Chess().fen(), fen_key: new Chess().fen().split(" ").slice(0,4).join(" "), trained_color: "white", moves: [], sources: [] }] });
    return Response.json({ providers: [], states: [], lines: [] });
  }));
  render(<Home />);
  const resume = await screen.findByRole("button", { name: "Resume repair", exact: true });
  await waitFor(() => expect(delayedCounts.length).toBeGreaterThan(0));
  fireEvent.click(resume);
  const dialog = await screen.findByRole("dialog", { name: "Choose one response per position" });
  fireEvent.click(await within(dialog).findByRole("button", { name: "e2e4", exact: true }));
  await act(async () => { delayedCounts.splice(0).forEach(finish => finish(Response.json(repertoires))); });
  expect(screen.getByRole("dialog", { name: "Choose one response per position" })).toBe(dialog);
  expect(within(dialog).getByText("e2e4", { selector: "strong" })).not.toBeNull();
});
