import { JSX } from "react/jsx-runtime";
import { IconButton } from "../buttons/IconButton";

export interface GoToStartProps {
  onClick: () => void;
  disabled: boolean;
}

export function GoToStartButton({
  onClick,
  disabled,
}: GoToStartProps): JSX.Element {
  return (
    <IconButton onClick={onClick} disabled={disabled} aria-label="Go to start">
      ↑
    </IconButton>
  );
}
