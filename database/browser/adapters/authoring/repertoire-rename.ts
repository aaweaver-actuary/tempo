const PENDING_KEY = "tempo-pending-repertoire-rename-v1";

export function readPendingRepertoireRenameRecord(): unknown {
  const stored = localStorage.getItem(PENDING_KEY);
  return stored ? JSON.parse(stored) : undefined;
}

export function writePendingRepertoireRenameRecord(record: { operationId: string; repertoireId: string; name: string }): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(record));
}

export function clearPendingRepertoireRenameRecord(): void {
  localStorage.removeItem(PENDING_KEY);
}
