import { useLayoutEffect, useRef } from "react";
import { useBoardKeyboard, type BoardKeyboardActions } from "../../app/lib/keyboard-shortcuts";

// Keep command registration in state tests that replace only Chessground rendering.
// Real visibility, geometry and input cancellation are verified by browser specs.
export function KeyboardTestBoard({ fen, keyboard, testId, shapes, lastMove }: {
  fen: string; keyboard?: BoardKeyboardActions; testId: string;
  shapes?: unknown[]; lastMove?: readonly string[];
}) {
  const boardRef = useRef<HTMLDivElement>(null);
  useBoardKeyboard(boardRef, { ...keyboard, flip: () => undefined, help: () => undefined });
  useLayoutEffect(() => {
    if (boardRef.current) Object.defineProperty(boardRef.current, "getClientRects", { value: () => [{ width: 400 }], configurable: true });
  }, []);
  return <div ref={boardRef} data-testid={testId} data-fen={fen} data-shapes={JSON.stringify(shapes)} data-last-move={JSON.stringify(lastMove)} />;
}
