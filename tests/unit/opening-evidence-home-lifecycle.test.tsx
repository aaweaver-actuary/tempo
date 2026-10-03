import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Home from "../../app/views/home_view";
import { useTrainingStore } from "../../app/state/training-store";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import * as journal from "../../app/lib/opening-evidence-journal";
import { mapQueueCardToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";
import manifest from "../fixtures/opening-evidence-manifest.json";

vi.mock("../../app/views/fetchAndInitializeQueue", () => ({ fetchAndInitializeQueue: vi.fn() }));
vi.mock("../../app/components/board/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn(),
  moveSoundEnabled: () => false, prepareMoveSounds: vi.fn(), cancelMoveSounds: vi.fn() }));
const card = mapQueueCardToPracticeCard({ id: "shadow-card", revision: 3, queue_entry_id: 101,
  start_fen: manifest.decisions[0].fen, moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening",
  repertoire_name: "Shadow", repertoire_source: "PGN", trained_color: "white", opening_decision_manifest: manifest });
let idleCallbacks: Map<number, IdleRequestCallback>;

beforeEach(() => {
  localStorage.clear();
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  idleCallbacks = new Map();
  let idleSequence = 0;
  vi.stubGlobal("requestIdleCallback", vi.fn((callback: IdleRequestCallback) => { idleCallbacks.set(++idleSequence, callback); return idleSequence; }));
  vi.stubGlobal("cancelIdleCallback", vi.fn((id: number) => { idleCallbacks.delete(id); }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ providers: [], states: [], lines: [], repertoires: [], discoveries: [] })));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });

it("AS-15 opening evidence recovery waits for foreground startup readiness", async () => {
  const recover = vi.spyOn(journal, "recoverOpeningEvidence").mockResolvedValue(undefined);
  let finishQueue!: () => void;
  vi.mocked(fetchAndInitializeQueue).mockImplementation(() => new Promise<void>(resolve => { finishQueue = resolve; }));
  render(<Home />);
  await waitFor(() => expect(fetchAndInitializeQueue).toHaveBeenCalled());
  expect(recover).not.toHaveBeenCalled();
  act(() => window.dispatchEvent(new Event("online")));
  expect(recover).not.toHaveBeenCalled();
  await act(async () => {
    useTrainingStore.getState().hydrateLocalQueue([card], true);
    useTrainingStore.setState({ queueReadiness: "ready" }); finishQueue();
  });
  expect(recover).not.toHaveBeenCalled();
  expect(idleCallbacks.size).toBe(1);
  await act(async () => { [...idleCallbacks.values()][0]({ didTimeout: false, timeRemaining: () => 50 }); });
  expect(recover).toHaveBeenCalledOnce();
  act(() => useTrainingStore.getState().setReviewed(2));
  expect(recover).toHaveBeenCalledOnce();
});

it("AS-15 recovery cancels idle work during foreground transitions and after unmount", async () => {
  const recover = vi.spyOn(journal, "recoverOpeningEvidence").mockResolvedValue(undefined);
  vi.mocked(fetchAndInitializeQueue).mockImplementation(async () => {
    if (!useTrainingStore.getState().isDatabaseQueueActive) useTrainingStore.getState().hydrateLocalQueue([card], true);
    useTrainingStore.setState({ queueReadiness: "ready" });
  });
  const mounted = render(<Home />);
  await waitFor(() => expect(idleCallbacks.size).toBe(1));
  const canceledCallback = [...idleCallbacks.values()][0];
  act(() => useTrainingStore.getState().setAttemptPhase("opponentReplyPending"));
  expect(idleCallbacks.size).toBe(0);
  act(() => canceledCallback({ didTimeout: false, timeRemaining: () => 50 }));
  expect(recover).not.toHaveBeenCalled();
  act(() => { window.dispatchEvent(new Event("online")); window.dispatchEvent(new Event("online")); });
  expect(idleCallbacks.size).toBe(0);
  act(() => useTrainingStore.getState().setAttemptPhase("playerTurn"));
  expect(idleCallbacks.size).toBe(1);
  mounted.unmount();
  expect(idleCallbacks.size).toBe(0);
  expect(recover).not.toHaveBeenCalled();
});

