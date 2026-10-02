import { useRef, useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import { Chessboard } from "../../app/components/board/chessboard";
import { PersistentBoardShell } from "../../app/components/board/persistent-board-shell";
import TrainingView from "../../app/views/training_view";
import { useTrainingStore } from "../../app/state/training-store";
import { asCardId, asFenString, asSanMove } from "../../app/types";
import { positionsFromMoves, useBoardHistory } from "../../app/hooks/use-board-history";
import { letterShortcutsEnabled, setLetterShortcutsEnabled, usePopupKeyboard } from "../../app/lib/keyboard-shortcuts";
import { Dialog } from "../../app/components/dialog";

vi.mock("@lichess-org/chessground", () => ({ Chessground: () => ({ set: vi.fn(), cancelMove: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn(), setAutoShapes: vi.fn(), setShapes: vi.fn() }) }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn() }));
const initialFen = new Chess().fen();
const firstPosition = positionsFromMoves(initialFen, ["e4"])[1];
const boardProps = { fen: initialFen, locked: false, showHint: false, theme: "brown" as const, pieceSet: "cburnett" as const, onMove: vi.fn() };
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockImplementation(function (this: HTMLElement) { return (this.closest('[hidden]') ? [] : [{ width: 400 }]) as unknown as DOMRectList; });
});

it("static preview boards preserve browser navigation defaults before and after activation", () => {
  render(<Chessboard {...boardProps} locked />);
  const frame = document.querySelector(".board-frame")!;
  for (const activated of [false, true]) {
    if (activated) fireEvent.pointerDown(document.querySelector(".board-viewport")!);
    for (const key of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"]) {
      const event = new KeyboardEvent("keydown", { key, cancelable: true });
      act(() => window.dispatchEvent(event));
      expect(event.defaultPrevented, `${key}, activated: ${activated}`).toBe(false);
      expect(frame.getAttribute("data-fen")).toBe(initialFen);
    }
  }
  fireEvent.keyDown(window, { key: "f" });
  expect(frame.getAttribute("data-orientation")).toBe("black");
  fireEvent.keyDown(window, { key: "r" });
  expect(frame.getAttribute("data-orientation")).toBe("white");
  fireEvent.keyDown(window, { key: "?" });
  expect(screen.getByRole("dialog")).toBeTruthy();
});

it("navigable boards consume browser navigation at the revealed frontier", () => {
  function RevealedBoard() {
    const history = useBoardHistory("revealed", [initialFen, firstPosition], firstPosition);
    return <Chessboard {...boardProps} fen={history.fen} locked={history.viewingHistory} keyboard={history.keyboard} />;
  }
  render(<RevealedBoard />);
  const frame = document.querySelector(".board-frame")!;
  for (const key of ["ArrowRight", "End", "ArrowDown"]) {
    const event = new KeyboardEvent("keydown", { key, cancelable: true });
    act(() => window.dispatchEvent(event));
    expect(event.defaultPrevented, key).toBe(true);
    expect(frame.getAttribute("data-fen")).toBe(firstPosition);
  }
  fireEvent.keyDown(window, { key: "Home" });
  expect(frame.getAttribute("data-fen")).toBe(initialFen);
  const event = new KeyboardEvent("keydown", { key: "ArrowLeft", cancelable: true });
  act(() => window.dispatchEvent(event));
  expect(event.defaultPrevented).toBe(true);
  expect(frame.getAttribute("data-fen")).toBe(initialFen);
});

it("navigation never crosses the revealed frontier of an unanswered training card", () => {
  const grade = vi.fn(async () => undefined); const restart = vi.fn(); const move = vi.fn();
  useTrainingStore.setState({ currentFenString: asFenString(firstPosition), step: 1, feedback: "ready", attempt: { entryKey: "keyboard-card", generation: 1, phase: "playerTurn" } });
  render(<><PersistentBoardShell /><TrainingView useSharedBoard dateLabel="Today" serviceError="" refreshDatabaseQueue={vi.fn()} cardsLeft={1}
    card={{ id: asCardId("keyboard-card"), kind: "opening", title: "Keyboard card", subtitle: "", startingFen: asFenString(initialFen),
      moves: [asSanMove("e4"), asSanMove("e5"), asSanMove("Nf3")], userMoveTarget: 2, orientation: "white" }}
    boardTheme="brown" pieceSet="cburnett" rateCard={grade} resetCardAttempt={restart} handleAttemptFailure={vi.fn()} setEditorCard={vi.fn()} onMove={move} /></>);
  const frame = document.querySelector('.board-frame')!;
  fireEvent.keyDown(window, { key: "ArrowRight" }); fireEvent.keyDown(window, { key: "ArrowDown" });
  expect(frame.getAttribute("data-fen")).toBe(firstPosition);
  fireEvent.keyDown(window, { key: "ArrowLeft" });
  expect(frame.getAttribute("data-fen")).toBe(initialFen);
  expect(frame.getAttribute("data-input-enabled")).toBe("false");
  fireEvent.keyDown(window, { key: "f" });
  expect(frame.getAttribute("data-orientation")).toBe("black");
  fireEvent.keyDown(window, { key: "r" });
  expect(frame.getAttribute("data-fen")).toBe(firstPosition);
  expect(frame.getAttribute("data-orientation")).toBe("white");
  expect(frame.getAttribute("data-input-enabled")).toBe("true");
  expect(useTrainingStore.getState().step).toBe(1);
  expect(grade).not.toHaveBeenCalled(); expect(restart).not.toHaveBeenCalled(); expect(move).not.toHaveBeenCalled();
  act(() => useTrainingStore.setState({ feedback: "complete" }));
  fireEvent.keyDown(window, { key: "End" });
  expect(frame.getAttribute("data-fen")).toBe(positionsFromMoves(initialFen, ["e4", "e5", "Nf3"]).at(-1));
  fireEvent.keyDown(window, { key: "r" });
  expect(frame.getAttribute("data-fen")).toBe(firstPosition);
  expect(grade).not.toHaveBeenCalled(); expect(restart).not.toHaveBeenCalled();
});

it("R restores a custom-FEN live decision while history remains read-only and attempts advance independently", () => {
  const root = positionsFromMoves(initialFen, ["d4"])[1];
  const positions = positionsFromMoves(root, ["d5", "c4"]);
  function Exercise({ liveCursor }: { liveCursor: number }) {
    const history = useBoardHistory("exercise", positions.slice(0, liveCursor + 1), positions[liveCursor]);
    return <Chessboard {...boardProps} fen={history.fen} locked={history.viewingHistory} keyboard={history.keyboard} orientation="black" />;
  }
  const view = render(<Exercise liveCursor={1} />);
  fireEvent.keyDown(window, { key: "Home" });
  expect(document.querySelector('.board-frame')?.getAttribute("data-fen")).toBe(root);
  view.rerender(<Exercise liveCursor={2} />);
  expect(document.querySelector('.board-frame')?.getAttribute("data-fen")).toBe(root);
  fireEvent.keyDown(window, { key: "r" });
  expect(document.querySelector('.board-frame')?.getAttribute("data-fen")).toBe(positions[2]);
  expect(document.querySelector('.board-frame')?.getAttribute("data-input-enabled")).toBe("true");
});

it("only the last active visible board handles keys and popup boards fence the background", () => {
  const firstFlip = vi.fn(); const secondFlip = vi.fn(); const popupFlip = vi.fn();
  const view = render(<><Chessboard {...boardProps} onFlip={firstFlip} /><Chessboard {...boardProps} onFlip={secondFlip} keyboard={{ defaultActive: true }} />
    <div hidden><Chessboard {...boardProps} onFlip={vi.fn()} /></div></>);
  fireEvent.keyDown(window, { key: "f" }); expect(secondFlip).toHaveBeenCalledOnce(); expect(firstFlip).not.toHaveBeenCalled();
  fireEvent.pointerDown(document.querySelector('.board-viewport')!);
  fireEvent.keyDown(window, { key: "f" }); expect(firstFlip).toHaveBeenCalledOnce();
  view.rerender(<><Chessboard {...boardProps} onFlip={firstFlip} /><Dialog titleId="popup-title" onClose={vi.fn()}><h2 id="popup-title">Popup</h2>
    <Chessboard {...boardProps} onFlip={popupFlip} /></Dialog></>);
  fireEvent.keyDown(window, { key: "f" }); expect(popupFlip).toHaveBeenCalledOnce(); expect(firstFlip).toHaveBeenCalledOnce();
});

it("typing widgets modifiers IME and handled events preserve their keyboard behavior", () => {
  const flip = vi.fn(); const next = vi.fn();
  render(<><Chessboard {...boardProps} onFlip={flip} keyboard={{ capturesNavigation: true, next }} /><input aria-label="Text" /><div contentEditable><span>Editable child</span></div>
    <div role="tablist"><button>Tab</button></div><div role="separator" tabIndex={0}>Split</div></>);
  fireEvent.keyDown(screen.getByLabelText("Text"), { key: "f" });
  fireEvent.keyDown(screen.getByText("Editable child"), { key: "f" });
  fireEvent.keyDown(screen.getByRole("separator"), { key: "ArrowRight" });
  fireEvent.keyDown(screen.getByText("Tab"), { key: "ArrowRight" });
  fireEvent.keyDown(window, { key: "f", ctrlKey: true }); fireEvent.keyDown(window, { key: "f", metaKey: true });
  fireEvent.keyDown(window, { key: "f", altKey: true }); fireEvent.keyDown(window, { key: "f", isComposing: true });
  fireEvent.keyDown(window, { key: "f", keyCode: 229 });
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  const range = document.createRange(); range.selectNodeContents(screen.getByText("Split")); selection.addRange(range);
  fireEvent.keyDown(window, { key: "ArrowRight" }); fireEvent.keyDown(window, { key: "f" }); selection.removeAllRanges();
  const prevented = new KeyboardEvent("keydown", { key: "ArrowRight", cancelable: true }); prevented.preventDefault(); window.dispatchEvent(prevented);
  expect(flip).not.toHaveBeenCalled(); expect(next).not.toHaveBeenCalled();
});

it("letter-shortcut preference persists while arrows Escape and contextual help remain active", () => {
  const flip = vi.fn(); const next = vi.fn();
  render(<Chessboard {...boardProps} onFlip={flip} keyboard={{ capturesNavigation: true, next }} />);
  act(() => setLetterShortcutsEnabled(false)); expect(letterShortcutsEnabled()).toBe(false); expect(localStorage.getItem("tempo-letter-shortcuts")).toBe("false");
  fireEvent.keyDown(window, { key: "f" }); expect(flip).not.toHaveBeenCalled();
  fireEvent.keyDown(window, { key: "ArrowRight" }); expect(next).toHaveBeenCalledOnce();
  fireEvent.keyDown(window, { key: "?", shiftKey: true }); expect(screen.getByRole("dialog")).toBeTruthy();
  fireEvent.keyDown(window, { key: "Escape" }); expect(screen.queryByRole("dialog")).toBeNull();
});

it("Hint and Next reuse only enabled actions and holding a letter does not repeat it", () => {
  const hint = vi.fn(); const nextItem = vi.fn();
  const view = render(<Chessboard {...boardProps} keyboard={{ hint, nextItem }} />);
  fireEvent.keyDown(window, { key: "h" }); fireEvent.keyDown(window, { key: "n" }); fireEvent.keyDown(window, { key: "n", repeat: true });
  expect(hint).toHaveBeenCalledOnce(); expect(nextItem).toHaveBeenCalledOnce();
  view.rerender(<Chessboard {...boardProps} keyboard={{}} />);
  fireEvent.keyDown(window, { key: "h" }); fireEvent.keyDown(window, { key: "n" });
  expect(hint).toHaveBeenCalledOnce(); expect(nextItem).toHaveBeenCalledOnce();
});

it("letter shortcuts can be disabled immediately when browser storage writes fail", () => {
  const blockedStorage = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Quota"); });
  setLetterShortcutsEnabled(false);
  expect(letterShortcutsEnabled()).toBe(false);
  blockedStorage.mockRestore(); setLetterShortcutsEnabled(true);
});

it("Escape closes one topmost popup per press and passive toasts do not block board commands", () => {
  const flip = vi.fn();
  function Layers() {
    const [outer, setOuter] = useState(true); const [inner, setInner] = useState(true); const [toast, setToast] = useState(true);
    const toastRef = useRef<HTMLDivElement>(null); usePopupKeyboard(toastRef, () => setToast(false), toast, true);
    return <><Chessboard {...boardProps} onFlip={flip} />{toast && <div ref={toastRef}>Toast</div>}
      {outer && <Dialog titleId="outer" onClose={() => setOuter(false)}><h2 id="outer">Outer</h2></Dialog>}
      {inner && <Dialog titleId="inner" onClose={() => setInner(false)}><h2 id="inner">Inner</h2></Dialog>}</>;
  }
  render(<Layers />);
  fireEvent.keyDown(window, { key: "Escape" }); expect(screen.queryByText("Inner")).toBeNull(); expect(screen.getByText("Outer")).toBeTruthy();
  fireEvent.keyDown(window, { key: "Escape", repeat: true }); expect(screen.getByText("Outer")).toBeTruthy();
  fireEvent.keyDown(window, { key: "Escape" }); expect(screen.queryByText("Outer")).toBeNull();
  fireEvent.keyDown(window, { key: "f" }); expect(flip).toHaveBeenCalledOnce();
  fireEvent.keyDown(window, { key: "Escape" }); expect(screen.queryByText("Toast")).toBeNull();
});
