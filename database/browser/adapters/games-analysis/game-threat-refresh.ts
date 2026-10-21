export function readPendingGameThreatRefresh(storageKey: string): string | null {
  return localStorage.getItem(storageKey);
}

export function storePendingGameThreatRefresh(storageKey: string, operationId: string): void {
  localStorage.setItem(storageKey, operationId);
}

export function clearPendingGameThreatRefresh(storageKey: string): void {
  localStorage.removeItem(storageKey);
}
