interface RetryButtonProps {
  onRetry: () => void;
}

export default function RetryButton({ onRetry }: RetryButtonProps) {
  return <button onClick={onRetry}>Retry</button>;
}
