export function readPendingFindingCard(storageKey: string): string | null {
  return localStorage.getItem(storageKey);
}

export function storePendingFindingCard(storageKey: string, pending: object): void {
  localStorage.setItem(storageKey, JSON.stringify(pending));
}

export function clearPendingFindingCard(storageKey: string): void {
  localStorage.removeItem(storageKey);
}
