import { JSX } from "react/jsx-runtime";

interface MoveNumberDisplayProps {
  cursor: number;
  length: number;
}

export function MoveNumberDisplay({
  cursor: cursor,
  length: length,
}: MoveNumberDisplayProps): JSX.Element {
  return (
    <span>
      {cursor} / {length}
    </span>
  );
}
