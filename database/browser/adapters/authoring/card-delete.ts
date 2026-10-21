const PENDING_KEY = "tempo-pending-card-delete-v1";

export function readPendingCardDeletionBytes(): string | null {
  return localStorage.getItem(PENDING_KEY);
}

export function readPendingCardDeletionRecord(): unknown {
  const stored = readPendingCardDeletionBytes();
  return stored ? JSON.parse(stored) : undefined;
}

export function readPendingCardEditBytes(): string | null {
  return localStorage.getItem("tempo-pending-card-revision-v1") || localStorage.getItem("tempo-pending-prefix-split-v1");
}

export function writePendingCardDeletionRecord(record: { operationId: string; cardId: string; expectedRevision: number }): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(record));
}

export function clearPendingCardDeletionRecord(): void {
  localStorage.removeItem(PENDING_KEY);
}
