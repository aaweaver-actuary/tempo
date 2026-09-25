interface RestartButtonProps {
  handleRestart: () => void;
  disabled?: boolean;
}

export default function RestartButton({ handleRestart, disabled = false }: RestartButtonProps) {
  return (
    <button onClick={handleRestart} disabled={disabled}>
      ↻ <span>Restart</span>
    </button>
  );
}
