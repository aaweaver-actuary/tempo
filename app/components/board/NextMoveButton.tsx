import { JSX } from "react/jsx-runtime";
import { IconButton } from "../buttons/IconButton";

export function NextMoveButton({
  onClick, length, cursor,
}: {
  onClick: () => void;
  length: number;
  cursor: number;
}): JSX.Element {
  return (
    <IconButton
      onClick={onClick}
      disabled={cursor === length}
      aria-label="Next move"
    >
      →
    </IconButton>
  );
}
