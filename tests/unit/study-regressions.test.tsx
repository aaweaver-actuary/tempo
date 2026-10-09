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
import { flushIntegrityRepairs, pendingIntegrityRepairs } from "../../app/lib/integrity-repair-outbox";

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
      if (url.includes("/api/operations/")) return Promise.resolve(new Response(null, { status: 404 }));
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
    await waitFor(() => expect(screen.getByRole("button", { name: "Check save" })).toBeTruthy());
    expect(screen.getByText("e2e4").closest("button")?.disabled).toBe(true);
    expect(pendingReviews().map((review) => review.queueEntryId)).toEqual([911, 912]);
    fireEvent.click(screen.getByRole("button", { name: "Check save" }));
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
      if (url.includes("/api/operations/review-attempt%3A")) return reviews.length
        ? Response.json({ state: "complete", response: { persisted: true } }) : new Response(null, { status: 404 });
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
      if (url.includes("/api/operations/review-attempt%3A")) return reviewCount
        ? Response.json({ state: "complete", response: { persisted: true } }) : new Response(null, { status: 404 });
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
      if (url.includes("/api/operations/")) return new Response(null, { status: 404 });
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
    await waitFor(() => expect(screen.getByRole("button", { name: "Check save" })).toBeTruthy());
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(832);
    expect(screen.getByText("e2e4").closest("button")?.disabled).toBe(true);
    expect(pendingReviews()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Check save" }));
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
        if (url.includes("/api/operations/")) return new Response(null, { status: 404 });
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
      () => expect(screen.getByRole("button", { name: "Check save" })).toBeTruthy(),
      { timeout: 2_000 },
    );
    expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(
      queueCard.start_fen,
    );
    expect(screen.getByText(/Waiting for the computer to confirm this result/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Check save" }));
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
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
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
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<Home />);
  await screen.findByRole("heading", { name: "Repair continuity" });
  await waitFor(() => expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(new Chess().fen()));
  vi.useFakeTimers();
  const moveButton = screen.getByText("e2e4"); moveButton.focus(); fireEvent.click(moveButton);
  const before = useTrainingStore.getState();
  const activeCardBefore = before.practiceCards[before.activeCardIndex];
  const queueReadsBefore = fetchMock.mock.calls.filter(([input]) => String(input).includes("/api/queue/window")).length;
  await act(async () => { window.dispatchEvent(new CustomEvent("tempo-integrity-repair-confirmed", { detail: { repertoireId: "rep" } })); });
  const after = useTrainingStore.getState();
  expect(after.attempt).toEqual(before.attempt); expect(after.step).toBe(before.step);
  expect(after.currentFenString).toBe(before.currentFenString); expect(document.activeElement).toBe(moveButton);
  expect(after.practiceCards[after.activeCardIndex]).toBe(activeCardBefore);
  expect(after.practiceCards[after.activeCardIndex].queueEntryId).toBe(activeCardBefore.queueEntryId);
  expect(fetchMock.mock.calls.filter(([input]) => String(input).includes("/api/queue/window"))).toHaveLength(queueReadsBefore);
  expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(430); });
  expect(useTrainingStore.getState().step).toBe(2);
});

it("final repair reconciliation immediately loads newly unblocked cards from an empty training queue", async () => {
  const repairIssue = { id: "final-conflict", kind: "multiple_responses", signature: "final-signature",
    fen: new Chess().fen(), fen_key: new Chess().fen().split(" ").slice(0, 4).join(" "),
    trained_color: "white", moves: [], sources: [{ type: "line", id: "blocked-source" }] };
  const unblockedCard = { id: "finally-playable", queue_entry_id: 874, start_fen: new Chess().fen(),
    moves: ["e2e4", "e7e5", "g1f3"], trained_color: "white", content_type: "opening",
    repertoire_name: "Unblocked study", repertoire_source: "fixture.pgn" };
  const submission = { task_id: "final-repair-graph", repertoire_id: "rep", issue_id: repairIssue.id, state: "queued" };
  let submissionConfirmed = false;
  let repairPublished = false;
  let completeQueueRead: ((response: Response) => void) | undefined;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/queue/window")) {
      if (!repairPublished) return Response.json({ count: 0, cards: [] });
      return new Promise<Response>(resolve => { completeQueueRead = resolve; });
    }
    if (url.endsWith("/repertoires")) return Response.json({ repertoires: [{ id: "rep", name: "Repair repertoire",
      source_name: "fixture.pgn", line_count: 1, card_count: 1, due_count: 1,
      graph_state: "ready", graph_generation: repairPublished ? 2 : 1,
      integrity_status: repairPublished ? "clean" : "needs_repair", integrity_issue_count: repairPublished ? 0 : 1,
      blocked_due_count: repairPublished ? 0 : 1 }] });
    if (url.endsWith("/integrity")) return Response.json({ repertoire_id: "rep",
      status: repairPublished ? "clean" : "needs_repair", issue_count: repairPublished ? 0 : 1,
      first_issue_id: repairPublished ? null : repairIssue.id, scan_status: "idle", scan_generation: "scan:2",
      scan_progress: { completed: 1, total: 1 }, last_scan_error: null, issues: repairPublished ? [] : [repairIssue] });
    if (url.endsWith("/final-conflict/resolve") && init?.method === "POST") {
      submissionConfirmed = true;
      return Response.json(submission);
    }
    if (url.includes("/api/operations/")) return Response.json(submissionConfirmed
      ? { state: "complete", response: submission } : { state: "unknown" });
    if (url.endsWith("/system/tasks")) return Response.json({ tasks: [{ id: submission.task_id,
      kind: "opening_graph_rebuild", deduplication_key: "rep", generation: 2, state: repairPublished ? "complete" : "queued" }] });
    return Response.json({ providers: [], states: [], lines: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<Home />);
  await screen.findByText(/You['’]re done for today/);
  await waitFor(() => expect(useTrainingStore.getState().queueReadiness).toBe("ready"));
  expect(useTrainingStore.getState().isDatabaseQueueActive).toBe(true);
  expect(useTrainingStore.getState().cardsLeft).toBe(0);
  expect(useTrainingStore.getState().practiceCards).toEqual([]);
  fireEvent.click(await screen.findByRole("button", { name: "Resume repair" }));
  const dialog = await screen.findByRole("dialog", { name: "Choose one response per position" });
  await within(dialog).findByText(/line blocked-source/);
  fireEvent.click(within(dialog).getByRole("button", { name: "e2e4" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Keep this response" }));
  await within(dialog).findByText(/All choices queued/);
  fireEvent.click(within(dialog).getByRole("button", { name: "Defer repertoire repair" }));
  vi.useFakeTimers();
  await act(async () => { await flushIntegrityRepairs(); });
  expect(pendingIntegrityRepairs()).toHaveLength(1);
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ issueId: repairIssue.id, phase: "validating", taskId: submission.task_id });
  const queueReadsBeforeCompletion = fetchMock.mock.calls.filter(([input]) => String(input).includes("/api/queue/window")).length;
  repairPublished = true;
  await act(async () => { await flushIntegrityRepairs(); });
  expect(pendingIntegrityRepairs()).toHaveLength(0);
  expect(fetchMock.mock.calls.filter(([input]) => String(input).includes("/api/queue/window")))
    .toHaveLength(queueReadsBeforeCompletion + 1);
  expect(completeQueueRead).toBeTypeOf("function");
  // The read is already proved with refresh timers frozen. Let the real queue
  // worker fallback yield while hydrating the explicitly released response.
  vi.useRealTimers();
  await act(async () => { completeQueueRead!(Response.json({ count: 1, cards: [unblockedCard] })); });
  await waitFor(() => expect(useTrainingStore.getState().queueReadiness).toBe("ready"));
  const playableState = useTrainingStore.getState();
  expect(playableState.cardsLeft).toBe(1);
  expect(playableState.practiceCards[playableState.activeCardIndex]).toMatchObject({ backendId: unblockedCard.id, queueEntryId: 874 });
  expect(playableState.attempt.phase).toBe("playerTurn");
  expect(screen.getByRole("heading", { name: "Unblocked study" })).toBeTruthy();
  expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(new Chess().fen());
  expect(screen.queryByText(/You['’]re done for today/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Resume repair" })).toBeNull();
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
  const resume = await screen.findByRole("button", { name: "Resume repair" });
  await waitFor(() => expect(delayedCounts.length).toBeGreaterThan(0));
  fireEvent.click(resume);
  const dialog = await screen.findByRole("dialog", { name: "Choose one response per position" });
  fireEvent.click(await within(dialog).findByRole("button", { name: "e2e4" }));
  await act(async () => { delayedCounts.splice(0).forEach(finish => finish(Response.json(repertoires))); });
  expect(screen.getByRole("dialog", { name: "Choose one response per position" })).toBe(dialog);
  expect(within(dialog).getByText("e2e4", { selector: "strong" })).not.toBeNull();
});

type IntegrityOrderingRepertoire = {
  id: string;
  name: string;
  source_name: string;
  line_count: number;
  card_count: number;
  due_count: number;
  graph_state: "ready";
  graph_generation: number;
  integrity_status: "needs_repair" | "clean";
  integrity_issue_count: number;
  blocked_due_count: number;
};

function integrityOrderingRepertoire(id: string, issueCount: number, blockedDue = issueCount * 3): IntegrityOrderingRepertoire {
  return {
    id, name: `Repair ${id}`, source_name: "fixture.pgn", line_count: 2,
    card_count: 12, due_count: 12, graph_state: "ready", graph_generation: 2,
    integrity_status: issueCount ? "needs_repair" : "clean",
    integrity_issue_count: issueCount, blocked_due_count: blockedDue,
  };
}

async function renderIntegrityOrderingHome(initialRepertoires = [integrityOrderingRepertoire("rep", 2)]) {
  const integrityReads: {
    passive: boolean;
    resolve: (response: Response) => void;
    reject: (error: Error) => void;
  }[] = [];
  const queueResponses: ((response: Response) => void)[] = [];
  const integrityEvidenceReads: string[] = [];
  let holdIntegrityReads = false;
  let holdQueueReads = false;
  let initialIntegrityReads = 0;
  const queuePayload = { count: 1, cards: [{
    id: "integrity-ordering-card", queue_entry_id: 874, start_fen: new Chess().fen(),
    moves: ["e2e4", "e7e5", "g1f3"], trained_color: "white", content_type: "opening",
    repertoire_id: "rep", repertoire_name: "Integrity ordering study", repertoire_source: "fixture.pgn", revision: 1,
  }] };
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    if (url.includes("/api/queue/window")) {
      if (holdQueueReads) return new Promise<Response>(resolve => queueResponses.push(resolve));
      return Promise.resolve(Response.json(queuePayload));
    }
    if (url.endsWith("/repertoires")) {
      if (!holdIntegrityReads) {
        initialIntegrityReads += 1;
        return Promise.resolve(Response.json({ repertoires: initialRepertoires }));
      }
      return new Promise<Response>((resolve, reject) => integrityReads.push({
        passive: new Headers(init?.headers).get("X-Tempo-Work-Class") === "background", resolve, reject,
      }));
    }
    const integrityMatch = url.match(/\/api\/repertoires\/([^/]+)\/integrity$/);
    if (integrityMatch) {
      const repertoireId = decodeURIComponent(integrityMatch[1]);
      integrityEvidenceReads.push(repertoireId);
      return Promise.resolve(Response.json({ repertoire_id: repertoireId, status: "needs_repair", issue_count: 1,
        first_issue_id: `${repertoireId}-ordering-issue`, scan_status: "idle", scan_generation: "scan:2",
        scan_progress: { completed: 2, total: 2 }, last_scan_error: null,
        issues: [{ id: `${repertoireId}-ordering-issue`, kind: "multiple_responses", signature: `${repertoireId}-ordering-signature`,
          fen: new Chess().fen(), fen_key: new Chess().fen().split(" ").slice(0, 4).join(" "),
          trained_color: "white", moves: [], sources: [{ type: "line", id: `${repertoireId}-ordering-source` }] }] }));
    }
    if (url.endsWith("/api/cards/integrity-ordering-card") && init?.method === "PUT")
      return Promise.resolve(Response.json({ card_id: "integrity-ordering-card", replaced: false, history_mode: "preserve", revision: 2 }));
    if (url.endsWith("/api/repertoire/lines")) return Promise.resolve(Response.json({ lines: [] }));
    return Promise.resolve(Response.json({ providers: [], states: [], lines: [], moves: [] }));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<Home />);
  await screen.findByRole("heading", { name: "Integrity ordering study" });
  const queueReadCount = () => fetchMock.mock.calls.filter(([input]) => String(input).includes("/api/queue/window")).length;
  // The startup check and both queue refreshes must finish before requests are held.
  await waitFor(() => {
    expect(queueReadCount()).toBeGreaterThanOrEqual(2);
    expect(initialIntegrityReads).toBe(queueReadCount() + 1);
    expect(useTrainingStore.getState().queueReadiness).toBe("ready");
    expect(screen.getByRole("button", { name: "Resume repair" })).not.toBeNull();
  });
  holdIntegrityReads = true;
  const studyBoard = screen.getByTestId("board");
  const studyMove = within(studyBoard).getByRole("button", { name: "e2e4" });
  studyMove.focus();
  return {
    fetchMock, integrityReads, integrityEvidenceReads, queueResponses, queuePayload,
    queueReadCount, studyBoard, studyMove,
    holdQueueReads: () => { holdQueueReads = true; },
    resolveIntegrity: async (index: number, repertoires: IntegrityOrderingRepertoire[]) => {
      expect(integrityReads[index]).toBeDefined();
      await act(async () => { integrityReads[index].resolve(Response.json({ repertoires })); });
    },
  };
}

type IntegrityOrderingHome = Awaited<ReturnType<typeof renderIntegrityOrderingHome>>;

function captureIntegrityOrderingStudy(home: IntegrityOrderingHome) {
  const training = useTrainingStore.getState();
  return {
    attempt: structuredClone(training.attempt), queueEntryId: training.getCard().queueEntryId,
    fen: training.currentFenString, step: training.step,
    boardFen: home.studyBoard.getAttribute("data-fen"), queueReads: home.queueReadCount(),
  };
}

function expectIntegrityOrderingStudy(home: IntegrityOrderingHome, before: ReturnType<typeof captureIntegrityOrderingStudy>) {
  const training = useTrainingStore.getState();
  expect(training.attempt).toEqual(before.attempt);
  expect(training.getCard().queueEntryId).toBe(before.queueEntryId);
  expect(training.currentFenString).toBe(before.fen);
  expect(training.step).toBe(before.step);
  expect(home.studyBoard.getAttribute("data-fen")).toBe(before.boardFen);
  expect(home.queueReadCount()).toBe(before.queueReads);
}

function expectIntegrityOrderingCounts(issueCount: number, blockedDue: number) {
  const notice = screen.getByRole("button", { name: "Resume repair" }).closest('[role="status"]')!;
  expect(notice.textContent).toContain(`${blockedDue} opening card${blockedDue === 1 ? "" : "s"} paused by repertoire repair.`);
  expect(notice.textContent).toContain(`${issueCount} issue${issueCount === 1 ? "" : "s"} remaining.`);
}

async function requestIntegrityOrderingRefresh(kind: "generic" | "passive", repertoireId?: string) {
  await act(async () => {
    window.dispatchEvent(kind === "passive"
      ? new CustomEvent("tempo-integrity-repair-confirmed", { detail: { repertoireId: repertoireId ?? "rep" } })
      : new CustomEvent("tempo:integrity", { detail: repertoireId ? { repertoireId } : undefined }));
  });
}

async function expectIntegrityOrderingDialog(repertoireId: string) {
  const dialog = screen.queryByRole("dialog", { name: "Choose one response per position" });
  expect(dialog).not.toBeNull();
  await within(dialog!).findByText(`Affected sources: line ${repertoireId}-ordering-source`);
  return dialog!;
}

describe("integrity request intent ordering regressions", () => {
  for (const refreshKind of ["generic", "passive"] as const) {
    for (const completionOrder of ["latest first", "older first"] as const) {
      it(`${refreshKind === "generic"
        ? "newer generic integrity refresh preserves an earlier preferred repair-open intent"
        : "newer passive reconciliation preserves a current preferred repair-open intent"} (${completionOrder})`, async () => {
        const home = await renderIntegrityOrderingHome();
        const before = captureIntegrityOrderingStudy(home);
        await requestIntegrityOrderingRefresh("generic", "rep");
        await requestIntegrityOrderingRefresh(refreshKind);
        expect(home.integrityReads.map(read => read.passive)).toEqual([false, refreshKind === "passive"]);
        if (completionOrder === "older first") {
          await home.resolveIntegrity(0, [integrityOrderingRepertoire("rep", 5, 11)]);
          expectIntegrityOrderingCounts(5, 11);
          await expectIntegrityOrderingDialog("rep");
          expectIntegrityOrderingStudy(home, before);
        }
        await home.resolveIntegrity(1, [integrityOrderingRepertoire("rep", 1, 3)]);
        expectIntegrityOrderingCounts(1, 3);
        const dialog = await expectIntegrityOrderingDialog("rep");
        fireEvent.click(within(dialog).getByRole("button", { name: "e2e4" }));
        if (completionOrder === "latest first")
          await home.resolveIntegrity(0, [integrityOrderingRepertoire("rep", 5, 11)]);
        expectIntegrityOrderingCounts(1, 3);
        expect(screen.getByRole("dialog", { name: "Choose one response per position" })).toBe(dialog);
        expect(within(dialog).getByText("e2e4", { selector: "strong" })).not.toBeNull();
        expect(home.integrityEvidenceReads).toEqual(["rep"]);
        expectIntegrityOrderingStudy(home, before);
      });
    }
  }

  for (const preferredState of ["clean", "removed"] as const) {
    for (const refreshKind of ["generic", "passive"] as const) {
      for (const completionOrder of ["latest first", "older first"] as const) {
        it(`${completionOrder === "older first"
          ? "newer clean snapshot preserves a repair dialog opened by an earlier accepted snapshot"
          : "clean authoritative reconciliation retires obsolete preferred repair intent"} (${preferredState}, ${refreshKind}, ${completionOrder})`, async () => {
          const home = await renderIntegrityOrderingHome();
          const before = captureIntegrityOrderingStudy(home);
          await requestIntegrityOrderingRefresh("generic", "rep");
          await requestIntegrityOrderingRefresh(refreshKind);
          if (completionOrder === "older first") {
            await home.resolveIntegrity(0, [integrityOrderingRepertoire("rep", 5, 11)]);
            expectIntegrityOrderingCounts(5, 11);
            const dialog = await expectIntegrityOrderingDialog("rep");
            fireEvent.click(within(dialog).getByRole("button", { name: "e2e4" }));
          }
          const latest = [integrityOrderingRepertoire("other", 1, 4)];
          if (preferredState === "clean") latest.unshift(integrityOrderingRepertoire("rep", 0));
          await home.resolveIntegrity(1, latest);
          expectIntegrityOrderingCounts(1, 4);
          if (completionOrder === "latest first")
            await home.resolveIntegrity(0, [integrityOrderingRepertoire("rep", 5, 11)]);
          expectIntegrityOrderingCounts(1, 4);
          if (completionOrder === "older first") {
            const dialog = await expectIntegrityOrderingDialog("rep");
            expect(within(dialog).getByText("e2e4", { selector: "strong" })).not.toBeNull();
            fireEvent.click(within(dialog).getByRole("button", { name: "Defer repertoire repair" }));
          }
          expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
          expect(home.integrityEvidenceReads).toEqual(completionOrder === "older first" ? ["rep"] : []);
          expect(document.activeElement).toBe(home.studyMove);
          expectIntegrityOrderingStudy(home, before);
          // A later needs-repair snapshot must not revive the retired intent.
          await requestIntegrityOrderingRefresh("passive");
          await home.resolveIntegrity(2, [integrityOrderingRepertoire("other", 1, 4), integrityOrderingRepertoire("rep", 1, 3)]);
          expectIntegrityOrderingCounts(2, 7);
          expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
          expect(document.activeElement).toBe(home.studyMove);
          fireEvent.click(screen.getByRole("button", { name: "Resume repair" }));
          await expectIntegrityOrderingDialog("other");
          expect(home.integrityEvidenceReads).toEqual(completionOrder === "older first" ? ["rep", "other"] : ["other"]);
          expectIntegrityOrderingStudy(home, before);
        });
      }
    }
  }

  for (const trailingRefresh of ["none", "generic", "passive"] as const) {
    for (const completionOrder of ["latest first", "older first"] as const) {
      it(`newer preferred repertoire intent cannot be replaced by an older preferred request (${trailingRefresh}, ${completionOrder})`, async () => {
        const home = await renderIntegrityOrderingHome();
        const before = captureIntegrityOrderingStudy(home);
        await requestIntegrityOrderingRefresh("generic", "rep");
        await requestIntegrityOrderingRefresh("generic", "other");
        if (trailingRefresh !== "none") await requestIntegrityOrderingRefresh(trailingRefresh);
        const latestIndex = trailingRefresh === "none" ? 1 : 2;
        if (completionOrder === "older first") {
          await home.resolveIntegrity(0, [integrityOrderingRepertoire("rep", 5, 11)]);
          expectIntegrityOrderingCounts(5, 11);
          expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
          expect(document.activeElement).toBe(home.studyMove);
          if (latestIndex === 2) {
            await home.resolveIntegrity(1, [integrityOrderingRepertoire("other", 5, 10)]);
            expectIntegrityOrderingCounts(5, 10);
            await expectIntegrityOrderingDialog("other");
          }
        }
        await home.resolveIntegrity(latestIndex, [integrityOrderingRepertoire("rep", 1, 3), integrityOrderingRepertoire("other", 2, 4)]);
        expectIntegrityOrderingCounts(3, 7);
        const dialog = await expectIntegrityOrderingDialog("other");
        fireEvent.click(within(dialog).getByRole("button", { name: "e2e4" }));
        if (completionOrder === "latest first") {
          if (latestIndex === 2) await home.resolveIntegrity(1, [integrityOrderingRepertoire("other", 5, 10)]);
          await home.resolveIntegrity(0, [integrityOrderingRepertoire("rep", 5, 11)]);
        }
        expectIntegrityOrderingCounts(3, 7);
        expect(screen.getByRole("dialog", { name: "Choose one response per position" })).toBe(dialog);
        expect(within(dialog).getByText("e2e4", { selector: "strong" })).not.toBeNull();
        expect(home.integrityEvidenceReads).toEqual(["other"]);
        expectIntegrityOrderingStudy(home, before);
      });
    }
  }

  for (const intentTiming of ["before manual open", "after manual open"] as const) {
    for (const preferredStillNeedsRepair of [true, false]) {
      it(`preferred reconciliation preserves an explicitly opened dialog and selected response (${intentTiming}, ${preferredStillNeedsRepair ? "needs repair" : "clean"})`, async () => {
        const home = await renderIntegrityOrderingHome([integrityOrderingRepertoire("rep", 2), integrityOrderingRepertoire("other", 1, 4)]);
        const before = captureIntegrityOrderingStudy(home);
        if (intentTiming === "before manual open") await requestIntegrityOrderingRefresh("generic", "other");
        fireEvent.click(screen.getByRole("button", { name: "Resume repair" }));
        const dialog = await expectIntegrityOrderingDialog("rep");
        fireEvent.click(within(dialog).getByRole("button", { name: "e2e4" }));
        if (intentTiming === "after manual open") await requestIntegrityOrderingRefresh("generic", "other");
        await home.resolveIntegrity(0, [integrityOrderingRepertoire("rep", 1), integrityOrderingRepertoire("other", preferredStillNeedsRepair ? 1 : 0, preferredStillNeedsRepair ? 4 : 0)]);
        expectIntegrityOrderingCounts(preferredStillNeedsRepair ? 2 : 1, preferredStillNeedsRepair ? 7 : 3);
        expect(screen.getByRole("dialog", { name: "Choose one response per position" })).toBe(dialog);
        expect(within(dialog).getByText("e2e4", { selector: "strong" })).not.toBeNull();
        expect(within(dialog).getByText("Affected sources: line rep-ordering-source")).not.toBeNull();
        expect(home.integrityEvidenceReads).toEqual(["rep"]);
        expectIntegrityOrderingStudy(home, before);
      });
    }
  }

  for (const pendingRepertoireId of ["rep", "other"]) {
    it(`deferring an explicitly opened dialog cancels pending preferred intent until a fresh event (${pendingRepertoireId})`, async () => {
      const home = await renderIntegrityOrderingHome([integrityOrderingRepertoire("rep", 2), integrityOrderingRepertoire("other", 1, 4)]);
      const before = captureIntegrityOrderingStudy(home);
      fireEvent.click(screen.getByRole("button", { name: "Resume repair" }));
      const dialog = await expectIntegrityOrderingDialog("rep");
      fireEvent.click(within(dialog).getByRole("button", { name: "e2e4" }));
      await requestIntegrityOrderingRefresh("generic", pendingRepertoireId);
      fireEvent.click(within(dialog).getByRole("button", { name: "Defer repertoire repair" }));
      expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
      await home.resolveIntegrity(0, [integrityOrderingRepertoire("rep", 1), integrityOrderingRepertoire("other", 1, 4)]);
      expectIntegrityOrderingCounts(2, 7);
      expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
      expect(document.activeElement).toBe(home.studyMove);
      expect(home.integrityEvidenceReads).toEqual(["rep"]);
      await requestIntegrityOrderingRefresh("passive");
      await home.resolveIntegrity(1, [integrityOrderingRepertoire("rep", 1), integrityOrderingRepertoire("other", 1, 4)]);
      expectIntegrityOrderingCounts(2, 7);
      expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
      expect(document.activeElement).toBe(home.studyMove);
      expectIntegrityOrderingStudy(home, before);
      await requestIntegrityOrderingRefresh("generic", "rep");
      await home.resolveIntegrity(2, [integrityOrderingRepertoire("rep", 1), integrityOrderingRepertoire("other", 1, 4)]);
      await expectIntegrityOrderingDialog("rep");
      expectIntegrityOrderingCounts(2, 7);
      expect(home.integrityEvidenceReads).toEqual(["rep", "rep"]);
      expectIntegrityOrderingStudy(home, before);
    });
  }

  for (const refreshKind of ["generic", "passive"] as const) {
    for (const latestFailure of ["transport", "valid-shaped HTTP error", "malformed JSON", "schema"] as const) {
      for (const failureOrder of ["before older success", "after older success"] as const) {
        it(`${failureOrder === "before older success"
          ? "failed newer integrity request cannot fence an older valid preferred snapshot"
          : "older valid snapshot remains authoritative when a newer request later fails"} (${refreshKind}, ${latestFailure})`, async () => {
          const home = await renderIntegrityOrderingHome();
          const before = captureIntegrityOrderingStudy(home);
          await requestIntegrityOrderingRefresh("generic", "rep");
          await requestIntegrityOrderingRefresh(refreshKind);
          expect(home.integrityReads.map(read => read.passive)).toEqual([false, refreshKind === "passive"]);
          const failNewerRequest = async () => {
            await act(async () => {
              if (latestFailure === "transport") home.integrityReads[1].reject(new Error("Latest integrity read unavailable"));
              else home.integrityReads[1].resolve(latestFailure === "valid-shaped HTTP error"
                ? Response.json({ repertoires: [integrityOrderingRepertoire("rep", 0)] }, { status: 503 })
                : latestFailure === "malformed JSON"
                ? new Response("{", { headers: { "Content-Type": "application/json" } })
                : Response.json({ repertoires: [{ ...integrityOrderingRepertoire("rep", 1), integrity_issue_count: "invalid" }] }));
            });
          };
          if (failureOrder === "before older success") {
            await failNewerRequest();
            expectIntegrityOrderingCounts(2, 6);
            expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
            expect(document.activeElement).toBe(home.studyMove);
          }
          await home.resolveIntegrity(0, [integrityOrderingRepertoire("rep", 5, 11)]);
          expectIntegrityOrderingCounts(5, 11);
          const dialog = await expectIntegrityOrderingDialog("rep");
          fireEvent.click(within(dialog).getByRole("button", { name: "e2e4" }));
          if (failureOrder === "after older success") await failNewerRequest();
          expectIntegrityOrderingCounts(5, 11);
          expect(screen.getByRole("dialog", { name: "Choose one response per position" })).toBe(dialog);
          expect(within(dialog).getByText("e2e4", { selector: "strong" })).not.toBeNull();
          expect(home.integrityEvidenceReads).toEqual(["rep"]);
          expect(home.integrityReads).toHaveLength(2);
          expectIntegrityOrderingStudy(home, before);
        });
      }
    }
  }

  for (const newerPreferredState of ["clean", "removed"] as const) {
    it(`pre-intent snapshot cannot retire newer preferred repair intent (${newerPreferredState})`, async () => {
      const home = await renderIntegrityOrderingHome();
      const before = captureIntegrityOrderingStudy(home);
      await requestIntegrityOrderingRefresh("generic", "rep");
      await requestIntegrityOrderingRefresh("generic", "other");
      const olderSnapshot = [integrityOrderingRepertoire("rep", 5, 11)];
      if (newerPreferredState === "clean") olderSnapshot.push(integrityOrderingRepertoire("other", 0));
      await home.resolveIntegrity(0, olderSnapshot);
      expectIntegrityOrderingCounts(5, 11);
      expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
      expect(document.activeElement).toBe(home.studyMove);
      await home.resolveIntegrity(1, [integrityOrderingRepertoire("rep", 1, 3), integrityOrderingRepertoire("other", 2, 4)]);
      expectIntegrityOrderingCounts(3, 7);
      await expectIntegrityOrderingDialog("other");
      expect(home.integrityEvidenceReads).toEqual(["other"]);
      expect(home.integrityReads).toHaveLength(2);
      expectIntegrityOrderingStudy(home, before);
    });
  }

  for (const navigationPath of ["primary navigation", "Browse tree"] as const) {
    it(`workspace navigation cancels pending preferred repair intent (${navigationPath})`, async () => {
      const home = await renderIntegrityOrderingHome();
      if (navigationPath === "Browse tree") {
        fireEvent.click(screen.getByRole("button", { name: "Repertoire" }));
        await waitFor(() => expect(home.integrityReads).toHaveLength(1));
        await home.resolveIntegrity(0, [integrityOrderingRepertoire("rep", 2)]);
        await screen.findByRole("button", { name: "Browse tree" });
      }
      const before = captureIntegrityOrderingStudy(home);
      await requestIntegrityOrderingRefresh("generic", "rep");
      const preferredRead = navigationPath === "Browse tree" ? 1 : 0;
      fireEvent.click(navigationPath === "Browse tree"
        ? screen.getByRole("button", { name: "Browse tree" })
        : screen.getAllByRole("button", { name: "Builder" })[0]);
      await screen.findByRole("heading", { name: "Builder", level: 1 });
      await home.resolveIntegrity(preferredRead, [integrityOrderingRepertoire("rep", 1)]);
      expect(screen.getByRole("heading", { name: "Builder", level: 1 })).not.toBeNull();
      expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
      expect(home.integrityEvidenceReads).toEqual([]);
      const training = useTrainingStore.getState();
      expect(training.attempt).toEqual(before.attempt);
      expect(training.getCard().queueEntryId).toBe(before.queueEntryId);
      expect(training.currentFenString).toBe(before.fen);
      expect(training.step).toBe(before.step);
      expect(home.queueReadCount()).toBe(before.queueReads);
    });
  }

  it("CardEditor save repair intent survives its later queue integrity refresh", async () => {
    const home = await renderIntegrityOrderingHome();
    home.holdQueueReads();
    fireEvent.click(screen.getByRole("button", { name: /Edit card$/ }));
    const editor = await screen.findByRole("dialog", { name: "Edit Integrity ordering study" });
    fireEvent.click(within(editor).getByRole("button", { name: "Validate & save" }));
    await waitFor(() => {
      expect(home.queueResponses).toHaveLength(1);
      expect(home.integrityReads).toHaveLength(1);
      expect(screen.queryByRole("dialog", { name: "Edit Integrity ordering study" })).toBeNull();
    });
    expect(home.fetchMock.mock.calls.filter(([input, init]) => String(input).endsWith("/api/cards/integrity-ordering-card") && init?.method === "PUT")).toHaveLength(1);
    // The real Home onSave starts its queue refresh before dispatching preferred tempo:integrity.
    await act(async () => { home.queueResponses[0](Response.json(home.queuePayload)); });
    await waitFor(() => expect(home.integrityReads).toHaveLength(2));
    expect(home.integrityReads.map(read => read.passive)).toEqual([false, false]);
    // Saving resets the line intentionally; only the subsequent integrity reads must preserve it.
    const before = captureIntegrityOrderingStudy(home);
    await home.resolveIntegrity(1, [integrityOrderingRepertoire("rep", 1)]);
    expectIntegrityOrderingCounts(1, 3);
    const dialog = await expectIntegrityOrderingDialog("rep");
    fireEvent.click(within(dialog).getByRole("button", { name: "e2e4" }));
    await home.resolveIntegrity(0, [integrityOrderingRepertoire("rep", 5, 11)]);
    expectIntegrityOrderingCounts(1, 3);
    expect(screen.getByRole("dialog", { name: "Choose one response per position" })).toBe(dialog);
    expect(within(dialog).getByText("e2e4", { selector: "strong" })).not.toBeNull();
    expect(home.integrityEvidenceReads).toEqual(["rep"]);
    expectIntegrityOrderingStudy(home, before);
  });
});

for (const { title, latestIssueCount, olderIssueCount, preferred } of [
  { title: "older passive integrity reconciliation cannot restore repair counts after a newer clean result",
    latestIssueCount: 0, olderIssueCount: 1, preferred: false },
  { title: "older passive integrity reconciliation cannot replace newer partial repair counts",
    latestIssueCount: 1, olderIssueCount: 2, preferred: false },
  { title: "newer preferred integrity reconciliation opens its dialog while an older passive response is pending",
    latestIssueCount: 1, olderIssueCount: 2, preferred: true },
]) {
  it(title, async () => {
    const pendingPassiveResponses: ((response: Response) => void)[] = [];
    let foregroundIssueCount = 2;
    const repertoireCounts = (issueCount: number) => ({ repertoires: [{
      id: "rep", name: "Repair repertoire", source_name: "fixture.pgn", line_count: 2,
      card_count: 6, due_count: 6, graph_state: "ready", graph_generation: 2,
      integrity_status: issueCount ? "needs_repair" : "clean",
      integrity_issue_count: issueCount, blocked_due_count: issueCount * 3,
    }] });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/queue/window")) return Response.json({ count: 1, cards: [{
        id: "overlapping-repair-study", queue_entry_id: 873, start_fen: new Chess().fen(),
        moves: ["e2e4", "e7e5", "g1f3"], trained_color: "white", content_type: "opening",
        repertoire_name: "Overlapping repair study", repertoire_source: "fixture.pgn",
      }] });
      if (url.endsWith("/repertoires")) {
        if (new Headers(init?.headers).get("X-Tempo-Work-Class") === "background") {
          return new Promise<Response>(resolve => pendingPassiveResponses.push(resolve));
        }
        return Response.json(repertoireCounts(foregroundIssueCount));
      }
      if (url.endsWith("/integrity")) return Response.json({ repertoire_id: "rep", status: "needs_repair",
        issue_count: 1, first_issue_id: "overlap-issue", scan_status: "idle", scan_generation: "scan:2",
        scan_progress: { completed: 2, total: 2 }, last_scan_error: null,
        issues: [{ id: "overlap-issue", kind: "multiple_responses", signature: "overlap-signature",
          fen: new Chess().fen(), fen_key: new Chess().fen().split(" ").slice(0, 4).join(" "),
          trained_color: "white", moves: [], sources: [] }] });
      return Response.json({ providers: [], states: [], lines: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Home />);
    await screen.findByRole("heading", { name: "Overlapping repair study" });
    await screen.findByRole("button", { name: "Resume repair" });
    await act(async () => {});
    const studyMove = screen.getByRole("button", { name: "e2e4" });
    studyMove.focus();
    const beforeReconciliation = useTrainingStore.getState();
    const queueReadsBeforeReconciliation = fetchMock.mock.calls.filter(([input]) => String(input).includes("/api/queue/window")).length;
    await act(async () => {
      window.dispatchEvent(new CustomEvent("tempo-integrity-repair-confirmed", { detail: { repertoireId: "rep" } }));
    });
    expect(pendingPassiveResponses).toHaveLength(1);
    if (preferred) {
      foregroundIssueCount = latestIssueCount;
      await act(async () => {
        window.dispatchEvent(new CustomEvent("tempo:integrity", { detail: { repertoireId: "rep" } }));
      });
    } else {
      await act(async () => {
        window.dispatchEvent(new CustomEvent("tempo-integrity-repair-confirmed", { detail: { repertoireId: "rep" } }));
      });
      expect(pendingPassiveResponses).toHaveLength(2);
      await act(async () => { pendingPassiveResponses[1](Response.json(repertoireCounts(latestIssueCount))); });
    }
    const assertLatestRepairCounts = () => {
      if (latestIssueCount === 0) {
        expect(screen.queryByRole("button", { name: "Resume repair" })).toBeNull();
        expect(screen.queryByText(/opening cards? paused by repertoire repair/)).toBeNull();
        expect(screen.queryByText(/issues? remaining\./)).toBeNull();
      } else {
        const notice = screen.getByRole("button", { name: "Resume repair" }).closest('[role="status"]')!;
        expect(notice.textContent).toContain("3 opening cards paused by repertoire repair.");
        expect(notice.textContent).toContain("1 issue remaining.");
      }
    };
    assertLatestRepairCounts();
    const dialog = preferred ? await screen.findByRole("dialog", { name: "Choose one response per position" }) : null;
    if (dialog) fireEvent.click(await within(dialog).findByRole("button", { name: "e2e4" }));
    await act(async () => { pendingPassiveResponses[0](Response.json(repertoireCounts(olderIssueCount))); });
    assertLatestRepairCounts();
    if (dialog) {
      expect(screen.getByRole("dialog", { name: "Choose one response per position" })).toBe(dialog);
      expect(within(dialog).getByText("e2e4", { selector: "strong" })).not.toBeNull();
    } else {
      expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
      expect(document.activeElement).toBe(studyMove);
    }
    const afterReconciliation = useTrainingStore.getState();
    expect(afterReconciliation.attempt).toEqual(beforeReconciliation.attempt);
    expect(afterReconciliation.currentFenString).toBe(beforeReconciliation.currentFenString);
    expect(afterReconciliation.step).toBe(beforeReconciliation.step);
    expect(afterReconciliation.getCard().queueEntryId).toBe(beforeReconciliation.getCard().queueEntryId);
    expect(fetchMock.mock.calls.filter(([input]) => String(input).includes("/api/queue/window")))
      .toHaveLength(queueReadsBeforeReconciliation);
  });
}

for (const { title, hasRemainingConflict, crossTab } of [
  { title: "confirmed partial repair restores the deferred resume notice without changing study", hasRemainingConflict: true, crossTab: false },
  { title: "confirmed final repair removes the deferred resume notice without changing study", hasRemainingConflict: false, crossTab: false },
  { title: "external partial repair refreshes deferred counts and preserves active study", hasRemainingConflict: true, crossTab: true },
  { title: "external final repair removes the deferred notice without changing active study", hasRemainingConflict: false, crossTab: true },
]) {
  it(title, async () => {
    const repairIssues = ["first", "second"].map(id => ({ id, kind: "multiple_responses", signature: `${id}-signature`,
      fen: new Chess().fen(), fen_key: new Chess().fen().split(" ").slice(0, 4).join(" "),
      trained_color: "white", moves: [], sources: [{ type: "line", id: `${id}-source` }] }));
    let serverIssues = hasRemainingConflict ? repairIssues : repairIssues.slice(0, 1);
    let submissionConfirmed = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/queue/window")) return Response.json({ count: 1, cards: [{
        id: "partial-repair-study", queue_entry_id: 872, start_fen: new Chess().fen(), moves: ["e2e4", "e7e5", "g1f3"],
        trained_color: "white", content_type: "opening", repertoire_name: "Active study", repertoire_source: "fixture.pgn",
      }] });
      if (url.endsWith("/repertoires")) return Response.json({ repertoires: [{ id: "rep", name: "Repair repertoire",
        source_name: "fixture.pgn", line_count: 2, card_count: 6, due_count: 6, graph_state: "ready", graph_generation: 2,
        integrity_status: serverIssues.length ? "needs_repair" : "clean", integrity_issue_count: serverIssues.length,
        blocked_due_count: serverIssues.length * 3 }] });
      if (url.endsWith("/integrity")) return Response.json({ repertoire_id: "rep",
        status: serverIssues.length ? "needs_repair" : "clean", issue_count: serverIssues.length,
        first_issue_id: serverIssues[0]?.id ?? null, scan_status: "idle", scan_generation: "scan:2",
        scan_progress: { completed: 2, total: 2 }, last_scan_error: null, issues: serverIssues });
      const submission = { task_id: "partial-repair-graph", repertoire_id: "rep", issue_id: "first", state: "queued" };
      if (url.endsWith("/first/resolve") && init?.method === "POST") { submissionConfirmed = true; return Response.json(submission); }
      if (url.includes("/api/operations/")) return Response.json(submissionConfirmed
        ? { state: "complete", response: submission } : { state: "unknown" });
      if (url.endsWith("/system/tasks")) return Response.json({ tasks: [{ id: "partial-repair-graph",
        kind: "opening_graph_rebuild", deduplication_key: "rep", generation: 2, state: "complete" }] });
      return Response.json({ providers: [], states: [], lines: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Home />);
    await screen.findByRole("heading", { name: "Active study" });
    fireEvent.click(await screen.findByRole("button", { name: "Resume repair" }));
    const dialog = await screen.findByRole("dialog", { name: "Choose one response per position" });
    await within(dialog).findByText(/line first-source/);
    fireEvent.click(within(dialog).getByRole("button", { name: "e2e4" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep this response" }));
    if (hasRemainingConflict) await within(dialog).findByText(/line second-source/);
    else await within(dialog).findByText(/All choices queued/);
    expect(pendingIntegrityRepairs().map(repair => repair.issueId)).toEqual(["first"]);
    fireEvent.click(within(dialog).getByRole("button", { name: "Defer repertoire repair" }));
    const studyMove = screen.getByRole("button", { name: "e2e4" });
    studyMove.focus();
    const beforeConfirmation = useTrainingStore.getState();
    const queueReadsBeforeConfirmation = fetchMock.mock.calls.filter(([input]) => String(input).includes("/api/queue/window")).length;
    await act(async () => { await flushIntegrityRepairs(); });
    const countsBeforeCompletion = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/repertoires")).length;
    serverIssues = hasRemainingConflict ? repairIssues.slice(1) : [];
    if (crossTab) {
      const repair = pendingIntegrityRepairs()[0];
      const storageKey = `tempo-pending-integrity-repairs-v3:${repair.operationId}`;
      const oldValue = localStorage.getItem(storageKey);
      await act(async () => {
        localStorage.removeItem(storageKey);
        window.dispatchEvent(new StorageEvent("storage", { key: storageKey, oldValue, newValue: null, storageArea: localStorage }));
      });
    } else {
      await act(async () => { await flushIntegrityRepairs(); });
    }
    // Local validation reads publication once, then Home reconciles once; external removal only reconciles.
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/repertoires")))
      .toHaveLength(countsBeforeCompletion + (crossTab ? 1 : 2));
    expect(pendingIntegrityRepairs()).toHaveLength(0);
    expect(screen.queryByRole("dialog", { name: "Choose one response per position" })).toBeNull();
    const afterConfirmation = useTrainingStore.getState();
    expect(afterConfirmation.attempt).toEqual(beforeConfirmation.attempt);
    expect(afterConfirmation.currentFenString).toBe(beforeConfirmation.currentFenString);
    expect(afterConfirmation.step).toBe(beforeConfirmation.step);
    expect(afterConfirmation.getCard().queueEntryId).toBe(beforeConfirmation.getCard().queueEntryId);
    expect(document.activeElement).toBe(studyMove);
    expect(fetchMock.mock.calls.filter(([input]) => String(input).includes("/api/queue/window"))).toHaveLength(queueReadsBeforeConfirmation);
    const latestCountsRequest = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/repertoires")).at(-1)!;
    expect(new Headers(latestCountsRequest[1]?.headers).get("X-Tempo-Work-Class")).toBe("background");
    if (hasRemainingConflict) {
      const resume = screen.getByRole("button", { name: "Resume repair" });
      const notice = resume.closest('[role="status"]')!;
      expect(notice.textContent).toContain("3 opening cards paused by repertoire repair.");
      expect(notice.textContent).toContain("1 issue remaining.");
      fireEvent.click(resume);
      await screen.findByText(/line second-source/);
      expect(screen.queryByText(/line first-source/)).toBeNull();
    } else {
      expect(screen.queryByRole("button", { name: "Resume repair" })).toBeNull();
      expect(screen.queryByText(/opening cards? paused by repertoire repair/)).toBeNull();
    }
});
}
