const PENDING_KEY = "tempo-pending-analysis-paste-v1";

export function readPendingAnalysisPasteRecord(): unknown {
  const stored = localStorage.getItem(PENDING_KEY);
  return stored ? JSON.parse(stored) : undefined;
}

export function writePendingAnalysisPasteRecord(record: { operationId: string; body: string }): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(record));
}

export function clearPendingAnalysisPasteRecord(): void {
  localStorage.removeItem(PENDING_KEY);
}
