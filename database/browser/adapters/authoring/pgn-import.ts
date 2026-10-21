const PENDING_KEY = "tempo-pending-pgn-import-v1";

export function readPendingPgnImportBytes(): string | null {
  return localStorage.getItem(PENDING_KEY);
}

export function decodePendingPgnImportRecord(stored: string): unknown {
  return JSON.parse(stored);
}

export function writePendingPgnImportRecord(record: { operationId: string; fingerprint: string }): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(record));
}

export function clearPendingPgnImportRecord(): void {
  localStorage.removeItem(PENDING_KEY);
}
