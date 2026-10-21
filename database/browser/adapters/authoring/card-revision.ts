const PENDING_KEY = "tempo-pending-card-revision-v1";

export function readPendingCardRevisionRecord(): unknown {
  const stored = localStorage.getItem(PENDING_KEY);
  return stored ? JSON.parse(stored) : undefined;
}

export function writePendingCardRevisionRecord(record: { operationId: string; request: string }): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(record));
}

export function clearPendingCardRevisionRecord(): void {
  localStorage.removeItem(PENDING_KEY);
}
