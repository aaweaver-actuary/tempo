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

const api = vi.hoisted(() => ({ set: vi.fn(), cancelMove: vi.fn(), destroy: vi.fn(),
  redrawAll: vi.fn(), setAutoShapes: vi.fn(), setShapes: vi.fn() }));
const createBoard = vi.hoisted(() => vi.fn((element: unknown, config: unknown) => { void element; void config; return api; }));
vi.mock("@lichess-org/chessground", () => ({ Chessground: createBoard }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn() }));
const boardProps = { fen: STANDARD_FEN, locked: false, showHint: false,
  theme: "brown" as const, pieceSet: "cburnett" as const, onMove: vi.fn() };
const card = { id: asCardId("drag-card"), kind: "opening" as const, title: "Drag regression", subtitle: "",
  startingFen: asFenString(STANDARD_FEN), moves: [asSanMove("e4")], userMoveTarget: 1, orientation: "white" as const };
const trainingProps = { dateLabel: "Today", serviceError: "", refreshDatabaseQueue: vi.fn(), cardsLeft: 1,
  card, boardTheme: "brown" as const, pieceSet: "cburnett" as const, rateCard: vi.fn(async () => undefined),
  handleAttemptFailure: vi.fn(), resetCardAttempt: vi.fn(), setEditorCard: vi.fn(), useSharedBoard: true };

beforeEach(() => {
  vi.clearAllMocks();
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
  api.cancelMove.mockClear(); api.set.mockClear();
  for (let update = 0; update < 10; update++)
    view.rerender(<Chessboard {...boardProps} lastMove={["e7", "e5"]} onMove={vi.fn()} />);
  expect(api.cancelMove).not.toHaveBeenCalled();
  expect(api.set).not.toHaveBeenCalled();
});

it("training parent status updates keep the lease and forward the latest handler exactly once", () => {
  const firstMove = vi.fn(); const currentMove = vi.fn();
  const view = render(<><PersistentBoardShell /><TrainingView {...trainingProps} onMove={firstMove} /></>);
  const initialHandler = useBoardShellStore.getState().board.onMove;
  const currentAfter = api.set.mock.calls.findLast(([config]) => config.movable?.events?.after)?.[0].movable.events.after;
  const snapshots: string[] = [];
  const unsubscribe = useBoardShellStore.subscribe((state) => snapshots.push(state.board.interactionMode));
  api.cancelMove.mockClear(); api.set.mockClear();
  view.rerender(<><PersistentBoardShell /><TrainingView {...trainingProps} dateLabel="Updated status" onMove={currentMove} /></>);
  expect(snapshots).not.toContain("readonly");
  expect(useBoardShellStore.getState().board.onMove).toBe(initialHandler);
  expect(api.cancelMove).not.toHaveBeenCalled();
  const config = createBoard.mock.calls.at(-1)![1] as { movable: { events: { after: (from: string, to: string) => void } } };
  act(() => (currentAfter ?? config.movable.events.after)("e2", "e4"));
  expect(firstMove).not.toHaveBeenCalled();
  expect(currentMove).toHaveBeenCalledExactlyOnceWith("e2", "e4");
  unsubscribe();
});

it("a wrong attempt returning to the same FEN explicitly resets through position revision", () => {
  const view = render(<Chessboard {...boardProps} positionRevision={0} />);
  api.cancelMove.mockClear(); api.set.mockClear();
  view.rerender(<Chessboard {...boardProps} positionRevision={1} />);
  expect(api.cancelMove).toHaveBeenCalledTimes(1);
  expect(api.set).toHaveBeenCalledWith(expect.objectContaining({ fen: STANDARD_FEN }));
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
  expect(createBoard).toHaveBeenCalledTimes(2);
  expect(api.destroy).toHaveBeenCalledTimes(1);
  expect(useBoardShellStore.getState().leaseActive).toBe(true);
  const initialSession = useBoardShellStore.getState().session;
  api.cancelMove.mockClear();
  view.rerender(<StrictMode><PersistentBoardShell /><Publisher snapshot={{ onMove: vi.fn() }} /></StrictMode>);
  expect(useBoardShellStore.getState().session).toBe(initialSession);
  expect(api.cancelMove).not.toHaveBeenCalled();
  view.unmount();
  expect(useBoardShellStore.getState().leaseActive).toBe(false);
  expect(api.destroy).toHaveBeenCalledTimes(2);
});

it("same-FEN card changes and same-owner remounts invalidate held input", () => {
  const shell = render(<PersistentBoardShell />);
  const publisher = render(<Publisher snapshot={{ positionKey: "card-one" }} />);
  api.cancelMove.mockClear();
  publisher.rerender(<Publisher snapshot={{ positionKey: "card-two" }} />);
  expect(api.cancelMove).toHaveBeenCalledTimes(1);
  publisher.unmount();
  api.cancelMove.mockClear();
  render(<Publisher snapshot={{ positionKey: "card-two" }} />);
  expect(api.cancelMove).toHaveBeenCalledTimes(1);
  expect(createBoard).toHaveBeenCalledTimes(1);
  shell.unmount();
});

it("read-only or unavailable input cancels the held move and ignores queued move events", () => {
  const onMove = vi.fn();
  render(<PersistentBoardShell />);
  const publisher = render(<Publisher snapshot={{ onMove }} />);
  api.cancelMove.mockClear();
  publisher.rerender(<Publisher snapshot={{ onMove, unavailable: "Reconnect to continue" }} />);
  expect(api.cancelMove).toHaveBeenCalledTimes(1);
  expect(api.set).toHaveBeenLastCalledWith(expect.objectContaining({ draggable: { enabled: false, showGhost: true } }));
  const config = createBoard.mock.calls.at(-1)![1] as { movable: { events: { after: (from: string, to: string) => void } } };
  act(() => config.movable.events.after("e2", "e4"));
  expect(onMove).not.toHaveBeenCalled();
});

it("annotation, highlight, handler and appearance updates preserve drag without reapplying FEN", async () => {
  const view = render(<Chessboard {...boardProps} lastMove={["e7", "e5"]} expectedSan="e4" />);
  api.cancelMove.mockClear(); api.set.mockClear();
  view.rerender(<Chessboard {...boardProps} lastMove={["d7", "d5"]} theme="blue" pieceSet="merida"
    expectedSan="e4" showHint shapes={[{ orig: "g1", dest: "f3", brush: "blue", label: { text: "Knight" } }]}
    drawnShapes={[{ orig: "a4", brush: "green" }]} onMove={vi.fn()} />);
  await waitFor(() => expect(api.setAutoShapes).toHaveBeenLastCalledWith(expect.arrayContaining([
    { orig: "e2", dest: "e4", brush: "yellow" },
  ])));
  expect(api.cancelMove).not.toHaveBeenCalled();
  expect(api.set.mock.calls.every(([config]) => !("fen" in config))).toBe(true);
  expect(api.setShapes).toHaveBeenLastCalledWith([{ orig: "a4", brush: "green" }]);
});

it("select-only and free editing retain current handlers and disable input when locked", () => {
  const onSquareSelect = vi.fn(); const onFreeMove = vi.fn();
  const view = render(<Chessboard {...boardProps} selectOnly onSquareSelect={onSquareSelect} />);
  const config = createBoard.mock.calls.at(-1)![1] as {
    events: { select: (square: string) => void }; movable: { events: { after: (from: string, to: string) => void } } };
  act(() => config.events.select("a4"));
  expect(onSquareSelect).toHaveBeenCalledExactlyOnceWith("a4");
  view.rerender(<Chessboard {...boardProps} editMode onFreeMove={onFreeMove} onSquareSelect={onSquareSelect} />);
  const currentAfter = api.set.mock.calls.at(-1)![0].movable.events.after;
  act(() => currentAfter("e2", "e4"));
  expect(onFreeMove).toHaveBeenCalledExactlyOnceWith("e2", "e4");
  view.rerender(<Chessboard {...boardProps} editMode locked onFreeMove={onFreeMove} onSquareSelect={onSquareSelect} />);
  act(() => { config.events.select("a4"); config.movable.events.after("e2", "e4"); });
  expect(onSquareSelect).toHaveBeenCalledTimes(1);
  expect(onFreeMove).toHaveBeenCalledTimes(1);
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
  const oldMove = vi.fn(); const currentMove = vi.fn(); const oldDrawing = vi.fn(); const currentDrawing = vi.fn();
  const view = render(<Chessboard {...boardProps} positionKey="old-card" onMove={oldMove} onDrawnShapesChange={oldDrawing} />);
  const initialConfig = createBoard.mock.calls.at(-1)![1] as {
    movable: { events: { after: (from: string, to: string) => void } };
    drawable: { onChange: (shapes: unknown[]) => void } };
  const queuedMove = initialConfig.movable.events.after;
  const queuedDrawing = initialConfig.drawable.onChange;
  view.rerender(<Chessboard {...boardProps} positionKey="current-card" onMove={currentMove} onDrawnShapesChange={currentDrawing} />);
  act(() => { queuedMove("e2", "e4"); queuedDrawing([{ orig: "a4", brush: "red" }]); });
  expect(oldMove).not.toHaveBeenCalled(); expect(currentMove).not.toHaveBeenCalled();
  expect(oldDrawing).not.toHaveBeenCalled(); expect(currentDrawing).not.toHaveBeenCalled();
  const currentConfig = api.set.mock.calls.at(-1)![0];
  act(() => { currentConfig.movable.events.after("e2", "e4"); currentConfig.drawable.onChange([{ orig: "b4", brush: "green" }]); });
  expect(currentMove).toHaveBeenCalledExactlyOnceWith("e2", "e4");
  expect(currentDrawing).toHaveBeenCalledExactlyOnceWith([{ orig: "b4", brush: "green" }]);
});

it("Chessground drawing cannot mutate published annotations or shared defaults", () => {
  render(<PersistentBoardShell />);
  render(<Publisher />);
  const publishedShapes = useBoardShellStore.getState().board.drawnShapes;
  const imperativeShapes = api.setShapes.mock.calls.at(-1)![0];
  expect(imperativeShapes).not.toBe(publishedShapes);
  imperativeShapes.push({ orig: "a4", brush: "green" });
  expect(publishedShapes).toEqual([]);
  expect(defaultBoardState.drawnShapes).toEqual([]);
});

it("editor mode transition refreshes geometry after the setup palette moves the board", () => {
  const view = render(<Chessboard {...boardProps} editMode />);
  api.redrawAll.mockClear();
  view.rerender(<Chessboard {...boardProps} editMode={false} />);
  expect(api.redrawAll).toHaveBeenCalledOnce();
  expect(api.set).toHaveBeenLastCalledWith(expect.objectContaining({ movable: expect.objectContaining({ free: false }) }));
});
