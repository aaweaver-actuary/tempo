interface RestartButtonProps {
  handleRestart: () => void;
}

export default function RestartButton({ handleRestart }: RestartButtonProps) {
  return (
    <button onClick={handleRestart}>
      ↻ <span>Restart</span>
    </button>
  );
}
