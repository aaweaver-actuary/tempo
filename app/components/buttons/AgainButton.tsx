import { Button } from "../ui";
interface AgainButtonProps {
  handleAgain: () => void;
  isAttemptFailed: boolean;
  isFeedbackComplete: boolean;
  hasNoCardsLeft: boolean;
  isReviewBlocked?: boolean;
}

export default function AgainButton({
  handleAgain,
  isAttemptFailed,
  isFeedbackComplete,
  hasNoCardsLeft,
  isReviewBlocked = false,
}: AgainButtonProps) {
  return (
    <Button
      onClick={handleAgain}
      disabled={isAttemptFailed || isFeedbackComplete || hasNoCardsLeft || isReviewBlocked}
    >
      ⌁ <span>Show move</span>
    </Button>
  );
}
