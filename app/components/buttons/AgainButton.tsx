interface AgainButtonProps {
  handleAgain: () => void;
  isAttemptFailed: boolean;
  isFeedbackComplete: boolean;
  hasNoCardsLeft: boolean;
  showHint: boolean;
}

export default function AgainButton({
  handleAgain,
  isAttemptFailed,
  isFeedbackComplete,
  hasNoCardsLeft,
  showHint,
}: AgainButtonProps) {
  return (
    <button
      onClick={handleAgain}
      disabled={isAttemptFailed || isFeedbackComplete || hasNoCardsLeft}
    >
      ⌁ <span>{showHint ? "Hide move" : "Show move"}</span>
    </button>
  );
}
