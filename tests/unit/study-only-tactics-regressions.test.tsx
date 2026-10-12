import { act, render, screen, waitFor } from "@testing-library/react";
import { Chess, type Square } from "chess.js";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Home from "../../app/views/home_view";
import TrainingView from "../../app/views/training_view";
import { useTrainingStore } from "../../app/state/training-store";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { mapQueueCardToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";
import { pendingReviews } from "../../app/lib/review-outbox";
import { flushTrainingFailures } from "../../app/lib/training-failure-outbox";
import * as local from "../../app/utils/local";

vi.mock("../../app/components/board/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn(),
  moveSoundEnabled: () => false, prepareMoveSounds: vi.fn(), cancelMoveSounds: vi.fn() }));

const tacticFen = "q5nr/1ppknQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 w - - 1 18";
const tacticMoves = ["a2e6", "d7d8", "f7f8"];
const nextCard = { id: "next-tactic", queue_entry_id: 9202, revision: 1, start_fen: new Chess().fen(),
  moves: ["e2e4"], content_type: "tactic", repertoire_name: "Next tactic", repertoire_source: "test" };

function mockDailyTactic(studyOnlyMode: boolean, repertoireId?: string, moves = tacticMoves, startingFen = tacticFen) {
  vi.spyOn(local, "usesLocalApi").mockReturnValue(true);
  const tactic = { id: "study-only-tactic", queue_entry_id: 9201, revision: 1, start_fen: startingFen,
    moves, content_type: "tactic", repertoire_id: repertoireId, repertoire_name: "Tactics",
    repertoire_source: "test", first_correct_at: "2000-01-01T00:00:00Z" };
  let failedPersisted = false;
  let reviewed = false;
  const failureRequests: unknown[] = [];
  const reviewRequests: { outcome: string; guided: boolean }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/settings")) return Response.json({ study_only_mode: studyOnlyMode });
    if (url.includes("/api/queue/window")) return Response.json({ cards: reviewed ? [nextCard]
      : [{ ...tactic, attempt_failed: failedPersisted }, nextCard], count: reviewed ? 1 : 2 });
    if (url.endsWith("/fail")) {
      failedPersisted = true;
      failureRequests.push(JSON.parse(String(options?.body)));
      return Response.json({ attempt_failed: true });
    }
    if (url.endsWith("/review")) {
      reviewRequests.push(JSON.parse(String(options?.body)));
      reviewed = true;
      return Response.json({ persisted: true });
    }
    if (url.includes("/api/operations/")) return new Response(null, { status: 404 });
    if (url.includes("/move-membership?")) return Response.json({ detail: "No opening sources for tactics" }, { status: 503 });
    // Normal mode also starts with unavailable bulk opening evidence.
    if (url.includes("/repertoire/lines")) return new Promise<Response>(() => {});
    return Response.json({ providers: [], states: [], repertoires: [], discoveries: [], annotations: [] });
  }));
  return { tactic, failureRequests, reviewRequests };
}

async function readyDailyTactic() {
  const view = render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().getCard().backendId).toBe("study-only-tactic"));
  await waitFor(() => expect(useBoardShellStore.getState().board.onMove).toBeTypeOf("function"));
  return view;
}

async function play(moveUci: string) {
  await act(async () => useBoardShellStore.getState().board.onMove?.(moveUci.slice(0, 2) as Square, moveUci.slice(2, 4) as Square));
}

async function finishTactic() {
  vi.useFakeTimers();
  await play("a2e6");
  await act(async () => vi.advanceTimersByTimeAsync(420));
  await play("f7f8");
}

beforeEach(() => vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} }));
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it.each([undefined, "__tactics__"])("study_only_daily_tactic_wrong_move_records_again (%s)", async repertoireId => {
  const { failureRequests } = mockDailyTactic(true, repertoireId);
  const view = await readyDailyTactic();
  await screen.findByText(/Study-only mode is on/);
  await play("a3b4");
  expect(useTrainingStore.getState()).toMatchObject({ isAttemptFailed: true, feedback: "wrong", currentFenString: tacticFen, step: 0, showHint: true });
  await flushTrainingFailures();
  expect(failureRequests).toEqual([{ card_id: "study-only-tactic", expected_revision: 1 }]);
  expect(view.container.querySelector(".feedback-icon")?.textContent).toBe("×");
  expect(screen.getByText("That move is not the puzzle solution. Follow the highlighted move to finish.")).toBeTruthy();
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/move-membership?"))).toBe(false);
});

it("study_only_daily_tactic_guided_completion_never_becomes_correct", async () => {
  const { reviewRequests } = mockDailyTactic(true, "__tactics__");
  const view = await readyDailyTactic();
  await screen.findByText(/Study-only mode is on/);
  await play("a3b4");
  await flushTrainingFailures();
  await finishTactic();
  expect(pendingReviews()).toHaveLength(1);
  expect(pendingReviews()[0]).toMatchObject({ outcome: "again", guided: true });
  expect(view.container.querySelector(".feedback-icon")?.textContent).toBe("×");
  expect(view.container.querySelector(".feedback")?.classList.contains("wrong")).toBe(true);
  expect(screen.getByText("This attempt counts as Again.")).toBeTruthy();
  await act(async () => vi.advanceTimersByTimeAsync(750));
  vi.useRealTimers();
  await waitFor(() => expect(reviewRequests).toHaveLength(1));
  expect(reviewRequests[0]).toMatchObject({ outcome: "again", guided: true });
});

it("study_only_daily_tactic_failure_survives_reload_before_completion", async () => {
  const { reviewRequests } = mockDailyTactic(true, "__tactics__");
  const mounted = await readyDailyTactic();
  await screen.findByText(/Study-only mode is on/);
  await play("a3b4");
  await flushTrainingFailures();
  mounted.unmount();
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  await readyDailyTactic();
  expect(useTrainingStore.getState()).toMatchObject({ isAttemptFailed: true, currentFenString: tacticFen, showHint: true });
  await finishTactic();
  expect(pendingReviews()[0]).toMatchObject({ outcome: "again", guided: true });
  await act(async () => vi.advanceTimersByTimeAsync(750));
  vi.useRealTimers();
  await waitFor(() => expect(reviewRequests).toHaveLength(1));
  expect(reviewRequests[0]).toMatchObject({ outcome: "again", guided: true });
});

it("daily_tactic_wrong_move_does_not_wait_for_normal_mode_opening_preparation", async () => {
  const { failureRequests } = mockDailyTactic(false, "__tactics__");
  await readyDailyTactic();
  await play("a3b4");
  expect(useTrainingStore.getState().isAttemptFailed).toBe(true);
  await flushTrainingFailures();
  expect(failureRequests).toHaveLength(1);
});

it("study_only_daily_tactic_clean_solution_remains_correct", async () => {
  mockDailyTactic(true, "__tactics__");
  const view = await readyDailyTactic();
  await finishTactic();
  expect(pendingReviews()[0]).toMatchObject({ outcome: "correct", guided: false });
  expect(view.container.querySelector(".feedback-icon")?.textContent).toBe("✓");
});

it("study_only_daily_tactic_accepts_an_immediate_mate_outside_the_reference_line", async () => {
  const mateFen = "7k/5K2/6Q1/8/8/8/8/8 w - - 0 1";
  mockDailyTactic(true, "__tactics__", ["g6h6"], mateFen);
  await readyDailyTactic();
  vi.useFakeTimers();
  await play("g6g7");
  expect(new Chess(useTrainingStore.getState().currentFenString).isCheckmate()).toBe(true);
  expect(pendingReviews()[0]).toMatchObject({ outcome: "correct", guided: false });
});

it("guided_training_completion_does_not_show_success_check", () => {
  const card = mapQueueCardToPracticeCard({ ...nextCard, moves: ["e2e4"] });
  useTrainingStore.setState({ feedback: "complete", isAttemptFailed: true, cardsLeft: 1 });
  const view = render(<TrainingView card={card} cardsLeft={1} dateLabel="Today" serviceError=""
    refreshDatabaseQueue={vi.fn()} boardTheme="brown" pieceSet="cburnett" rateCard={vi.fn(async () => undefined)}
    handleAttemptFailure={vi.fn()} resetCardAttempt={vi.fn()} setEditorCard={vi.fn()} onMove={vi.fn()} />);
  expect(view.container.querySelector(".feedback-icon")?.textContent).toBe("×");
  expect(view.container.querySelector(".feedback")?.classList.contains("wrong")).toBe(true);
  expect(screen.getByText("Guided line complete")).toBeTruthy();
  expect(screen.getByText("This attempt counts as Again.")).toBeTruthy();
});
