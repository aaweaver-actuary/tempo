interface AgainButtonProps {
  handleAgain: () => void;
  isAttemptFailed: boolean;
  isFeedbackComplete: boolean;
  hasNoCardsLeft: boolean;
}

export default function AgainButton({
  handleAgain,
  isAttemptFailed,
  isFeedbackComplete,
  hasNoCardsLeft,
}: AgainButtonProps) {
  return (
    <button
      onClick={handleAgain}
      disabled={isAttemptFailed || isFeedbackComplete || hasNoCardsLeft}
    >
      ⌁ <span>Show move</span>
    </button>
  );
}
