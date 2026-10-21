const PENDING_KEY = "tempo-pending-branch-removal-v1";

export function readPendingBranchRemovalRecord(): unknown {
  const stored = localStorage.getItem(PENDING_KEY);
  return stored ? JSON.parse(stored) : undefined;
}

export function writePendingBranchRemovalRecord(record: { operationId: string; body: string }): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(record));
}

export function clearPendingBranchRemovalRecord(): void {
  localStorage.removeItem(PENDING_KEY);
}
