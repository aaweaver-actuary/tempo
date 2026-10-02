"use client";
import { JSX } from "react";
import { Button } from "../buttons/BaseButton";

interface FlipButtonProps {
  board: {
    unavailable?: boolean;
    onFlip?: () => void;
  };
}

export function FlipBoardButton({
  board,
}: FlipButtonProps): JSX.Element {
  return (
    <Button
      disabled={Boolean(board.unavailable)}
      aria-label="Flip board"
      title="Flip board (F)"
      onClick={() => {
        window.dispatchEvent(new Event("tempo:flip-board"));
      }}
    >
      ⇅ <span>Flip</span>
    </Button>
  );
}
