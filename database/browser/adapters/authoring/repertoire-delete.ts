const PENDING_KEY = "tempo-pending-repertoire-delete-v1";

export function readPendingRepertoireDeletionRecord(): unknown {
  const stored = localStorage.getItem(PENDING_KEY);
  return stored ? JSON.parse(stored) : undefined;
}

export function writePendingRepertoireDeletionRecord(record: { operationId: string; repertoireId: string; learnedCards: "keep" | "delete" }): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(record));
}

export function clearPendingRepertoireDeletionRecord(): void {
  localStorage.removeItem(PENDING_KEY);
}
