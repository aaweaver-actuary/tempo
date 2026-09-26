import { Button } from "../ui";
interface RestartButtonProps {
  handleRestart: () => void;
  disabled?: boolean;
}

export default function RestartButton({ handleRestart, disabled = false }: RestartButtonProps) {
  return (
    <Button onClick={handleRestart} disabled={disabled}>
      ↻ <span>Restart</span>
    </Button>
  );
}
