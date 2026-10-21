export function readPendingGameFinding(storageKey: string): string | null {
  return localStorage.getItem(storageKey);
}

export function storePendingGameFinding(storageKey: string, pending: object): void {
  localStorage.setItem(storageKey, JSON.stringify(pending));
}

export function clearPendingGameFinding(storageKey: string): void {
  localStorage.removeItem(storageKey);
}
