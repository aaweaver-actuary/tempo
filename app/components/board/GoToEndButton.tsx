import { JSX } from "react/jsx-runtime";
import { IconButton } from "../buttons/IconButton";

interface GoToEndButtonProps {
  onClick: () => void;
  disabled: boolean;
}

export function GoToEndButton({
  onClick,
  disabled,
}: GoToEndButtonProps): JSX.Element {
  return (
    <IconButton onClick={onClick} disabled={disabled} aria-label="Go to end">
      ↓
    </IconButton>
  );
}
