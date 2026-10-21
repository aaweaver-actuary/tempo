const PENDING_KEY = "tempo-pending-prefix-split-v1";

export function readPendingPrefixSplitRecord(): unknown {
  const stored = localStorage.getItem(PENDING_KEY);
  return stored ? JSON.parse(stored) : undefined;
}

export function writePendingPrefixSplitRecord(record: { operationId: string; decision: "accept" | "reject"; cardId: string; revision: number }): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(record));
}

export function clearPendingPrefixSplitRecord(): void {
  localStorage.removeItem(PENDING_KEY);
}
