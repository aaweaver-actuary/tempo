import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Chess, type Square } from "chess.js";
import type { Config } from "@lichess-org/chessground/config";
import { Chessboard } from "../../app/components/board/chessboard";
import { PersistentBoardShell } from "../../app/components/board/persistent-board-shell";
import { PositionSolutionBoard, PositionSolutionTabs } from "../../app/components/position-solution-editor";
import { usePositionSolutionEditor } from "../../app/hooks/use-position-solution-editor";
import { useBoardPublisher } from "../../app/hooks/use-board-publisher";
import { playChessMoveSound } from "../../app/lib/move-sound";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import StudyExerciseRunner from "../../app/views/study_exercise_runner";

const boardApi = vi.hoisted(() => ({ state: { dom: { bounds: { clear: vi.fn() } } }, set: vi.fn(),
  cancelMove: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn(), setAutoShapes: vi.fn(), setShapes: vi.fn() }));
const createBoard = vi.hoisted(() => vi.fn((element: unknown, config: Config) => { void element; void config; return boardApi; }));
vi.mock("@lichess-org/chessground", () => ({ Chessground: createBoard }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  useBoardShellStore.setState(useBoardShellStore.getInitialState(), true);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});
afterEach(() => vi.unstubAllGlobals());

function playBoardInput(from: Square, to: Square) {
  const configuration = boardApi.set.mock.calls.findLast(([config]) => (config as Config).movable?.events?.after)?.[0] as Config | undefined;
  const after = configuration?.movable?.events?.after ?? createBoard.mock.calls.at(-1)?.[1].movable?.events?.after;
  expect(after).toBeTypeOf("function");
  act(() => after!(from, to, { premove: false }));
}

const whitePromotionFen = "7k/P7/8/8/8/8/8/7K w - - 0 1";
const promotionCases = [
  { fen: whitePromotionFen, from: "a7", to: "a8", checkingPieces: ["q", "r"] },
  { fen: "7k/8/8/8/8/8/p7/7K b - - 0 1", from: "a2", to: "a1", checkingPieces: ["q", "r"] },
  { fen: "8/P7/8/8/8/7K/8/7k w - - 0 1", from: "a7", to: "a8", checkingPieces: ["q", "b"] },
].flatMap((position) => ["q", "r", "b", "n"].flatMap((promotion) => [false, true].map((shared) => ({
  ...position, promotion, shared, givesCheck: position.checkingPieces.includes(promotion),
}))));

it.each(promotionCases)("Study underpromotion board feedback uses the selected promotion $promotion at $fen with shared=$shared", async ({ fen, from, to, promotion, shared, givesCheck }) => {
  const submissions: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/present")) return Response.json({ id: "feedback-exercise", revision: 1,
      type: "move_line", mode: "single", prompt: "Choose the actual promotion", hint: "", fen });
    if (String(input).endsWith("/attempts")) {
      submissions.push(JSON.parse(String(init?.body)));
      return Response.json({ attempt_id: "feedback-attempt", assessment: { outcome: "correct", feedback: "Saved actual move" } });
    }
    return Response.json({ specification: { type: "move_line", prompt: "Choose the actual promotion", mode: "single",
      accepted_lines: [[`${from}${to}${promotion}`]] } });
  }));
  const view = render(<>{shared && <PersistentBoardShell />}<StudyExerciseRunner studyId="feedback-study"
    exerciseId="feedback-exercise" boardTheme="brown" pieceSet="cburnett" useSharedBoard={shared} /></>);
  await screen.findByText("Choose the actual promotion");
  fireEvent.change(screen.getByLabelText("Promotion"), { target: { value: promotion } });
  playBoardInput(from as Square, to as Square);
  const expectedPosition = new Chess(fen);
  expectedPosition.move({ from, to, promotion });
  expect(view.container.querySelector(".board-frame")?.getAttribute("data-fen")).toBe(expectedPosition.fen());
  expect(playChessMoveSound).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ from, to, promotion, captured: undefined }), givesCheck);
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  await screen.findByText("Saved actual move");
  expect(submissions).toMatchObject([{ answer: { type: "move_line", moves: [`${from}${to}${promotion}`] } }]);
});

it.each([
  { fen: new Chess().fen(), from: "e2", to: "e4", captured: undefined, givesCheck: false },
  { fen: "7k/8/8/8/8/8/1p6/KR6 w - - 0 1", from: "b1", to: "b2", captured: "p", givesCheck: false },
  { fen: "7k/8/8/8/8/8/8/KR6 w - - 0 1", from: "b1", to: "h1", captured: undefined, givesCheck: true },
  { fen: whitePromotionFen, from: "a7", to: "a8", captured: undefined, givesCheck: true },
])("ordinary board feedback retains move capture check and default queen semantics for $from$to", ({ fen, from, to, captured, givesCheck }) => {
  const onMove = vi.fn();
  render(<Chessboard fen={fen} locked={false} showHint={false} theme="brown" pieceSet="cburnett" onMove={onMove} />);
  playBoardInput(from as Square, to as Square);
  expect(onMove).toHaveBeenCalledExactlyOnceWith(from, to);
  expect(playChessMoveSound).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ from, to, captured,
    promotion: from === "a7" ? "q" : undefined }), givesCheck);
});

function SolutionEditor() {
  const editor = usePositionSolutionEditor(whitePromotionFen);
  return <><PositionSolutionTabs editor={editor} /><PositionSolutionBoard editor={editor} theme="brown" pieceSet="cburnett" /></>;
}

it("solution editor board feedback uses its existing promotion selector", () => {
  const view = render(<SolutionEditor />);
  fireEvent.click(screen.getByRole("button", { name: "Solution" }));
  fireEvent.change(screen.getByLabelText("Promotion"), { target: { value: "n" } });
  playBoardInput("a7", "a8");
  expect(screen.getByRole("button", { name: "1.a8=N" })).toBeTruthy();
  expect(view.container.querySelector(".board-frame")?.getAttribute("data-fen")).toBe("N6k/8/8/8/8/8/8/7K b - - 0 1");
  expect(playChessMoveSound).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ promotion: "n" }), false);
});

function BoardOwner({ owner, promotion }: { owner: "train" | "games"; promotion?: "q" | "r" | "b" | "n" }) {
  useBoardPublisher(owner, { fen: whitePromotionFen, interactionMode: "legal", ...(promotion ? { promotion } : {}), onMove: vi.fn() });
  return null;
}

it("promotion metadata updates preserve the board and a new owner defaults to queen", () => {
  const view = render(<><PersistentBoardShell /><BoardOwner owner="train" promotion="n" /></>);
  boardApi.cancelMove.mockClear();
  view.rerender(<><PersistentBoardShell /><BoardOwner owner="train" promotion="r" /></>);
  expect(createBoard).toHaveBeenCalledTimes(1);
  expect(boardApi.cancelMove).not.toHaveBeenCalled();
  view.rerender(<><PersistentBoardShell /><BoardOwner owner="games" /></>);
  playBoardInput("a7", "a8");
  expect(playChessMoveSound).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ promotion: "q" }), true);
});
