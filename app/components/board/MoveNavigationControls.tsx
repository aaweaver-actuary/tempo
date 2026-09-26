import { JSX } from "react/jsx-runtime";
import { GoToStartButton } from "./GoToStartButton";
import { PreviousMoveButton } from "./PreviousMoveButton";
import { MoveNumberDisplay } from "./MoveNumberDisplay";
import { NextMoveButton } from "./NextMoveButton";
import { GoToEndButton } from "./GoToEndButton";

interface MoveNavigationControlsProps {
  cursor: number;
  length: number;
  onChange: (cursor: number) => void;
}

export function MoveNavigationControls({
  cursor,
  length,
  onChange,
}: MoveNavigationControlsProps): JSX.Element {
  function clickGoStart() {
    onChange(0);
  }

  function clickPreviousMove() {
    onChange(Math.max(0, cursor - 1));
  }

  function isPreviousMoveDisabled() {
    return cursor === 0;
  }

  function clickNextMove() {
    onChange(Math.min(length, cursor + 1));
  }

  function clickGoEnd() {
    onChange(length);
  }

  function isNextMoveDisabled() {
    return cursor === length;
  }

  return (
    <div className="move-navigator" aria-label="Move navigation">
      <GoToStartButton
        onClick={clickGoStart}
        disabled={isPreviousMoveDisabled()}
      />
      <PreviousMoveButton
        onClick={clickPreviousMove}
        disabled={isPreviousMoveDisabled()}
      />
      <MoveNumberDisplay cursor={cursor} length={length} />
      <NextMoveButton onClick={clickNextMove} length={length} cursor={cursor} />
      <GoToEndButton onClick={clickGoEnd} disabled={isNextMoveDisabled()} />
    </div>
  );
}
