const PENDING_KEY = "tempo-pending-repertoire-branch-v1";

export function readPendingBranchRecord(): unknown {
  const stored = localStorage.getItem(PENDING_KEY);
  return stored ? JSON.parse(stored) : undefined;
}

export function writePendingBranchRecord(record: { operationId: string; body: string }): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(record));
}

export function clearPendingBranchRecord(): void {
  localStorage.removeItem(PENDING_KEY);
}
