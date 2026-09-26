import { Button } from "../ui";
interface RetryButtonProps {
  onRetry: () => void;
}

export default function RetryButton({ onRetry }: RetryButtonProps) {
  return <Button onClick={onRetry}>Retry</Button>;
}
