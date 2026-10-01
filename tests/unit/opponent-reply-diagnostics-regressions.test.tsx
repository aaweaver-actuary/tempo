import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Api } from "@lichess-org/chessground/api";
import type { Square } from "chess.js";
import { Chess } from "chess.js";
import Home from "../../app/views/home_view";
import { useTrainingStore } from "../../app/state/training-store";
import { asSanMove } from "../../app/types";
import {
  installTempoDragCapture, recordTempoDragPhase, resetTempoDragDiagnostics,
  tempoDragDiagnostics,
} from "../../app/lib/performance";

vi.mock("../../app/components/board/chessboard", () => ({
  Chessboard: ({ fen, locked, onMove }: {
    fen: string; locked: boolean; onMove: (from: Square, to: Square) => void;
  }) => <div data-testid="reply-board" data-fen={fen}>
    <button disabled={locked} onClick={() => onMove("e2", "e4")}>Play e4</button>
    <button disabled={locked} onClick={() => onMove("g1", "f3")}>Play Nf3</button>
  </div>,
}));

const captures: Array<ReturnType<typeof installTempoDragCapture>> = [];
const captureSurfaces: HTMLElement[] = [];

afterEach(() => {
  // Exercise Home's cleanup before disposing its independent diagnostic fixture.
  cleanup();
  captures.splice(0).forEach(capture => capture.dispose());
  captureSurfaces.splice(0).forEach(surface => surface.remove());
  resetTempoDragDiagnostics();
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});

async function mountTraining() {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  window.history.replaceState({}, "", "/?tempoPerformance=drag");
  const queueCard = {
    id: "reply-diagnostics", queue_entry_id: 731, start_fen: new Chess().fen(),
    moves: ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"],
    content_type: "opening", repertoire_name: "Synthetic reply lifecycle",
    repertoire_source: "PGN", cycle: 0,
  };
  vi.stubGlobal("fetch", vi.fn(async input => {
    const url = String(input);
    if (url.includes("/api/queue/window"))
      return Response.json({ cards: [queueCard], count: 1 });
    return Response.json({ providers: [], states: [], lines: [], repertoires: [], discoveries: [] });
  }));
  const mounted = render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(731));
  await waitFor(() => expect((screen.getByRole("button", { name: "Play e4" }) as HTMLButtonElement).disabled).toBe(false));
  vi.useFakeTimers();
  resetTempoDragDiagnostics();
  const unrelatedId = recordTempoDragPhase("engine-work", "start");
  expect(unrelatedId).toBeGreaterThan(0);
  return { ...mounted, unrelatedId };
}

function dragFixture() {
  const surface = document.createElement("div");
  const piece = document.createElement("piece");
  piece.style.transform = "translate(225px, 325px)";
  surface.appendChild(piece);
  document.body.appendChild(surface);
  captureSurfaces.push(surface);
  const state = {
    draggable: { current: undefined as undefined | {
      started: boolean; orig: string; origPos: [number, number]; pos: [number, number]; element: HTMLElement;
    } },
    dom: { bounds: () => ({ left: 10, top: 20, width: 400, height: 400 }) },
    orientation: "white", animation: { enabled: true, duration: 180 },
  };
  captures.push(installTempoDragCapture(surface, { state } as unknown as Api));
  function start() {
    state.draggable.current = { started: true, orig: "e2", origPos: [240, 350], pos: [260, 370], element: piece };
    piece.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, buttons: 1, clientX: 240, clientY: 350 }));
    act(() => vi.advanceTimersByTime(20));
  }
  function end() {
    document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    state.draggable.current = undefined;
    act(() => vi.advanceTimersByTime(20));
  }
  return { start, end };
}

function restart() {
  fireEvent.click(screen.getByRole("button", { name: /Restart/ }));
}

function scheduleReply() {
  fireEvent.click(screen.getByRole("button", { name: "Play e4" }));
  expect(useTrainingStore.getState().attempt.phase).toBe("opponentReplyPending");
  const start = tempoDragDiagnostics().sessions.at(-1)!.phases
    .filter(phase => phase.operation === "opponent-reply" && phase.edge === "start").at(-1)!;
  expect(start.operationId).toBeGreaterThan(0);
  return start.operationId;
}

function expectTerminal(operationId: number, edge: "end" | "error") {
  const retainedSessions = tempoDragDiagnostics().sessions.filter(session =>
    session.phases.some(phase => phase.operationId === operationId));
  expect(retainedSessions.length).toBeGreaterThan(0);
  for (const session of retainedSessions)
    expect(session.phases.filter(phase => phase.operationId === operationId).map(phase => phase.edge))
      .toEqual(["start", edge]);
}

function expectOnlyUnrelatedOperation(unrelatedId: number) {
  expect(tempoDragDiagnostics().phaseTracking).toEqual({ activeOperations: 1, truncated: false });
  expect(tempoDragDiagnostics().sessions.at(-1)!.phases.filter(phase => phase.operationId === unrelatedId))
    .toEqual([expect.objectContaining({ edge: "start", operation: "engine-work" })]);
}

it("opponent_reply_reset_closes_its_diagnostic_phase", async () => {
  const { unrelatedId } = await mountTraining();
  const drag = dragFixture(); drag.start(); drag.end();
  const operationId = scheduleReply();
  act(() => vi.advanceTimersByTime(100));
  restart();
  expect(useTrainingStore.getState().step).toBe(0);
  expectTerminal(operationId, "error");
  expectOnlyUnrelatedOperation(unrelatedId);
  act(() => vi.advanceTimersByTime(420));
  expect(useTrainingStore.getState().step).toBe(0);
  drag.start(); drag.end();
  expect(tempoDragDiagnostics().sessions.at(-1)!.phases.some(phase => phase.operationId === operationId)).toBe(false);
  expectOnlyUnrelatedOperation(unrelatedId);
});

it("opponent_reply_home_unmount_closes_its_diagnostic_phase", async () => {
  const { unmount, unrelatedId } = await mountTraining();
  const drag = dragFixture(); drag.start(); drag.end();
  const operationId = scheduleReply();
  act(() => vi.advanceTimersByTime(100));
  const beforeUnmount = useTrainingStore.getState().currentFenString;
  unmount();
  expectTerminal(operationId, "error");
  expectOnlyUnrelatedOperation(unrelatedId);
  act(() => vi.advanceTimersByTime(420));
  expect(useTrainingStore.getState().currentFenString).toBe(beforeUnmount);
  drag.start(); drag.end();
  expect(tempoDragDiagnostics().sessions.at(-1)!.phases.some(phase => phase.operationId === operationId)).toBe(false);
  expectOnlyUnrelatedOperation(unrelatedId);
});

it("opponent_reply_repeated_cancellation_preserves_registry_capacity", async () => {
  const { unrelatedId } = await mountTraining();
  const drag = dragFixture();
  const playMoveButton = screen.getByRole("button", { name: "Play e4" });
  const restartButton = screen.getByRole("button", { name: /Restart/ });
  for (let cycle = 0; cycle < 129; cycle++) {
    drag.start(); drag.end();
    fireEvent.click(playMoveButton);
    expect(useTrainingStore.getState().attempt.phase).toBe("opponentReplyPending");
    fireEvent.click(restartButton);
    expect(useTrainingStore.getState().step).toBe(0);
  }
  expectOnlyUnrelatedOperation(unrelatedId);
  const retainedReplyStarts = tempoDragDiagnostics().sessions.flatMap(session => session.phases)
    .filter(phase => phase.operation === "opponent-reply" && phase.edge === "start");
  expect(retainedReplyStarts).toHaveLength(10);
  retainedReplyStarts.forEach(phase => expectTerminal(phase.operationId, "error"));
  drag.start(); drag.end();
  expect(tempoDragDiagnostics().sessions.at(-1)!.phases.every(phase => phase.operationId === unrelatedId)).toBe(true);
  const freshOperationId = recordTempoDragPhase("review-persistence", "start");
  expect(freshOperationId).toBeGreaterThan(0);
  recordTempoDragPhase("review-persistence", "end", freshOperationId);
  expectTerminal(freshOperationId, "end");
  expectOnlyUnrelatedOperation(unrelatedId);
});

it("opponent_reply_completion_and_stale_callbacks_preserve_newer_ownership", async () => {
  const { unrelatedId } = await mountTraining();
  const drag = dragFixture(); drag.start(); drag.end();
  const operationId = scheduleReply();
  act(() => vi.advanceTimersByTime(419));
  expect(useTrainingStore.getState().step).toBe(1);
  expect(tempoDragDiagnostics().phaseTracking.activeOperations).toBe(2);
  act(() => vi.advanceTimersByTime(1));
  expect(useTrainingStore.getState().step).toBe(2);
  expectTerminal(operationId, "end");
  expectOnlyUnrelatedOperation(unrelatedId);
  restart();
  expectTerminal(operationId, "end");

  const scheduledTimers = vi.spyOn(globalThis, "setTimeout");
  const canceledId = scheduleReply();
  const oldCallback = scheduledTimers.mock.calls.find(call => call[1] === 420)![0] as () => void;
  restart();
  const newerId = scheduleReply();
  act(() => oldCallback());
  expectTerminal(canceledId, "error");
  expect(tempoDragDiagnostics().sessions.at(-1)!.phases.filter(phase => phase.operationId === newerId))
    .toEqual([expect.objectContaining({ edge: "start" })]);
  expect(tempoDragDiagnostics().phaseTracking.activeOperations).toBe(2);
  expect(useTrainingStore.getState().step).toBe(1);
  restart();
  expectTerminal(newerId, "error");
  expectOnlyUnrelatedOperation(unrelatedId);
  act(() => vi.advanceTimersByTime(420));
  expect(useTrainingStore.getState().step).toBe(0);
  expectTerminal(operationId, "end");
});

it("opponent_reply_handled_failure_closes_its_diagnostic_phase", async () => {
  const { unrelatedId } = await mountTraining();
  // Keep Home's real move handler; supply an invalid reply after queue validation.
  act(() => useTrainingStore.setState(state => ({ practiceCards: state.practiceCards.map(card =>
    ({ ...card, moves: [asSanMove("e4"), asSanMove("illegal-reply"), asSanMove("Nf3")] })) })));
  const drag = dragFixture(); drag.start(); drag.end();
  const operationId = scheduleReply();
  act(() => vi.advanceTimersByTime(420));
  expect(useTrainingStore.getState().serviceError).toContain("opponent reply is illegal");
  expectTerminal(operationId, "error");
  expectOnlyUnrelatedOperation(unrelatedId);
  cleanup();
  expectTerminal(operationId, "error");
  expectOnlyUnrelatedOperation(unrelatedId);
});
