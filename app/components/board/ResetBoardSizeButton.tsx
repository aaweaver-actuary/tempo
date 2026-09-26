"use client";
import { JSX } from "react";
import { Button } from "../buttons/BaseButton";

interface ResetBoardSizeButtonProps {
  onClick: () => void;
}

export function ResetBoardSizeButton({
  onClick,
}: ResetBoardSizeButtonProps): JSX.Element {
  return (
    <Button
      aria-label="Reset board size"
      title="Reset board size"
      onClick={onClick}
    >
      ↺
    </Button>
  );
}
