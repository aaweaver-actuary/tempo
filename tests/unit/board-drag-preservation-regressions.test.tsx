import { useBoardPublisher } from "../../app/hooks/use-board-publisher";
import { boardDiagnostics } from "../../app/lib/board-diagnostics";
import { defaultBoardState, type BoardShellSnapshot } from "../../app/state/board-shell-store";
import { StrictMode } from "react";
import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Chessboard } from "../../app/components/board/chessboard";
import TrainingView from "../../app/views/training_view";
import { PersistentBoardShell } from "../../app/components/board/persistent-board-shell";
import { STANDARD_FEN } from "../../app/const";
import { asCardId, asFenString, asSanMove } from "../../app/types";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { useTrainingStore } from "../../app/state/training-store";

const observed = vi.hoisted(() => ({ props: undefined as import("@tempo/board-ui").BoardProps | undefined,
  handle: undefined as import("@tempo/board-ui").BoardHandle | undefined, mounts: 0, unmounts: 0 }));
vi.mock("@tempo/board-ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tempo/board-ui")>();
  const React = await import("react");
  return { ...actual, Board: React.forwardRef<import("@tempo/board-ui").BoardHandle, import("@tempo/board-ui").BoardProps>((props, ref) => {
    const inner = React.useRef<import("@tempo/board-ui").BoardHandle>(null);
    observed.props = props;
    React.useImperativeHandle(ref, () => inner.current!);
    React.useLayoutEffect(() => { observed.mounts++; observed.handle = inner.current ?? undefined;
      return () => { observed.unmounts++; observed.handle = undefined; }; }, []);
    return React.createElement(actual.Board, { ...props, ref: inner });
  }) };
});
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn() }));
const boardProps = { fen: STANDARD_FEN, locked: false, showHint: false,
  theme: "brown" as const, pieceSet: "cburnett" as const, onMove: vi.fn() };
const card = { id: asCardId("drag-card"), kind: "opening" as const, title: "Drag regression", subtitle: "",
  startingFen: asFenString(STANDARD_FEN), moves: [asSanMove("e4")], userMoveTarget: 1, orientation: "white" as const };
const trainingProps = { dateLabel: "Today", serviceError: "", refreshDatabaseQueue: vi.fn(), cardsLeft: 1,
  card, boardTheme: "brown" as const, pieceSet: "cburnett" as const, rateCard: vi.fn(async () => undefined),
  handleAttemptFailure: vi.fn(), resetCardAttempt: vi.fn(), setEditorCard: vi.fn(), useSharedBoard: true };

beforeEach(() => {
  vi.clearAllMocks(); observed.props = undefined; observed.handle = undefined; observed.mounts = 0; observed.unmounts = 0;
  useBoardShellStore.setState(useBoardShellStore.getInitialState(), true);
  useTrainingStore.setState({ currentFenString: asFenString(STANDARD_FEN), step: 0, boardAttempt: 0,
    feedback: "ready", showHint: false, queueNotice: "", lastMove: undefined, opponentLastMove: undefined,
    teachingEncounterKey: null, isAttemptFailed: false, attempt: { entryKey: "drag-card", generation: 1, phase: "playerTurn" } });
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ x: 0, y: 80, top: 80,
    left: 0, right: 640, bottom: 720, width: 640, height: 640, toJSON: () => ({}) });
});

it("equivalent last-move values do not cancel a held piece or reapply FEN", () => {
  const view = render(<Chessboard {...boardProps} lastMove={["e7", "e5"]} />);
  const first = observed.props!, baseline = boardDiagnostics();
  for (let i = 0; i < 10; i++) view.rerender(<Chessboard {...boardProps} lastMove={["e7", "e5"]} onMove={vi.fn()} />);
  expect(observed.props!.positionIdentity).toBe(first.positionIdentity);
  expect(observed.props!.positionRevision).toBe(first.positionRevision);
  expect(boardDiagnostics().inputCancellations).toBe(baseline.inputCancellations);
});

it("training parent status updates keep the lease and forward the latest handler exactly once", () => {
  const firstMove = vi.fn(), currentMove = vi.fn();
  const view = render(<><PersistentBoardShell /><TrainingView {...trainingProps} onMove={firstMove} /></>);
  const identity = observed.props!.positionIdentity, baseline = boardDiagnostics();
  view.rerender(<><PersistentBoardShell /><TrainingView {...trainingProps} dateLabel="Updated" onMove={currentMove} /></>);
  expect(observed.props!.positionIdentity).toBe(identity);
  expect(boardDiagnostics().inputCancellations).toBe(baseline.inputCancellations);
  act(() => observed.props!.onMove?.({ from: "e2", to: "e4", positionIdentity: identity, positionRevision: observed.props!.positionRevision }));
  expect(firstMove).not.toHaveBeenCalled(); expect(currentMove).toHaveBeenCalledExactlyOnceWith("e2", "e4");
});

it("a wrong attempt returning to the same FEN explicitly resets through position revision", () => {
  const view = render(<Chessboard {...boardProps} positionRevision={0} />);
  const baseline = boardDiagnostics(); view.rerender(<Chessboard {...boardProps} positionRevision={1} />);
  expect(observed.props!.positionRevision).toBe(1);
  expect(boardDiagnostics().inputCancellations).toBe(baseline.inputCancellations + 1);
});

function Publisher({ snapshot = {}, enabled = true }: { snapshot?: Partial<BoardShellSnapshot>; enabled?: boolean }) {
  useBoardPublisher("train", enabled ? { ...boardProps, interactionMode: "legal", ...snapshot } : null);
  return null;
}

it("equivalent snapshots suppress store publications while callbacks use current card and step", () => {
  const grade = vi.fn();
  const view = render(<Publisher snapshot={{ lastMove: ["e7", "e5"], shapes: [{ orig: "e2", dest: "e4", brush: "green" }],
    onMove: () => grade("first-card", 0) }} />);
  const originalBoard = useBoardShellStore.getState().board;
  const originalSession = useBoardShellStore.getState().session;
  const subscribe = vi.fn();
  const unsubscribe = useBoardShellStore.subscribe(subscribe);
  view.rerender(<Publisher snapshot={{ lastMove: ["e7", "e5"], shapes: [{ orig: "e2", dest: "e4", brush: "green" }],
    onMove: () => grade("current-card", 2) }} />);
  expect(useBoardShellStore.getState().board).toBe(originalBoard);
  expect(useBoardShellStore.getState().session).toBe(originalSession);
  expect(subscribe).not.toHaveBeenCalled();
  originalBoard.onMove?.("e2", "e4");
  expect(grade).toHaveBeenCalledExactlyOnceWith("current-card", 2);
  unsubscribe();
});

it("released and remounted same-owner sessions fence both late updates and late releases", () => {
  const firstMount = render(<Publisher snapshot={{ positionKey: "first" }} />);
  const firstSession = useBoardShellStore.getState().session;
  firstMount.unmount();
  const actions = useBoardShellStore.getState();
  actions.updateShellBoardForOwner("train", { showHint: true }, firstSession);
  actions.setShellBoardForOwner("train", { ...defaultBoardState, showHint: true }, firstSession);
  expect(useBoardShellStore.getState().leaseActive).toBe(false);
  render(<Publisher snapshot={{ positionKey: "replacement" }} />);
  const replacementBoard = useBoardShellStore.getState().board;
  actions.updateShellBoardForOwner("train", { showHint: true }, firstSession);
  actions.setShellBoardForOwner("train", { ...defaultBoardState, showHint: true }, firstSession);
  actions.releaseShellBoardForOwner("train", firstSession);
  expect(useBoardShellStore.getState().board).toBe(replacementBoard);
  expect(useBoardShellStore.getState().session).toBeGreaterThan(firstSession);
});

it("an obsolete mounted publisher cannot steal a replacement owner's lease on rerender", () => {
  const obsolete = render(<Publisher />);
  act(() => useBoardShellStore.getState().acquireShellBoardForOwner("games", { ...defaultBoardState, owner: "games" }));
  obsolete.rerender(<Publisher snapshot={{ showHint: true }} />);
  obsolete.unmount();
  expect(useBoardShellStore.getState().board.owner).toBe("games");
  expect(useBoardShellStore.getState().board.showHint).toBe(false);
});

it("Strict Mode reacquires once per setup and reapplies the board after construction cleanup", () => {
  const view = render(<StrictMode><PersistentBoardShell /><Publisher /></StrictMode>);
  expect(observed.mounts).toBe(2); expect(observed.unmounts).toBe(1);
  expect(useBoardShellStore.getState().leaseActive).toBe(true);
  const session = useBoardShellStore.getState().session, baseline = boardDiagnostics();
  view.rerender(<StrictMode><PersistentBoardShell /><Publisher snapshot={{ onMove: vi.fn() }} /></StrictMode>);
  expect(useBoardShellStore.getState().session).toBe(session);
  expect(boardDiagnostics().inputCancellations).toBe(baseline.inputCancellations);
  view.unmount(); expect(useBoardShellStore.getState().leaseActive).toBe(false); expect(observed.unmounts).toBe(2);
});

it("same-FEN card changes and same-owner remounts invalidate held input", () => {
  const shell = render(<PersistentBoardShell />);
  const publisher = render(<Publisher snapshot={{ positionKey: "card-one" }} />);
  const first = observed.props!.positionIdentity, baseline = boardDiagnostics();
  publisher.rerender(<Publisher snapshot={{ positionKey: "card-two" }} />);
  expect(observed.props!.positionIdentity).not.toBe(first);
  expect(boardDiagnostics().inputCancellations).toBe(baseline.inputCancellations + 1);
  publisher.unmount(); const previous = observed.props!.positionIdentity;
  render(<Publisher snapshot={{ positionKey: "card-two" }} />);
  expect(observed.props!.positionIdentity).not.toBe(previous); expect(observed.mounts).toBe(1); shell.unmount();
});

it("read-only or unavailable input cancels the held move and ignores queued move events", () => {
  vi.useFakeTimers(); const onMove = vi.fn(); render(<PersistentBoardShell />);
  const publisher = render(<Publisher snapshot={{ onMove }} />);
  act(() => { observed.handle!.selectSquare("e2"); observed.handle!.selectSquare("e4"); });
  publisher.rerender(<Publisher snapshot={{ onMove, unavailable: "Reconnect to continue" }} />);
  expect(observed.props!.locked).toBe(true); act(() => vi.advanceTimersByTime(1));
  expect(onMove).not.toHaveBeenCalled(); vi.useRealTimers();
});

it("annotation, highlight, handler and appearance updates preserve drag without reapplying FEN", async () => {
  const view = render(<Chessboard {...boardProps} lastMove={["e7", "e5"]} expectedSan="e4" />);
  const baseline = boardDiagnostics();
  view.rerender(<Chessboard {...boardProps} lastMove={["d7", "d5"]} theme="blue" pieceSet="merida" expectedSan="e4" showHint
    shapes={[{ orig: "g1", dest: "f3", brush: "blue", label: { text: "Knight" } }]} drawnShapes={[{ orig: "a4", brush: "green" }]} />);
  await waitFor(() => expect(observed.props!.shapes).toEqual(expect.arrayContaining([expect.objectContaining({ orig: "e2", dest: "e4", brush: "yellow" })])));
  expect(observed.props!.drawnShapes).toEqual([expect.objectContaining({ orig: "a4", brush: "green" })]);
  expect(boardDiagnostics().inputCancellations).toBe(baseline.inputCancellations);
});

it("select-only and free editing retain current handlers and disable input when locked", async () => {
  const onSquareSelect = vi.fn(), onFreeMove = vi.fn();
  const view = render(<Chessboard {...boardProps} selectOnly onSquareSelect={onSquareSelect} />);
  act(() => observed.handle!.selectSquare("a4")); await waitFor(() => expect(onSquareSelect).toHaveBeenCalledExactlyOnceWith("a4"));
  view.rerender(<Chessboard {...boardProps} editMode onFreeMove={onFreeMove} onSquareSelect={onSquareSelect} />);
  expect(observed.props!.mode).toBe("free");
  act(() => observed.props!.onFreeMove?.({ from: "e2", to: "e4", positionIdentity: observed.props!.positionIdentity, positionRevision: observed.props!.positionRevision }));
  expect(onFreeMove).toHaveBeenCalledExactlyOnceWith("e2", "e4");
  view.rerender(<Chessboard {...boardProps} editMode locked onFreeMove={onFreeMove} onSquareSelect={onSquareSelect} />);
  expect(observed.props!.locked).toBe(true); act(() => observed.handle!.selectSquare("a4")); expect(onSquareSelect).toHaveBeenCalledTimes(1);
});

it("a fixed twenty-update status workload produces no new publication, lease release or reset", () => {
  const view = render(<><PersistentBoardShell /><TrainingView {...trainingProps} onMove={vi.fn()} /></>);
  const baseline = boardDiagnostics();
  for (let update = 0; update < 20; update++)
    view.rerender(<><PersistentBoardShell /><TrainingView {...trainingProps} dateLabel={`Status ${update}`} onMove={vi.fn()} /></>);
  const result = boardDiagnostics();
  for (const event of ["acquisitions", "releases", "publications", "positionResets", "inputCancellations"] as const)
    expect(result[event] - baseline[event], event).toBe(0);
});

it("deferred Chessground events from a previous card cannot grade or annotate its replacement", () => {
  vi.useFakeTimers(); const oldMove = vi.fn(), currentMove = vi.fn();
  const view = render(<Chessboard {...boardProps} positionKey="old-card" onMove={oldMove} />);
  act(() => { observed.handle!.selectSquare("e2"); observed.handle!.selectSquare("e4"); });
  view.rerender(<Chessboard {...boardProps} positionKey="current-card" onMove={currentMove} />);
  act(() => vi.advanceTimersByTime(1)); expect(oldMove).not.toHaveBeenCalled(); expect(currentMove).not.toHaveBeenCalled();
  act(() => { observed.handle!.selectSquare("g1"); observed.handle!.selectSquare("f3"); vi.advanceTimersByTime(1); });
  expect(currentMove).toHaveBeenCalledExactlyOnceWith("g1", "f3"); vi.useRealTimers();
});

it("Chessground drawing cannot mutate published annotations or shared defaults", () => {
  render(<PersistentBoardShell />); render(<Publisher />);
  const published = useBoardShellStore.getState().board.drawnShapes;
  expect(observed.props!.drawnShapes).not.toBe(published);
  (observed.props!.drawnShapes as unknown[]).push({ orig: "a4", brush: "green" });
  expect(published).toEqual([]); expect(defaultBoardState.drawnShapes).toEqual([]);
});

it("editor mode transition refreshes geometry after the setup palette moves the board", () => {
  const view = render(<Chessboard {...boardProps} editMode />);
  expect(observed.props!.mode).toBe("free");
  view.rerender(<Chessboard {...boardProps} editMode={false} />);
  expect(observed.props!.mode).toBe("legal"); expect(observed.mounts).toBe(1);
});


it("heading size changes clear hit-test bounds without canceling a held drag", () => {
  const heading = document.createElement("div"); let notify: (() => void) | undefined;
  const disconnected = vi.fn();
  vi.stubGlobal("ResizeObserver", class { constructor(private callback: () => void) {} observe(element: HTMLElement) { if (element === heading) notify = this.callback; } disconnect() { disconnected(); } });
  const view = render(<Chessboard {...boardProps} layoutAnchor={heading} />);
  const baseline = boardDiagnostics(); expect(observed.props!.layoutAnchor).toBe(heading);
  act(() => notify?.()); expect(boardDiagnostics().inputCancellations).toBe(baseline.inputCancellations);
  expect(observed.mounts).toBe(1); view.unmount(); expect(disconnected).toHaveBeenCalled();
});
