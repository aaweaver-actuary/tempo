import { JSX } from "react/jsx-runtime";
import { IconButton } from "../buttons/IconButton";

interface PreviousMoveButtonProps {
  onClick: () => void;
  disabled: boolean;
}

export function PreviousMoveButton({
  onClick,
  disabled,
}: PreviousMoveButtonProps): JSX.Element {
  return (
    <IconButton
      onClick={onClick}
      disabled={disabled}
      aria-label="Previous move"
    >
      ←
    </IconButton>
  );
}
